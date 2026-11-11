#!/usr/bin/env python3
"""Automated YouTube Data API v3 uploader for GEOMETOR Euclid proposition videos.

Supports:
- One-time OAuth 2.0 authorization & automatic token refresh (--auth)
- Resumable MP4 upload for both 16:9 (1080p) and 9:16 (Shorts)
- Custom thumbnail upload (I_XX_thumb.png and I_XX_short_thumb.png)
- Frame-accurate .srt closed-caption track upload (I_XX.srt)
- In-place metadata/thumbnail updates for already-uploaded videos (--update-meta)
- Persistent tracking of uploaded video IDs and URLs inside each prop's youtube.json
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

_orig_getaddrinfo = socket.getaddrinfo
_GOOGLE_DNS_FALLBACK = {
    "www.googleapis.com": "172.217.117.4",
    "oauth2.googleapis.com": "172.217.116.10",
}


def _resilient_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
  try:
    return _orig_getaddrinfo(host, port, family, type, proto, flags)
  except socket.gaierror:
    if host in _GOOGLE_DNS_FALLBACK:
      return _orig_getaddrinfo(_GOOGLE_DNS_FALLBACK[host], port, family, type, proto, flags)
    raise


socket.getaddrinfo = _resilient_getaddrinfo

CONFIG_DIR = Path.home() / ".config" / "geometor"
DEFAULT_CLIENT_SECRET = CONFIG_DIR / "youtube_client_secret.json"
DEFAULT_TOKEN_PATH = CONFIG_DIR / "youtube_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

VIDEOS_Root = (Path.cwd() / "videos") if (Path.cwd() / "videos").exists() else Path("/home/phi/PROJECTS/geometor/etc/videos")
QUOTA_LEDGER_PATH = CONFIG_DIR / "quota_ledger.json"


# Official YouTube Data API v3 Quota Unit Costs
QUOTA_COSTS = {
    "channels.list": 1,
    "videos.list": 1,
    "playlists.list": 1,
    "playlistItems.list": 1,
    "playlists.insert": 50,
    "playlists.update": 50,
    "playlistItems.insert": 50,
    "playlistItems.delete": 50,
    "thumbnails.set": 50,
    "videos.update": 50,
    "captions.insert": 400,
    "videos.insert": 1600,
}

_SESSION_QUOTA_EVENTS: list[dict] = []


def _pt_date_str() -> str:
  """Return current date in America/Los_Angeles (YouTube quota reset timezone)."""
  from zoneinfo import ZoneInfo

  return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def _record_quota(endpoint: str, ok: bool = True, detail: str = "") -> None:
  """Record an API call and its official YouTube Data API v3 quota unit cost."""
  units = QUOTA_COSTS.get(endpoint, 1) if ok else 0
  evt = {
      "ts": datetime.now(timezone.utc).isoformat(),
      "pt_date": _pt_date_str(),
      "endpoint": endpoint,
      "units": units,
      "ok": ok,
      "detail": detail,
  }
  _SESSION_QUOTA_EVENTS.append(evt)
  try:
    ledger = (
        json.loads(QUOTA_LEDGER_PATH.read_text(encoding="utf-8"))
        if QUOTA_LEDGER_PATH.exists()
        else {"days": {}}
    )
    pt_day = evt["pt_date"]
    day_entry = ledger.setdefault("days", {}).setdefault(
        pt_day, {"total_units": 0, "by_endpoint": {}, "events": []}
    )
    day_entry["total_units"] = int(day_entry.get("total_units", 0)) + units
    ep_map = day_entry.setdefault("by_endpoint", {})
    ep_stat = ep_map.setdefault(endpoint, {"calls": 0, "failed": 0, "units": 0})
    if ok:
      ep_stat["calls"] += 1
      ep_stat["units"] += units
    else:
      ep_stat["failed"] += 1
    QUOTA_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUOTA_LEDGER_PATH.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
  except Exception:
    pass


def print_quota_report() -> None:
  """Print session and daily PT YouTube Data API v3 quota usage summary."""
  if not QUOTA_LEDGER_PATH.exists():
    return
  try:
    ledger = json.loads(QUOTA_LEDGER_PATH.read_text(encoding="utf-8"))
  except Exception:
    return
  pt_day = _pt_date_str()
  day_entry = ledger.get("days", {}).get(pt_day, {})
  by_ep = day_entry.get("by_endpoint", {})
  total_u = day_entry.get("total_units", 0)
  sess_u = sum(e["units"] for e in _SESSION_QUOTA_EVENTS)
  print("\n======================================================================")
  print(f"YOUTUBE DATA API v3 QUOTA LEDGER (PT Date: {pt_day})")
  print("======================================================================")
  print(f"{'Endpoint':24s} {'Cost/Call':>10s} {'OK Calls':>10s} {'Failed':>8s} {'Total Units':>13s}")
  print(f"{'-'*24} {'-'*10} {'-'*10} {'-'*8} {'-'*13}")
  for ep, st in sorted(by_ep.items()):
    cost = QUOTA_COSTS.get(ep, 1)
    print(
        f"{ep:24s} {cost:10d} {st.get('calls', 0):10d} {st.get('failed', 0):8d} {st.get('units', 0):13,d}"
    )
  print(f"{'-'*24} {'-'*10} {'-'*10} {'-'*8} {'-'*13}")
  print(f"{'Session Units Used':46s} {sess_u:21,d}")
  print(f"{'Today (PT) Tracked Units':46s} {total_u:21,d}")
  print("======================================================================")


def _load_client_config(client_secret_path: Path) -> dict:
  if not client_secret_path.exists():
    raise FileNotFoundError(
        f"OAuth client secret not found at: {client_secret_path}\n"
        "Save your Google Cloud Desktop OAuth client JSON there or pass --client-secret."
    )
  raw = json.loads(client_secret_path.read_text(encoding="utf-8"))
  cfg = raw.get("installed") or raw.get("web")
  if not cfg:
    raise ValueError(
        f"Invalid client_secret JSON in {client_secret_path}: expected 'installed' or 'web' key."
    )
  return cfg


def run_oauth_flow(
    client_secret_path: Path,
    token_path: Path,
    port: int = 8085,
) -> dict:
  """Runs local loopback OAuth 2.0 flow and saves refreshable token."""
  cfg = _load_client_config(client_secret_path)
  client_id = cfg["client_id"]
  client_secret = cfg["client_secret"]
  auth_uri = cfg.get("auth_uri", "https://accounts.google.com/o/oauth2/v2/auth")
  token_uri = cfg.get("token_uri", "https://oauth2.googleapis.com/token")
  redirect_uri = f"http://127.0.0.1:{port}"

  captured: dict[str, str] = {}

  class _CallbackHandler(BaseHTTPRequestHandler):

    def do_GET(self):  # noqa: N802
      parsed = urllib.parse.urlparse(self.path)
      qs = urllib.parse.parse_qs(parsed.query)
      if "code" in qs:
        captured["code"] = qs["code"][0]
        body = (
            "<html><body style='background:#0b0e15;color:#e8b85a;"
            "font-family:monospace;padding:40px;'>"
            "<h2>GEOMETOR YouTube Authorization Complete</h2>"
            "<p style='color:#f4f1ea;'>You can close this tab and return to the terminal.</p>"
            "</body></html>"
        )
        self.send_response(200)
      else:
        captured["error"] = qs.get("error", ["unknown_error"])[0]
        body = f"<html><body>Authorization failed: {captured['error']}</body></html>"
        self.send_response(400)
      self.send_header("Content-Type", "text/html; charset=utf-8")
      self.end_headers()
      self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):  # noqa: A002
      return

  params = {
      "client_id": client_id,
      "redirect_uri": redirect_uri,
      "response_type": "code",
      "scope": " ".join(SCOPES),
      "access_type": "offline",
      "prompt": "consent",
  }
  consent_url = f"{auth_uri}?{urllib.parse.urlencode(params)}"
  print("\n======================================================================")
  print("GEOMETOR YOUTUBE OAUTH 2.0 AUTHORIZATION")
  print("======================================================================")
  print("1. Opening browser to authorize https://www.youtube.com/@geometor ...")
  print("   (When prompted, select your Google account and choose the @geometor channel)")
  print(f"\n   URL: {consent_url}\n")

  server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
  try:
    webbrowser.open(consent_url)
  except Exception:
    pass

  print(f"Waiting for OAuth callback on {redirect_uri} ...")
  server.handle_request()
  server.server_close()

  if "error" in captured or "code" not in captured:
    raise RuntimeError(
        f"OAuth authorization failed: {captured.get('error', 'no code received')}"
    )

  form = urllib.parse.urlencode({
      "code": captured["code"],
      "client_id": client_id,
      "client_secret": client_secret,
      "redirect_uri": redirect_uri,
      "grant_type": "authorization_code",
  }).encode("utf-8")

  req = urllib.request.Request(
      token_uri,
      data=form,
      headers={"Content-Type": "application/x-www-form-urlencoded"},
      method="POST",
  )
  with urllib.request.urlopen(req) as resp:
    token_data = json.loads(resp.read().decode("utf-8"))

  token_data["client_id"] = client_id
  token_data["client_secret"] = client_secret
  token_data["token_uri"] = token_uri
  token_data["expires_at"] = int(time.time()) + int(token_data.get("expires_in", 3600)) - 60

  token_path.parent.mkdir(parents=True, exist_ok=True)
  token_path.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
  os.chmod(token_path, 0o600)
  print(f"Saved OAuth token to: {token_path}")

  channel = get_authenticated_channel(token_data["access_token"])
  if channel:
    snippet = channel.get("snippet", {})
    print(
        f"Connected Channel   : {snippet.get('title')} "
        f"({snippet.get('customUrl', channel.get('id'))})"
    )
  return token_data


def get_valid_access_token(
    client_secret_path: Path = DEFAULT_CLIENT_SECRET,
    token_path: Path = DEFAULT_TOKEN_PATH,
) -> str:
  """Loads token_path and automatically refreshes access_token if expired."""
  if not token_path.exists():
    raise FileNotFoundError(
        f"YouTube OAuth token not found at {token_path}.\n"
        f"Run `python3 {Path(__file__).name} --auth` first."
    )
  token_data = json.loads(token_path.read_text(encoding="utf-8"))
  now = int(time.time())
  if token_data.get("access_token") and now < int(token_data.get("expires_at", 0)) - 540:
    return token_data["access_token"]

  refresh_token = token_data.get("refresh_token")
  if not refresh_token:
    raise RuntimeError(
        f"Token in {token_path} has no refresh_token. Re-run with `--auth`."
    )

  client_id = token_data.get("client_id")
  client_secret = token_data.get("client_secret")
  token_uri = token_data.get("token_uri", "https://oauth2.googleapis.com/token")
  if not (client_id and client_secret):
    cfg = _load_client_config(client_secret_path)
    client_id = cfg["client_id"]
    client_secret = cfg["client_secret"]

  form = urllib.parse.urlencode({
      "client_id": client_id,
      "client_secret": client_secret,
      "refresh_token": refresh_token,
      "grant_type": "refresh_token",
  }).encode("utf-8")

  req = urllib.request.Request(
      token_uri,
      data=form,
      headers={"Content-Type": "application/x-www-form-urlencoded"},
      method="POST",
  )
  with urllib.request.urlopen(req) as resp:
    refreshed = json.loads(resp.read().decode("utf-8"))

  token_data["access_token"] = refreshed["access_token"]
  token_data["expires_at"] = int(time.time()) + int(refreshed.get("expires_in", 3600)) - 600
  if "refresh_token" in refreshed:
    token_data["refresh_token"] = refreshed["refresh_token"]

  token_path.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
  os.chmod(token_path, 0o600)
  return token_data["access_token"]


def get_authenticated_channel(access_token: str) -> dict | None:
  url = "https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics&mine=true"
  req = urllib.request.Request(
      url,
      headers={"Authorization": f"Bearer {access_token}"},
      method="GET",
  )
  try:
    with urllib.request.urlopen(req) as resp:
      data = json.loads(resp.read().decode("utf-8"))
      items = data.get("items", [])
      return items[0] if items else None
  except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8", errors="replace")
    print(f"Warning: could not query channel info ({e.code}): {err_body}")
    return None


def upload_video_resumable(
    access_token: str,
    mp4_path: Path,
    title: str,
    description: str,
    tags: list[str],
    category_id: str = "27",
    privacy_status: str = "unlisted",
    publish_at: str | None = None,
) -> str:
  """Uploads an MP4 via YouTube Data API v3 resumable upload protocol and returns video_id."""
  file_size = mp4_path.stat().st_size
  status_dict: dict[str, object] = {
      "privacyStatus": "private" if publish_at else privacy_status,
      "selfDeclaredMadeForKids": False,
  }
  if publish_at:
    status_dict["publishAt"] = publish_at
  metadata = {
      "snippet": {
          "title": title,
          "description": description,
          "tags": tags,
          "categoryId": str(category_id),
          "defaultLanguage": "en",
          "defaultAudioLanguage": "en",
      },
      "status": status_dict,
  }
  body_bytes = json.dumps(metadata).encode("utf-8")

  init_url = (
      "https://www.googleapis.com/upload/youtube/v3/videos"
      "?uploadType=resumable&part=snippet,status"
  )
  init_req = urllib.request.Request(
      init_url,
      data=body_bytes,
      headers={
          "Authorization": f"Bearer {access_token}",
          "Content-Type": "application/json; charset=UTF-8",
          "X-Upload-Content-Length": str(file_size),
          "X-Upload-Content-Type": "video/mp4",
      },
      method="POST",
  )
  try:
    with urllib.request.urlopen(init_req, timeout=30) as resp:
      upload_url = resp.headers.get("Location")
  except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8", errors="replace")
    raise RuntimeError(f"Failed to initiate video upload ({e.code}): {err_body}") from e

  if not upload_url:
    raise RuntimeError("YouTube resumable upload did not return a Location header.")

  video_bytes = mp4_path.read_bytes()
  chunk_size = 256 * 1024  # 256 KB (minimum Google resumable chunk size, ~75s at 3.2 KB/s)
  offset = 0
  retries = 0
  tmp_chunk = mp4_path.parent / f".tmp_chunk_{mp4_path.stem}.bin"
  tmp_hdr = mp4_path.parent / f".tmp_hdr_{mp4_path.stem}.txt"
  try:
    while offset < file_size:
      end = min(offset + chunk_size, file_size) - 1
      chunk = video_bytes[offset : end + 1]
      tmp_chunk.write_bytes(chunk)
      tmp_hdr.unlink(missing_ok=True)
      proc = subprocess.run(
          [
              "curl",
              "-4",
              "--resolve",
              "www.googleapis.com:443:172.217.117.4",
              "--http1.1",
              "--max-time",
              "240",
              "-sS",
              "-D",
              str(tmp_hdr),
              "-X",
              "PUT",
              "-H",
              f"Authorization: Bearer {access_token}",
              "-H",
              "Content-Type: video/mp4",
              "-H",
              f"Content-Range: bytes {offset}-{end}/{file_size}",
              "--data-binary",
              f"@{tmp_chunk}",
              upload_url,
          ],
          capture_output=True,
          text=True,
          check=False,
      )
      hdr_text = tmp_hdr.read_text(encoding="utf-8", errors="replace") if tmp_hdr.exists() else ""
      if proc.returncode == 0 and proc.stdout.strip().startswith("{"):
        result = json.loads(proc.stdout)
        if "id" in result:
          print(f"    [100%] Uploaded {file_size / (1024*1024):.1f} MB -> {result['id']}", flush=True)
          _record_quota("videos.insert", ok=True, detail=f"{result['id']} ({title[:40]})")
          return result["id"]
      if " 308" in hdr_text:
        m_rng = None
        for line in hdr_text.splitlines():
          if line.lower().startswith("range:") and "-" in line:
            m_rng = int(line.split("-")[-1].strip())
        offset = (m_rng + 1) if m_rng is not None else (end + 1)
        pct = int(offset * 100 / file_size)
        print(f"    [{pct:3d}%] {offset / (1024*1024):.2f} / {file_size / (1024*1024):.2f} MB", flush=True)
        retries = 0
        continue
      # Query server for how many bytes were received before timeout
      tmp_hdr.unlink(missing_ok=True)
      chk = subprocess.run(
          [
              "curl",
              "-4",
              "--resolve",
              "www.googleapis.com:443:172.217.117.4",
              "--http1.1",
              "--max-time",
              "30",
              "-sS",
              "-D",
              str(tmp_hdr),
              "-X",
              "PUT",
              "-H",
              f"Authorization: Bearer {access_token}",
              "-H",
              "Content-Length: 0",
              "-H",
              f"Content-Range: bytes */{file_size}",
              upload_url,
          ],
          capture_output=True,
          text=True,
          check=False,
      )
      chk_hdr = tmp_hdr.read_text(encoding="utf-8", errors="replace") if tmp_hdr.exists() else ""
      if chk.returncode == 0 and chk.stdout.strip().startswith("{"):
        result = json.loads(chk.stdout)
        if "id" in result:
          print(f"    [100%] Uploaded {file_size / (1024*1024):.1f} MB -> {result['id']}", flush=True)
          _record_quota("videos.insert", ok=True, detail=f"{result['id']} ({title[:40]})")
          return result["id"]
      if " 308" in chk_hdr:
        for line in chk_hdr.splitlines():
          if line.lower().startswith("range:") and "-" in line:
            new_off = int(line.split("-")[-1].strip()) + 1
            if new_off > offset:
              offset = new_off
              pct = int(offset * 100 / file_size)
              print(f"    [{pct:3d}%] Resumed at {offset / (1024*1024):.2f} / {file_size / (1024*1024):.2f} MB", flush=True)
              retries = 0
              break
      retries += 1
      print(f"    [RETRY {retries}/8] at {offset / (1024*1024):.2f} MB (rc={proc.returncode})", flush=True)
      if retries > 8:
        raise RuntimeError(
            f"curl chunk upload failed after 8 retries at {offset}/{file_size}: {proc.stderr or proc.stdout[:300]}"
        )
      time.sleep(2)
  finally:
    tmp_chunk.unlink(missing_ok=True)
    tmp_hdr.unlink(missing_ok=True)

  raise RuntimeError("Upload loop exited without receiving video_id.")


def update_video_metadata(
    access_token: str,
    video_id: str,
    title: str,
    description: str,
    tags: list[str],
    category_id: str = "27",
    privacy_status: str = "unlisted",
    publish_at: str | None = None,
) -> None:
  """Updates title, description, tags, and privacy status of an existing YouTube video."""
  status_dict: dict[str, object] = {
      "privacyStatus": "private" if publish_at else privacy_status,
      "selfDeclaredMadeForKids": False,
  }
  if publish_at:
    status_dict["publishAt"] = publish_at
  metadata = {
      "id": video_id,
      "snippet": {
          "title": title,
          "description": description,
          "tags": tags,
          "categoryId": str(category_id),
          "defaultLanguage": "en",
          "defaultAudioLanguage": "en",
      },
      "status": status_dict,
  }
  url = "https://www.googleapis.com/youtube/v3/videos?part=snippet,status"
  req = urllib.request.Request(
      url,
      data=json.dumps(metadata).encode("utf-8"),
      headers={
          "Authorization": f"Bearer {access_token}",
          "Content-Type": "application/json; charset=UTF-8",
      },
      method="PUT",
  )
  try:
    with urllib.request.urlopen(req, timeout=30) as resp:
      resp.read()
    _record_quota("videos.update", ok=True, detail=f"{video_id} ({privacy_status})")
  except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8", errors="replace")
    _record_quota("videos.update", ok=False, detail=f"{video_id} HTTP {e.code}")
    raise RuntimeError(
        f"Failed updating metadata for {video_id} ({e.code}): {err_body}"
    ) from e


def upload_thumbnail(
    access_token: str,
    video_id: str,
    png_path: Path,
) -> bool:
  """Uploads a custom thumbnail for video_id (compressed to JPEG so it transfers in ~20s)."""
  if not png_path or not png_path.exists():
    return False
  jpg_path = png_path.with_suffix(".jpg")
  content_type = "image/png"
  upload_path = png_path
  try:
    subprocess.run(
        ["/usr/bin/ffmpeg", "-y", "-i", str(png_path), "-q:v", "4", str(jpg_path)],
        check=True,
        capture_output=True,
    )
    if jpg_path.exists():
      upload_path = jpg_path
      content_type = "image/jpeg"
  except Exception:
    pass
  url = (
      "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
      f"?videoId={urllib.parse.quote(video_id)}&uploadType=media"
  )
  proc = subprocess.run(
      [
          "curl",
          "-4",
          "--resolve",
          "www.googleapis.com:443:172.217.117.4",
          "--max-time",
          "180",
          "-sS",
          "-X",
          "POST",
          "-H",
          f"Authorization: Bearer {access_token}",
          "-H",
          f"Content-Type: {content_type}",
          "--data-binary",
          f"@{upload_path}",
          url,
      ],
      capture_output=True,
      text=True,
      check=False,
  )
  ok = proc.returncode == 0 and '"error"' not in proc.stdout
  _record_quota("thumbnails.set", ok=ok, detail=video_id)
  if not ok:
    err_msg = (proc.stderr or proc.stdout[:500]).strip()
    raise RuntimeError(f"Failed setting thumbnail for {video_id}: {err_msg}")
  return True


def upload_captions_srt(
    access_token: str,
    video_id: str,
    srt_path: Path,
    language: str = "en",
    name: str = "English",
) -> str | None:
  """Uploads an .srt closed-caption track for video_id via multipart/related."""
  if not srt_path or not srt_path.exists():
    return None

  boundary = f"===============geometor_{int(time.time() * 1000)}=="
  snippet_json = json.dumps({
      "snippet": {
          "videoId": video_id,
          "language": language,
          "name": name,
          "isDraft": False,
      }
  }).encode("utf-8")
  srt_bytes = srt_path.read_bytes()

  parts = [
      f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode("utf-8"),
      snippet_json,
      f"\r\n--{boundary}\r\nContent-Type: application/x-subrip; charset=UTF-8\r\n\r\n".encode(
          "utf-8"
      ),
      srt_bytes,
      f"\r\n--{boundary}--\r\n".encode("utf-8"),
  ]
  body = b"".join(parts)

  url = (
      "https://www.googleapis.com/upload/youtube/v3/captions"
      "?uploadType=multipart&part=snippet"
  )
  proc = subprocess.run(
      [
          "curl",
          "-4",
          "--resolve",
          "www.googleapis.com:443:172.217.117.4",
          "--max-time",
          "120",
          "-sS",
          "-X",
          "POST",
          "-H",
          f"Authorization: Bearer {access_token}",
          "-H",
          f'Content-Type: multipart/related; boundary="{boundary}"',
          "--data-binary",
          "@-",
          url,
      ],
      input=body,
      capture_output=True,
      check=False,
  )
  if proc.returncode != 0:
    _record_quota("captions.insert", ok=False, detail=video_id)
    err_msg = proc.stderr.decode("utf-8", errors="replace").strip()
    raise RuntimeError(f"Failed uploading captions for {video_id}: {err_msg}")
  raw_out = proc.stdout.decode("utf-8", errors="replace")
  try:
    data = json.loads(raw_out)
  except Exception as e:
    _record_quota("captions.insert", ok=False, detail=video_id)
    raise RuntimeError(f"Failed parsing captions response for {video_id}: {raw_out[:400]}") from e
  cap_id = data.get("id")
  _record_quota("captions.insert", ok=bool(cap_id), detail=video_id)
  if not cap_id:
    raise RuntimeError(f"Failed uploading captions for {video_id}: {raw_out[:400]}")
  return cap_id


def list_channel_playlists(access_token: str) -> dict[str, str]:
  """Returns {title: playlist_id} for the authenticated channel."""
  url = "https://www.googleapis.com/youtube/v3/playlists?part=snippet&mine=true&maxResults=50"
  req = urllib.request.Request(
      url,
      headers={"Authorization": f"Bearer {access_token}"},
      method="GET",
  )
  try:
    with urllib.request.urlopen(req) as resp:
      data = json.loads(resp.read().decode("utf-8"))
      _record_quota("playlists.list", ok=True, detail="mine=true")
      return {
          item["snippet"]["title"]: item["id"]
          for item in data.get("items", [])
          if "snippet" in item and "id" in item
      }
  except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8", errors="replace")
    _record_quota("playlists.list", ok=False, detail=f"HTTP {e.code}")
    print(f"  [!] Could not list playlists ({e.code}): {err_body}")
    return {}


def create_playlist(
    access_token: str,
    title: str,
    description: str,
    privacy_status: str = "unlisted",
) -> str | None:
  """Creates a new YouTube playlist and returns its playlist_id."""
  payload = {
      "snippet": {
          "title": title,
          "description": description,
          "defaultLanguage": "en",
      },
      "status": {
          "privacyStatus": privacy_status,
      },
  }
  url = "https://www.googleapis.com/youtube/v3/playlists?part=snippet,status"
  req = urllib.request.Request(
      url,
      data=json.dumps(payload).encode("utf-8"),
      headers={
          "Authorization": f"Bearer {access_token}",
          "Content-Type": "application/json; charset=UTF-8",
      },
      method="POST",
  )
  try:
    with urllib.request.urlopen(req) as resp:
      data = json.loads(resp.read().decode("utf-8"))
      pl_id = data.get("id")
      _record_quota("playlists.insert", ok=bool(pl_id), detail=title)
      return pl_id
  except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8", errors="replace")
    _record_quota("playlists.insert", ok=False, detail=f"{title} HTTP {e.code}")
    print(f"  [!] Playlist creation warning for '{title}' ({e.code}): {err_body}")
    return None


def ensure_book_playlists(
    access_token: str,
    book_rom: str = "c-00001-00019",
    privacy_status: str = "unlisted",
) -> tuple[str | None, str | None]:
  """Ensures separate 16:9 and 9:16 Shorts playlists exist for ETC collection <book_rom>."""
  reg_path = VIDEOS_Root / "playlists.json"
  reg: dict = {}
  if reg_path.exists():
    try:
      reg = json.loads(reg_path.read_text(encoding="utf-8"))
    except Exception:
      reg = {}

  book_key = book_rom.lower()
  book_entry = reg.setdefault(book_key, {})

  is_euclid = book_key in ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii") or (VIDEOS_Root / f"book_{book_key}").exists()
  if is_euclid:
    rom_up = book_rom.upper()
    title_1080p = f"Euclid's Elements • Book {rom_up}"
    title_short = f"Euclid's Elements • Book {rom_up} • Shorts"
  else:
    if book_key in ("c-00001-00019", "1", "x_1-x_19"):
      range_label = "X_1–X_19"
    else:
      range_label = book_rom
    title_1080p = f"Encyclopedia of Triangle Centers • {range_label}"
    title_short = f"Encyclopedia of Triangle Centers • {range_label} • Shorts"

  desc_1080p = (
      f"Constructive geometry, trilinear & barycentric coordinates, central lines, and functional relations "
      f"for {range_label} in Clark Kimberling's Encyclopedia of Triangle Centers (ETC)."
  )
  desc_short = (
      f"Vertical 9:16 Shorts exploring {range_label} in Clark Kimberling's Encyclopedia of Triangle Centers (ETC)."
  )

  pl_1080p = book_entry.get("wide_1080p", {}).get("id")
  pl_short = book_entry.get("short", {}).get("id")

  if not pl_1080p or not pl_short:
    existing_map = list_channel_playlists(access_token)
    if not pl_1080p:
      pl_1080p = existing_map.get(title_1080p)
    if not pl_short:
      pl_short = existing_map.get(title_short)

  created_1080p = False
  created_short = False
  if not pl_1080p:
    print(f"Creating 16:9 Playlist : {title_1080p} ...")
    pl_1080p = create_playlist(access_token, title_1080p, desc_1080p, privacy_status)
    if pl_1080p:
      created_1080p = True
      print(f"  → Created: https://www.youtube.com/playlist?list={pl_1080p}")

  if not pl_short:
    print(f"Creating 9:16 Playlist : {title_short} ...")
    pl_short = create_playlist(access_token, title_short, desc_short, privacy_status)
    if pl_short:
      created_short = True
      print(f"  → Created: https://www.youtube.com/playlist?list={pl_short}")

  if pl_1080p:
    prev_pv = book_entry.get("wide_1080p", {}).get("privacyStatus", "unlisted")
    book_entry["wide_1080p"] = {
        "id": pl_1080p,
        "title": title_1080p,
        "url": f"https://www.youtube.com/playlist?list={pl_1080p}",
        "privacyStatus": privacy_status if created_1080p else prev_pv,
    }
  if pl_short:
    prev_pv = book_entry.get("short", {}).get("privacyStatus", "unlisted")
    book_entry["short"] = {
        "id": pl_short,
        "title": title_short,
        "url": f"https://www.youtube.com/playlist?list={pl_short}",
        "privacyStatus": privacy_status if created_short else prev_pv,
    }

  reg_path.parent.mkdir(parents=True, exist_ok=True)
  reg_path.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")
  return pl_1080p, pl_short


def find_playlist_item(
    access_token: str,
    playlist_id: str,
    video_id: str,
) -> tuple[str | None, int | None]:
  """Finds (playlist_item_id, position) of video_id inside playlist_id."""
  url = (
      "https://www.googleapis.com/youtube/v3/playlistItems"
      f"?part=snippet&playlistId={urllib.parse.quote(playlist_id)}"
      f"&videoId={urllib.parse.quote(video_id)}&maxResults=5"
  )
  req = urllib.request.Request(
      url,
      headers={"Authorization": f"Bearer {access_token}"},
      method="GET",
  )
  try:
    with urllib.request.urlopen(req) as resp:
      data = json.loads(resp.read().decode("utf-8"))
      _record_quota("playlistItems.list", ok=True, detail=video_id)
      items = data.get("items", [])
      if items:
        item = items[0]
        return item.get("id"), item.get("snippet", {}).get("position")
  except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8", errors="replace")
    _record_quota("playlistItems.list", ok=False, detail=f"{video_id} HTTP {e.code}")
    raise RuntimeError(f"Playlist lookup failed for {video_id} ({e.code}): {err_body}") from e
  return None, None


def delete_playlist_item(
    access_token: str,
    playlist_item_id: str,
) -> bool:
  """Removes a playlistItem by its ID."""
  url = (
      "https://www.googleapis.com/youtube/v3/playlistItems"
      f"?id={urllib.parse.quote(playlist_item_id)}"
  )
  req = urllib.request.Request(
      url,
      headers={"Authorization": f"Bearer {access_token}"},
      method="DELETE",
  )
  try:
    with urllib.request.urlopen(req) as resp:
      resp.read()
    _record_quota("playlistItems.delete", ok=True, detail=playlist_item_id)
    return True
  except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8", errors="replace")
    _record_quota("playlistItems.delete", ok=False, detail=f"{playlist_item_id} HTTP {e.code}")
    if e.code == 404:
      return True
    raise RuntimeError(
        f"Playlist item delete failed for {playlist_item_id} ({e.code}): {err_body}"
    ) from e


def add_video_to_playlist(
    access_token: str,
    playlist_id: str,
    video_id: str,
    position: int | None = None,
) -> str:
  """Inserts video_id into a YouTube playlist (optionally at position) and returns playlist_item_id."""
  snippet: dict = {
      "playlistId": playlist_id,
      "resourceId": {
          "kind": "youtube#video",
          "videoId": video_id,
      },
  }
  if position is not None:
    snippet["position"] = int(position)
  payload = {"snippet": snippet}
  url = "https://www.googleapis.com/youtube/v3/playlistItems?part=snippet"
  req = urllib.request.Request(
      url,
      data=json.dumps(payload).encode("utf-8"),
      headers={
          "Authorization": f"Bearer {access_token}",
          "Content-Type": "application/json; charset=UTF-8",
      },
      method="POST",
  )
  try:
    with urllib.request.urlopen(req) as resp:
      data = json.loads(resp.read().decode("utf-8"))
      item_id = data.get("id")
      _record_quota("playlistItems.insert", ok=bool(item_id), detail=f"{video_id} -> {playlist_id}")
      if not item_id:
        raise RuntimeError(f"Playlist insert returned no item ID for {video_id}")
      return item_id
  except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8", errors="replace")
    _record_quota("playlistItems.insert", ok=False, detail=f"{video_id} HTTP {e.code}")
    raise RuntimeError(f"Playlist insert failed for {video_id} ({e.code}): {err_body}") from e


def _effective_privacy(
    meta: dict,
    key: str,
    privacy_override: str | None = None,
    publish_at: str | None = None,
) -> str:
  """Determine effective privacyStatus, inheriting 'public' if current or any historical version was published."""
  if publish_at:
    return "private"
  if privacy_override:
    return privacy_override
  if meta.get("privacyStatus") == "public":
    return "public"
  existing = meta.get("uploads", {}).get(key, {})
  if existing.get("privacyStatus") == "public" or existing.get("published_at"):
    return "public"
  for h_entry in meta.get("history", {}).get(key, []):
    if isinstance(h_entry, dict) and (
        h_entry.get("privacyStatus") == "public" or h_entry.get("published_at")
    ):
      return "public"
  return meta.get("privacyStatus", "unlisted")


def _sync_playlist_and_history_for_variant(
    access_token: str,
    playlist_id: str | None,
    current_vid: str,
    current_ver: int,
    current_upload: dict,
    history_list: list[dict],
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
) -> None:
  """Ensure current_vid replaces any superseded history version at its exact playlist position, and unlist all superseded versions."""
  if playlist_id:
    old_items_to_remove: list[tuple[dict, str, int | None]] = []
    target_pos: int | None = None
    for h_entry in history_list:
      h_vid = h_entry.get("video_id")
      if not h_vid or h_entry.get("playlist_removed"):
        continue
      old_pl_id, old_pos = find_playlist_item(access_token, playlist_id, h_vid)
      if old_pl_id:
        old_items_to_remove.append((h_entry, old_pl_id, old_pos))
        if old_pos is not None and (target_pos is None or old_pos < target_pos):
          target_pos = old_pos
      else:
        h_entry["playlist_removed"] = True

    # If a superseded version was still in the playlist at target_pos AND current_upload was
    # previously appended at the end, remove the mispositioned current item so we re-insert at target_pos.
    if target_pos is not None and current_upload.get("playlist_item_id"):
      cur_pl_id = current_upload["playlist_item_id"]
      print(
          f"  [PLAYLIST] Repositioning {current_vid} from appended slot ({cur_pl_id}) to position {target_pos} ..."
      )
      delete_playlist_item(access_token, cur_pl_id)
      current_upload["playlist_item_id"] = None

    if not current_upload.get("playlist_item_id"):
      pos_label = f" at position {target_pos}" if target_pos is not None else ""
      print(f"  [PLAYLIST] Adding {current_vid} to playlist {playlist_id}{pos_label} ...")
      pl_item_id = add_video_to_playlist(
          access_token, playlist_id, current_vid, position=target_pos
      )
      current_upload["playlist_id"] = playlist_id
      current_upload["playlist_item_id"] = pl_item_id

    if current_upload.get("playlist_item_id"):
      for h_entry, old_pl_id, _ in old_items_to_remove:
        h_ver = h_entry.get("version", "?")
        h_vid = h_entry.get("video_id", "")
        print(
            f"  [PLAYLIST] Removing superseded v{h_ver} ({h_vid}) from playlist ..."
        )
        if delete_playlist_item(access_token, old_pl_id):
          h_entry["playlist_removed"] = True

  for h_entry in history_list:
    h_vid = h_entry.get("video_id")
    h_ver = h_entry.get("version", 1)
    if not h_vid or h_entry.get("unlisted_confirmed"):
      continue
    old_title = f"{title} [v{h_ver}]"
    if len(old_title) > 100:
      old_title = f"{title[:93]} [v{h_ver}]"
    old_desc = f"Superseded by v{current_ver}.\n\n{description}"
    print(
        f"  [UNLIST] Unlisting superseded v{h_ver} ({h_vid}) as '{old_title}' ..."
    )
    update_video_metadata(
        access_token=access_token,
        video_id=h_vid,
        title=old_title,
        description=old_desc,
        tags=tags,
        category_id=category_id,
        privacy_status="unlisted",
    )
    h_entry["unlisted_confirmed"] = True


def _is_variant_complete(
    upload_entry: dict,
    history_list: list[dict],
    privacy: str,
    has_thumb_file: bool,
    has_srt_file: bool,
    skip_captions: bool,
    playlist_id: str | None,
) -> bool:
  """Return True only if an uploaded video and all its meta artifacts & history cleanups are 100% complete."""
  if not upload_entry.get("video_id"):
    return False
  if upload_entry.get("privacyStatus") != privacy:
    return False
  if has_thumb_file and not upload_entry.get("thumbnail_set"):
    return False
  if not skip_captions and has_srt_file and not upload_entry.get("caption_id"):
    return False
  if playlist_id and not upload_entry.get("playlist_item_id"):
    return False
  for h in history_list:
    if not h.get("unlisted_confirmed"):
      return False
    if h.get("playlist_item_id") and not h.get("playlist_removed"):
      return False
  return True


def _ensure_variant_complete(
    access_token: str,
    yt_json_path: Path,
    meta: dict,
    variant: dict,
    upload_entry: dict,
    history_list: list[dict],
    description: str,
    tags: list[str],
    category_id: str,
    privacy: str,
    publish_at: str | None,
    srt_path: Path | None,
    skip_captions: bool,
    update_metadata_first: bool = False,
    force_thumb: bool = False,
) -> None:
  """Complete all meta artifacts (metadata/privacy, thumbnail, captions, playlist position, and superseded history cleanup) for a single video."""
  vid = upload_entry["video_id"]
  ver = int(upload_entry.get("version", 1))
  title = variant["title"]
  thumb_path: Path | None = variant["thumb"]
  playlist_id: str | None = variant["playlist"]

  if update_metadata_first or upload_entry.get("privacyStatus") != privacy:
    print(f"  [META] Updating metadata & privacy ({privacy}) for {variant['label']} ({vid}) ...")
    update_video_metadata(
        access_token=access_token,
        video_id=vid,
        title=title,
        description=description,
        tags=tags,
        category_id=category_id,
        privacy_status=privacy,
        publish_at=publish_at,
    )
    upload_entry["privacyStatus"] = privacy
    if publish_at:
      upload_entry["publishAt"] = publish_at
    if privacy == "public" and not upload_entry.get("published_at"):
      upload_entry["published_at"] = datetime.now(timezone.utc).isoformat()
    yt_json_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

  if thumb_path and thumb_path.exists() and (not upload_entry.get("thumbnail_set") or force_thumb):
    print(f"  [THUMB] Setting custom thumbnail: {thumb_path.name} ...")
    upload_thumbnail(access_token, vid, thumb_path)
    upload_entry["thumbnail_set"] = True
    yt_json_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

  if not skip_captions and not upload_entry.get("caption_id") and srt_path and srt_path.exists():
    print(f"  [CC] Uploading closed captions: {srt_path.name} ...")
    upload_entry["caption_id"] = upload_captions_srt(access_token, vid, srt_path)
    yt_json_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

  _sync_playlist_and_history_for_variant(
      access_token=access_token,
      playlist_id=playlist_id,
      current_vid=vid,
      current_ver=ver,
      current_upload=upload_entry,
      history_list=history_list,
      title=title,
      description=description,
      tags=tags,
      category_id=category_id,
  )
  upload_entry["updated_at"] = datetime.now(timezone.utc).isoformat()
  yt_json_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def process_proposition_dir(
    prop_dir: Path,
    access_token: str | None,
    target: str = "both",
    privacy_override: str | None = None,
    publish_at: str | None = None,
    playlist_1080p: str | None = None,
    playlist_short: str | None = None,
    update_meta_only: bool = False,
    update_thumb: bool = False,
    replace_existing: bool = False,
    sync_missing: bool = False,
    skip_captions: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> bool:
  yt_json_path = prop_dir / "youtube.json"
  if not yt_json_path.exists():
    print(f"[SKIP] No youtube.json found in {prop_dir}")
    return True

  meta = json.loads(yt_json_path.read_text(encoding="utf-8"))
  short_code = meta.get("short_code", prop_dir.name)
  files = meta.get("files", {})
  category_id = str(meta.get("categoryId", "27"))
  tags = meta.get("tags", [])
  description = meta.get("description", "")
  srt_path = Path(files["captions_srt"]) if files.get("captions_srt") else None
  has_srt = bool(srt_path and srt_path.exists())
  uploads = meta.setdefault("uploads", {})
  history = meta.setdefault("history", {})

  variants = []
  if target in ("both", "1080p"):
    variants.append({
        "key": "wide_1080p",
        "label": "16:9 1080p",
        "title": meta.get("title_1080p", f"{short_code} • Euclid's Elements"),
        "mp4": Path(files["wide_1080p_mp4"]),
        "thumb": (
            Path(files["thumbnail_16x9_png"])
            if files.get("thumbnail_16x9_png")
            else None
        ),
        "playlist": playlist_1080p,
        "url_fmt": "https://youtu.be/{vid}",
    })
  if target in ("both", "short"):
    variants.append({
        "key": "short",
        "label": "9:16 Short",
        "title": meta.get("title_short", f"{short_code} • Euclid's Elements"),
        "mp4": Path(files["short_mp4"]),
        "thumb": (
            Path(files["thumbnail_9x16_png"])
            if files.get("thumbnail_9x16_png")
            else None
        ),
        "playlist": playlist_short,
        "url_fmt": "https://youtube.com/shorts/{vid}",
    })

  print(f"\n======================================================================")
  print(f"PROPOSITION {short_code} ({meta.get('g_id', '')}) — {prop_dir}")
  print(f"======================================================================")

  for v in variants:
    key = v["key"]
    privacy = _effective_privacy(meta, key, privacy_override, publish_at)
    if privacy == "public":
      meta["privacyStatus"] = "public"
    existing = uploads.get(key, {})
    existing_vid = existing.get("video_id")
    old_version = int(existing.get("version", 1))
    meta_version = int(meta.get("version", 2 if existing_vid else 1))
    new_version = (
        max(meta_version, old_version + 1)
        if (existing_vid and replace_existing)
        else meta_version
    )
    has_thumb = bool(v["thumb"] and v["thumb"].exists())
    prev_list = history.setdefault(key, [])

    # Determine whether we need to upload a new MP4 or ensure the existing upload is 100% complete
    mp4_already_current = False
    if existing_vid:
      if sync_missing or not (force or replace_existing):
        mp4_already_current = True
      elif replace_existing and not force:
        uploaded_at_str = existing.get("uploaded_at")
        if uploaded_at_str and v["mp4"].exists():
          try:
            up_ts = datetime.fromisoformat(uploaded_at_str).timestamp()
            mp4_ts = v["mp4"].stat().st_mtime
            if up_ts >= mp4_ts:
              mp4_already_current = True
          except Exception:
            pass

    if dry_run:
      disp_ver = old_version if (existing_vid and mp4_already_current) else new_version
      print(f"  [DRY-RUN] {v['label']} (v{disp_ver})")
      print(f"    Title     : {v['title']}")
      print(f"    MP4       : {v['mp4'].name} ({v['mp4'].stat().st_size / 1024:.1f} KB)")
      print(f"    Thumbnail : {v['thumb'].name if v['thumb'] else 'None'}")
      print(f"    Captions  : {srt_path.name if (srt_path and not skip_captions) else 'None'}")
      print(f"    Privacy   : {privacy}" + (f" (publishAt: {publish_at})" if publish_at else ""))
      if existing_vid:
        complete_flag = _is_variant_complete(
            existing, prev_list, privacy, has_thumb, has_srt, skip_captions, v["playlist"] or existing.get("playlist_id")
        )
        action = (
            f"REPLACE & UNLIST [v{old_version}]"
            if not mp4_already_current
            else ("COMPLETE" if complete_flag else "INCOMPLETE (NEEDS META SYNC)")
        )
        print(f"    {action:9s} : {existing_vid} ({existing.get('url')})")
      continue

    if not access_token:
      access_token = get_valid_access_token()
    assert access_token is not None

    if update_meta_only:
      if not existing_vid:
        print(f"  [SKIP] {v['label']} has not been uploaded yet; cannot --update-meta.")
        continue
      try:
        _ensure_variant_complete(
            access_token=access_token,
            yt_json_path=yt_json_path,
            meta=meta,
            variant=v,
            upload_entry=existing,
            history_list=prev_list,
            description=description,
            tags=tags,
            category_id=category_id,
            privacy=privacy,
            publish_at=publish_at,
            srt_path=srt_path,
            skip_captions=skip_captions,
            update_metadata_first=True,
            force_thumb=update_thumb,
        )
        print(f"  [OK] Updated {existing.get('url')}")
      except RuntimeError as e:
        yt_json_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        print(f"  [HALT] Stopped on {short_code} ({v['label']}) so video is completed before moving on: {e}")
        print_quota_report()
        return False
      continue

    if mp4_already_current:
      is_complete = _is_variant_complete(
          existing, prev_list, privacy, has_thumb, has_srt, skip_captions, v["playlist"]
      ) and (not sync_missing or bool(existing.get("updated_at")))
      if is_complete and not force:
        print(
            f"  [SKIP] {v['label']} (v{old_version}) is 100% complete: {existing.get('url')}"
        )
        continue
      print(
          f"  [COMPLETE] Finishing incomplete meta artifacts for {v['label']} (v{old_version}, {existing_vid}) before moving on ..."
      )
      try:
        _ensure_variant_complete(
            access_token=access_token,
            yt_json_path=yt_json_path,
            meta=meta,
            variant=v,
            upload_entry=existing,
            history_list=prev_list,
            description=description,
            tags=tags,
            category_id=category_id,
            privacy=privacy,
            publish_at=publish_at,
            srt_path=srt_path,
            skip_captions=skip_captions,
            update_metadata_first=not bool(existing.get("updated_at")),
            force_thumb=force,
        )
        print(f"  [OK] Completed {existing.get('url')}")
      except RuntimeError as e:
        yt_json_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        print(
            f"  [HALT] Could not finish meta artifacts for {short_code} ({v['label']}). "
            f"Halting before moving to next video.\n         Reason: {e}"
        )
        print_quota_report()
        return False
      continue

    if sync_missing and not existing_vid:
      print(f"  [SKIP] {v['label']} has not been uploaded yet; cannot --sync-missing.")
      continue

    print(f"  [UPLOAD] Uploading {v['label']} (v{new_version}): {v['mp4'].name} ...", flush=True)
    try:
      vid = upload_video_resumable(
          access_token=access_token,
          mp4_path=v["mp4"],
          title=v["title"],
          description=description,
          tags=tags,
          category_id=category_id,
          privacy_status=privacy,
          publish_at=publish_at,
      )
    except RuntimeError as e:
      if "uploadLimitExceeded" in str(e) or "defaultVideoInsertPerDayPerProject" in str(e):
        print(
            f"  [LIMIT] YouTube daily video_insert limit (100/day) reached for @geometor while uploading {short_code} ({v['label']}).\n"
            f"          Re-run `python3 scripts/upload_youtube.py --centers 1..19 --replace` "
            "once the midnight PT quota window resets."
        )
        print_quota_report()
        return False
      if "quotaExceeded" in str(e):
        print(
            f"  [QUOTA] Google Cloud YouTube Data API v3 daily quota (10,000 units) reached while uploading {short_code} ({v['label']}).\n"
            "          Quota resets at midnight Pacific Time (00:00 America/Los_Angeles)."
        )
        print_quota_report()
        return False
      raise
    url = v["url_fmt"].format(vid=vid)
    print(f"  [OK] Video ID  : {vid} → {url}")

    now_iso = datetime.now(timezone.utc).isoformat()

    # If replacing an existing take, archive the old record in history first
    if existing_vid and replace_existing:
      archived = dict(existing)
      archived["version"] = old_version
      archived["privacyStatus"] = "unlisted"
      archived["superseded_by"] = vid
      archived["superseded_at"] = now_iso
      prev_list.append(archived)

    meta["version"] = new_version
    uploads[key] = {
        "version": new_version,
        "video_id": vid,
        "url": url,
        "studio_url": f"https://studio.youtube.com/video/{vid}/edit",
        "caption_id": None,
        "playlist_id": v["playlist"],
        "playlist_item_id": None,
        "privacyStatus": privacy,
        **({"publishAt": publish_at} if publish_at else {}),
        **({"published_at": now_iso} if privacy == "public" else {}),
        "uploaded_at": now_iso,
        "thumbnail_set": False,
    }
    yt_json_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    # Immediately complete all meta artifacts (thumbnail, captions, playlist swap, unlisting superseded takes)
    # and HALT if any step fails so we never move to the next video with an incomplete upload.
    if not access_token:
      access_token = get_valid_access_token()
    assert access_token is not None
    try:
      _ensure_variant_complete(
          access_token=access_token,
          yt_json_path=yt_json_path,
          meta=meta,
          variant=v,
          upload_entry=uploads[key],
          history_list=prev_list,
          description=description,
          tags=tags,
          category_id=category_id,
          privacy=privacy,
          publish_at=publish_at,
          srt_path=srt_path,
          skip_captions=skip_captions,
          update_metadata_first=False,
          force_thumb=True,
      )
      print(f"  [OK] Fully completed {v['label']} (v{new_version}): {url}")
    except RuntimeError as e:
      yt_json_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
      print(
          f"  [HALT] Uploaded {vid} for {short_code} ({v['label']}), but failed completing its meta artifacts.\n"
          f"         Halting immediately before moving to the next video.\n"
          f"         Reason: {e}"
      )
      print_quota_report()
      return False

  return True



def get_videos_status(
    access_token: str,
    video_ids: list[str],
) -> dict[str, dict]:
  """Queries status and processingDetails for up to 50 video IDs in a single 1-unit API call."""
  if not video_ids:
    return {}
  out: dict[str, dict] = {}
  for i in range(0, len(video_ids), 50):
    batch = [vid for vid in video_ids[i : i + 50] if vid]
    if not batch:
      continue
    ids_param = urllib.parse.quote(",".join(batch))
    url = (
        "https://www.googleapis.com/youtube/v3/videos"
        f"?part=status,processingDetails,snippet&id={ids_param}"
    )
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    try:
      with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        _record_quota("videos.list", ok=True, detail=f"{len(batch)} videos")
        for item in data.get("items", []):
          vid = item.get("id")
          st = item.get("status", {})
          pr = item.get("processingDetails", {})
          up_status = st.get("uploadStatus", "unknown")
          proc_status = pr.get("processingStatus")
          priv_status = st.get("privacyStatus", "unknown")
          is_ready = up_status == "processed" and proc_status in (
              "succeeded",
              None,
          )
          out[vid] = {
              "upload_status": up_status,
              "processing_status": proc_status or "succeeded"
              if up_status == "processed"
              else (proc_status or "processing"),
              "privacy_status": priv_status,
              "rejection_reason": st.get("rejectionReason"),
              "failure_reason": pr.get("processingFailureReason"),
              "is_ready": is_ready,
              "snippet": item.get("snippet", {}),
          }
    except urllib.error.HTTPError as e:
      err_body = e.read().decode("utf-8", errors="replace")
      _record_quota("videos.list", ok=False, detail=f"HTTP {e.code}")
      if "quotaExceeded" in err_body:
        raise RuntimeError(f"quotaExceeded: {err_body}") from e
      print(f"  [!] Video status query warning ({e.code}): {err_body}")
  return out


def update_playlist_privacy(
    access_token: str,
    playlist_id: str,
    title: str,
    description: str,
    privacy_status: str = "public",
) -> bool:
  """Updates a YouTube playlist's privacyStatus (e.g. from unlisted to public)."""
  payload = {
      "id": playlist_id,
      "snippet": {
          "title": title,
          "description": description,
      },
      "status": {
          "privacyStatus": privacy_status,
      },
  }
  url = "https://www.googleapis.com/youtube/v3/playlists?part=snippet,status"
  req = urllib.request.Request(
      url,
      data=json.dumps(payload).encode("utf-8"),
      headers={
          "Authorization": f"Bearer {access_token}",
          "Content-Type": "application/json; charset=UTF-8",
      },
      method="PUT",
  )
  try:
    with urllib.request.urlopen(req) as resp:
      resp.read()
    _record_quota("playlists.update", ok=True, detail=f"{playlist_id} ({privacy_status})")
    return True
  except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8", errors="replace")
    _record_quota("playlists.update", ok=False, detail=f"{playlist_id} HTTP {e.code}")
    print(f"  [!] Playlist privacy update warning for {playlist_id} ({e.code}): {err_body}")
    return False


def check_and_publish_propositions(
    prop_dirs: list[Path],
    client_secret_path: Path,
    token_path: Path,
    target: str = "both",
    book_rom: str = "c-00001-00019",
    publish: bool = False,
    wait: bool = False,
    poll_interval: int = 60,
    skip_captions: bool = False,
) -> None:
  """Checks processing/check status of uploaded videos and optionally marks them public when ready."""
  reg_path = VIDEOS_Root / "playlists.json"
  reg = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else {}
  book_entry = reg.get(book_rom.lower(), {})
  pl_1080p = book_entry.get("wide_1080p", {}).get("id")
  pl_short = book_entry.get("short", {}).get("id")

  while True:
    access_token = get_valid_access_token(client_secret_path, token_path)
    if publish and (not pl_1080p or not pl_short):
      pl_1080p, pl_short = ensure_book_playlists(
          access_token=access_token,
          book_rom=book_rom,
          privacy_status="public",
      )
      reg = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else {}
      book_entry = reg.get(book_rom.lower(), {})

    entries: list[dict] = []
    video_ids: list[str] = []

    for pdir in prop_dirs:
      yt_json_path = pdir / "youtube.json"
      if not yt_json_path.exists():
        continue
      meta = json.loads(yt_json_path.read_text(encoding="utf-8"))
      short_code = meta.get("short_code", pdir.name)
      uploads = meta.get("uploads", {})
      for key, label, pl_id in (
          ("wide_1080p", "16:9", pl_1080p),
          ("short", "9:16", pl_short),
      ):
        if target == "1080p" and key != "wide_1080p":
          continue
        if target == "short" and key != "short":
          continue
        u = uploads.get(key, {})
        vid = u.get("video_id")
        if vid:
          video_ids.append(vid)
          entries.append({
              "prop_dir": pdir,
              "yt_json_path": yt_json_path,
              "short_code": short_code,
              "key": key,
              "label": label,
              "playlist_id": pl_id or u.get("playlist_id"),
              "video_id": vid,
              "version": u.get("version", 1),
              "url": u.get("url", ""),
              "has_thumb": bool(u.get("thumbnail_set")),
              "has_cc": bool(u.get("caption_id")),
              "has_pl": bool(u.get("playlist_item_id")),
              "local_privacy": u.get("privacyStatus", "unlisted"),
          })

    try:
      status_map = get_videos_status(access_token, video_ids)
    except RuntimeError as e:
      if "quotaExceeded" in str(e):
        if not wait:
          print(
              "\n[QUOTA] Google Cloud YouTube Data API v3 daily quota is currently exhausted.\n"
              "        Quota resets at midnight Pacific Time (00:00 America/Los_Angeles).\n"
              "        Run with `--publish --wait` to automatically wait for reset and publish."
          )
          print_quota_report()
          return
        now_str = datetime.now().strftime("%H:%M:%S")
        print(
            f"[{now_str}] API daily quota exhausted; sleeping 15m until quota reset ..."
        )
        time.sleep(900)
        continue
      raise

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n==========================================================================================")
    print(f"YOUTUBE CHECK & PUBLISH STATUS ({now_str})")
    print(f"==========================================================================================")
    print(f"{'Center':6s} {'Fmt':5s} {'Ver':4s} {'Video ID':12s} {'Upload':10s} {'Checks/HD':11s} {'Privacy':9s} {'TH':4s} {'CC':4s} {'PL':4s} {'URL'}")
    print(f"{'-'*6} {'-'*5} {'-'*4} {'-'*12} {'-'*10} {'-'*11} {'-'*9} {'-'*4} {'-'*4} {'-'*4} {'-'*32}")

    pending_count = 0
    quota_hit = False

    for item in entries:
      vid = item["video_id"]
      st = status_map.get(vid, {})
      up_st = st.get("upload_status", "?")
      pr_st = st.get("processing_status", "?")
      pv_st = st.get("privacy_status", item["local_privacy"])
      is_ready = st.get("is_ready", False)
      th_flag = "yes" if item["has_thumb"] else "no"
      cc_flag = "yes" if item["has_cc"] else "no"
      pl_flag = "yes" if item["has_pl"] else "no"
      print(
          f"{item['short_code']:6s} {item['label']:5s} v{item['version']:<3d} "
          f"{vid:12s} {up_st:10s} {pr_st:11s} {pv_st:9s} {th_flag:4s} {cc_flag:4s} {pl_flag:4s} {item['url']}"
      )

      needs_work = (
          not is_ready
          or pv_st != "public"
          or not item["has_thumb"]
          or not item["has_pl"]
          or (not skip_captions and not item["has_cc"])
      )
      if not publish:
        if not is_ready:
          pending_count += 1
        continue

      if not is_ready:
        pending_count += 1
        continue

      if needs_work and not quota_hit:
        yt_json_path = item["yt_json_path"]
        meta = json.loads(yt_json_path.read_text(encoding="utf-8"))
        files = meta.get("files", {})
        uploads = meta.setdefault("uploads", {})
        history = meta.setdefault("history", {})
        key = item["key"]
        u = uploads.get(key, {})
        title = (
            meta.get("title_1080p")
            if key == "wide_1080p"
            else meta.get("title_short")
        ) or f"{item['short_code']} • Encyclopedia of Triangle Centers"
        description = meta.get("description", "")
        tags = meta.get("tags", [])
        category_id = str(meta.get("categoryId", "27"))
        thumb_key = "thumbnail_16x9_png" if key == "wide_1080p" else "thumbnail_9x16_png"
        thumb_path = Path(files[thumb_key]) if files.get(thumb_key) else None
        srt_path = Path(files["captions_srt"]) if files.get("captions_srt") else None

        # 1. Ensure thumbnail is set if missing
        if not u.get("thumbnail_set") and thumb_path and thumb_path.exists():
          print(f"  [THUMB] Uploading thumbnail for {item['short_code']} {item['label']} ({vid}) ...")
          if upload_thumbnail(access_token, vid, thumb_path):
            u["thumbnail_set"] = True
          else:
            quota_hit = True

        # 2. Ensure .srt captions are uploaded unless --no-captions
        if not skip_captions and not u.get("caption_id") and srt_path and srt_path.exists() and not quota_hit:
          print(f"  [CC] Uploading captions for {item['short_code']} {item['label']} ({vid}) ...")
          cap_id = upload_captions_srt(access_token, vid, srt_path)
          if cap_id:
            u["caption_id"] = cap_id
          else:
            quota_hit = True

        # 3. Ensure playlist swap is complete
        pl_id = item["playlist_id"]
        prev_list = history.get(key, [])
        prev_entry = prev_list[-1] if prev_list else None
        prev_vid = prev_entry.get("video_id") if prev_entry else None
        prev_ver = prev_entry.get("version", item["version"] - 1) if prev_entry else (item["version"] - 1)

        if pl_id and not u.get("playlist_item_id") and not quota_hit:
          old_pl_item_id = None
          old_pos = None
          if prev_vid:
            old_pl_item_id, old_pos = find_playlist_item(access_token, pl_id, prev_vid)
          pos_label = f" at position {old_pos}" if old_pos is not None else ""
          print(f"  [PLAYLIST] Adding {vid} to playlist {pl_id}{pos_label} ...")
          pl_item_id = add_video_to_playlist(access_token, pl_id, vid, position=old_pos)
          if pl_item_id:
            u["playlist_item_id"] = pl_item_id
            u["playlist_id"] = pl_id
            if old_pl_item_id:
              print(f"  [PLAYLIST] Removing superseded v{prev_ver} ({prev_vid}) from playlist ...")
              delete_playlist_item(access_token, old_pl_item_id)
          else:
            quota_hit = True

        # 4. Ensure superseded video is unlisted with [vN] title
        if prev_vid and not quota_hit and not prev_entry.get("unlisted_confirmed"):
          old_title = f"{title} [v{prev_ver}]"
          if len(old_title) > 100:
            old_title = f"{title[:93]} [v{prev_ver}]"
          old_desc = f"Superseded by v{item['version']}.\n\n{description}"
          try:
            update_video_metadata(
                access_token=access_token,
                video_id=prev_vid,
                title=old_title,
                description=old_desc,
                tags=tags,
                category_id=category_id,
                privacy_status="unlisted",
            )
            prev_entry["unlisted_confirmed"] = True
          except Exception as e:
            if "quotaExceeded" in str(e):
              quota_hit = True

        # 5. Flip current video to PUBLIC!
        if pv_st != "public" and not quota_hit:
          print(f"  [PUBLISH] Marking {item['short_code']} {item['label']} ({vid}) → PUBLIC ...")
          try:
            update_video_metadata(
                access_token=access_token,
                video_id=vid,
                title=title,
                description=description,
                tags=tags,
                category_id=category_id,
                privacy_status="public",
            )
            u["privacyStatus"] = "public"
            u["published_at"] = datetime.now(timezone.utc).isoformat()
            print(f"  [OK] Published: {item['url']}")
          except Exception as e:
            print(f"  [!] Failed publishing {vid}: {e}")
            if "quotaExceeded" in str(e):
              quota_hit = True
            pending_count += 1
        elif pv_st == "public":
          u["privacyStatus"] = "public"

        if quota_hit:
          pending_count += 1

        yt_json_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    if publish and not quota_hit and pending_count == 0:
      # Also ensure the ETC playlists themselves are public
      range_label = "X_1–X_19" if book_rom.lower() in ("c-00001-00019", "1", "x_1-x_19") else book_rom
      for sub_key, default_title, default_desc in (
          (
              "wide_1080p",
              f"Encyclopedia of Triangle Centers • {range_label}",
              f"Constructive geometry, trilinear & barycentric coordinates, central lines, and functional relations for {range_label} in Clark Kimberling's Encyclopedia of Triangle Centers (ETC).",
          ),
          (
              "short",
              f"Encyclopedia of Triangle Centers • {range_label} • Shorts",
              f"Vertical 9:16 Shorts exploring {range_label} in Clark Kimberling's Encyclopedia of Triangle Centers (ETC).",
          ),
      ):
        pl_info = book_entry.get(sub_key, {})
        if pl_info.get("id") and pl_info.get("privacyStatus") != "public":
          print(f"  [PLAYLIST] Marking playlist '{pl_info.get('title', default_title)}' → PUBLIC ...")
          if update_playlist_privacy(
              access_token,
              pl_info["id"],
              pl_info.get("title", default_title),
              default_desc,
              "public",
          ):
            pl_info["privacyStatus"] = "public"
            reg_path.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")

    print_quota_report()

    if not wait or pending_count == 0:
      if publish and pending_count == 0:
        print("\n[DONE] All target videos have completed checks and are now PUBLIC!")
      break

    sleep_sec = 900 if quota_hit else poll_interval
    reason = "API quota reset" if quota_hit else f"{pending_count} video(s) still processing/pending"
    print(f"\n[WAIT] {reason}; polling again in {sleep_sec}s ...")
    time.sleep(sleep_sec)


def _resolve_center_dirs(spec: str, book: str = "c-00001-00019") -> list[Path]:
  book_dir = VIDEOS_Root / f"book_{book.lower()}"
  if book_dir.exists():
    dirs: list[Path] = []
    for part in spec.split(","):
      part = part.strip()
      if not part:
        continue
      if ".." in part:
        a, b = part.split("..", 1)
        for n in range(int(a), int(b) + 1):
          dirs.append(book_dir / f"{n:02d}")
      elif "-" in part:
        a, b = part.split("-", 1)
        for n in range(int(a), int(b) + 1):
          dirs.append(book_dir / f"{n:02d}")
      else:
        dirs.append(book_dir / f"{int(part):02d}")
    return dirs

  dirs: list[Path] = []
  for part in spec.split(","):
    part = part.strip()
    if not part:
      continue
    if part.lower() in ("intro", "intro-etc"):
      dirs.append(VIDEOS_Root / "intro-etc")
    elif part.lower() in ("c1", "c_01", "intro-c1", "intro-collection-1"):
      dirs.append(VIDEOS_Root / "intro-collection-1")
    elif ".." in part:
      a, b = part.split("..", 1)
      for n in range(int(a), int(b) + 1):
        dirs.append(VIDEOS_Root / f"x-{n:05d}")
    elif "-" in part:
      a, b = part.split("-", 1)
      for n in range(int(a), int(b) + 1):
        dirs.append(VIDEOS_Root / f"x-{n:05d}")
    else:
      n = int(part.lstrip("xX_"))
      dirs.append(VIDEOS_Root / f"x-{n:05d}")
  return dirs


def _resolve_topic_dirs(spec: str) -> list[Path]:
  topics_root = VIDEOS_Root / "topics"
  dirs: list[Path] = []
  for part in spec.split(","):
    part = part.strip()
    if not part:
      continue
    if ".." in part:
      a, b = part.split("..", 1)
      for n in range(int(a), int(b) + 1):
        matches = sorted(topics_root.glob(f"t-{n:02d}-*"))
        if matches:
          dirs.append(matches[0])
    elif part.isdigit() or part.lower().startswith("t_"):
      n = int(part.lstrip("tT_"))
      matches = sorted(topics_root.glob(f"t-{n:02d}-*"))
      if matches:
        dirs.append(matches[0])
    else:
      matches = sorted(topics_root.glob(f"*{part}*"))
      if matches:
        dirs.append(matches[0])
  return dirs


def main(argv: list[str] | None = None) -> None:
  if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
  parser = argparse.ArgumentParser(
      description="Upload GEOMETOR ETC 16:9 1440p videos and 9:16 Shorts to YouTube."
  )
  parser.add_argument(
      "dirs",
      nargs="*",
      help="Center or topic video folder paths (e.g. resources/videos/x-00001).",
  )
  parser.add_argument(
      "--collection",
      "--book",
      dest="book",
      default="c-00001-00019",
      help="ETC collection slug for playlists (default: c-00001-00019).",
  )
  parser.add_argument(
      "--centers",
      "--props",
      dest="props",
      help="Center numbers or ranges (e.g., '1', '1-19', '1..19', 'intro,1').",
  )
  parser.add_argument(
      "--topics",
      dest="topics",
      help="Topic numbers, ranges, or slugs (e.g., '1..24', '1', 'isogonal-conjugate').",
  )
  parser.add_argument(
      "--auth",
      action="store_true",
      help="Run OAuth 2.0 browser flow to authorize @geometor and save token.",
  )
  parser.add_argument(
      "--whoami",
      action="store_true",
      help="Verify current OAuth token and print the connected YouTube channel.",
  )
  parser.add_argument(
      "--client-secret",
      type=Path,
      default=DEFAULT_CLIENT_SECRET,
      help=f"Path to OAuth 2.0 client_secret.json (default: {DEFAULT_CLIENT_SECRET}).",
  )
  parser.add_argument(
      "--token",
      type=Path,
      default=DEFAULT_TOKEN_PATH,
      help=f"Path to saved OAuth token JSON (default: {DEFAULT_TOKEN_PATH}).",
  )
  parser.add_argument(
      "--target",
      choices=["both", "1080p", "short"],
      default="both",
      help="Which video format(s) to upload (default: both).",
  )
  parser.add_argument(
      "--privacy",
      choices=["unlisted", "private", "public"],
      help="Override privacyStatus from youtube.json.",
  )
  parser.add_argument(
      "--publish-at",
      help="ISO-8601 UTC timestamp for scheduled publishing (e.g. '2026-11-05T16:00:00Z'); automatically sets privacyStatus='private'.",
  )
  parser.add_argument(
      "--schedule-start",
      help="ISO-8601 UTC start timestamp when scheduling a batch of centers at regular intervals.",
  )
  parser.add_argument(
      "--schedule-step-hours",
      type=float,
      default=24.0,
      help="Hours between scheduled center releases when using --schedule-start (default: 24.0).",
  )
  parser.add_argument(
      "--playlist-1080p",
      help="Optional YouTube Playlist ID for 16:9 videos.",
  )
  parser.add_argument(
      "--playlist-short",
      help="Optional YouTube Playlist ID for 9:16 Shorts.",
  )
  parser.add_argument(
      "--no-playlists",
      action="store_true",
      help="Skip automatic playlist creation and assignment.",
  )
  parser.add_argument(
      "--no-captions",
      action="store_true",
      help="Skip .srt caption upload (saves 400 API quota units per video).",
  )
  parser.add_argument(
      "--update-meta",
      action="store_true",
      help="Update title, description, tags, and thumbnail on already-uploaded videos.",
  )
  parser.add_argument(
      "--update-thumb",
      action="store_true",
      help="Also re-upload custom thumbnail when using --update-meta.",
  )
  parser.add_argument(
      "--replace",
      action="store_true",
      help="Upload a new version (vN+1), swap it into the playlist at the same position, unlist the previous take with [vN] suffix, and archive in history.",
  )
  parser.add_argument(
      "--sync-missing",
      action="store_true",
      help="Sync missing thumbnails, captions, playlist items, and superseded unlistings for already-uploaded videos without re-uploading MP4s.",
  )
  parser.add_argument(
      "--status",
      action="store_true",
      help="Query live YouTube processing/check status and privacy for the selected centers.",
  )
  parser.add_argument(
      "--publish",
      action="store_true",
      help="Verify videos have finished YouTube processing & checks, sync any missing items, and mark videos & playlists PUBLIC.",
  )
  parser.add_argument(
      "--wait",
      action="store_true",
      help="Poll continuously until all target videos finish processing & checks (and are published if --publish is set).",
  )
  parser.add_argument(
      "--poll-interval",
      type=int,
      default=60,
      help="Polling interval in seconds when using --wait (default: 60).",
  )
  parser.add_argument(
      "--force",
      action="store_true",
      help="Upload even if youtube.json already records a video_id.",
  )
  parser.add_argument(
      "--dry-run",
      action="store_true",
      help="Inspect youtube.json and print upload plan without calling the YouTube API.",
  )
  parser.add_argument(
      "--quota",
      action="store_true",
      help="Print the YouTube Data API v3 quota ledger for today (Pacific Time) and exit.",
  )
  args = parser.parse_args(argv)

  if args.quota:
    print_quota_report()
    return

  if args.auth:
    run_oauth_flow(args.client_secret, args.token)
    if not args.dirs and not args.props and not args.topics:
      return

  if args.whoami:
    token = get_valid_access_token(args.client_secret, args.token)
    ch = get_authenticated_channel(token)
    if ch:
      s = ch.get("snippet", {})
      st = ch.get("statistics", {})
      print(
          f"Authenticated Channel: {s.get('title')} ({s.get('customUrl', ch.get('id'))}) "
          f"• {st.get('videoCount', '0')} videos"
      )
    return

  prop_dirs: list[Path] = []
  for d in args.dirs:
    p = Path(d)
    if p.exists():
      prop_dirs.append(p.resolve())
    else:
      prop_dirs.extend(_resolve_center_dirs(d, args.book))
  if args.props:
    prop_dirs.extend(_resolve_center_dirs(args.props, args.book))
  if args.topics:
    prop_dirs.extend(_resolve_topic_dirs(args.topics))
    if args.book == "c-00001-00019" and not args.props:
      args.book = "topics"

  if not prop_dirs:
    parser.print_help()
    sys.exit(1)

  if args.status:
    check_and_publish_propositions(
        prop_dirs=prop_dirs,
        client_secret_path=args.client_secret,
        token_path=args.token,
        target=args.target,
        book_rom=args.book,
        publish=args.publish,
        wait=args.wait,
        poll_interval=args.poll_interval,
        skip_captions=args.no_captions,
    )
    return

  access_token = (
      None
      if args.dry_run
      else get_valid_access_token(args.client_secret, args.token)
  )

  pl_1080p = args.playlist_1080p
  pl_short = args.playlist_short
  if (
      not args.dry_run
      and not args.no_playlists
      and not args.update_meta
      and access_token
      and (not pl_1080p or not pl_short)
  ):
    auto_1080p, auto_short = ensure_book_playlists(
        access_token=access_token,
        book_rom=args.book,
        privacy_status=args.privacy or "unlisted",
    )
    pl_1080p = pl_1080p or auto_1080p
    pl_short = pl_short or auto_short

  from datetime import timedelta

  base_sched_dt = None
  if args.schedule_start:
    base_sched_dt = datetime.fromisoformat(args.schedule_start.replace("Z", "+00:00")).astimezone(timezone.utc)

  for idx, pdir in enumerate(prop_dirs):
    item_publish_at = args.publish_at
    if base_sched_dt is not None:
      dt = base_sched_dt + timedelta(hours=idx * args.schedule_step_hours)
      item_publish_at = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    ok = process_proposition_dir(
        prop_dir=pdir,
        access_token=access_token,
        target=args.target,
        privacy_override=args.privacy,
        publish_at=item_publish_at,
        playlist_1080p=pl_1080p,
        playlist_short=pl_short,
        update_meta_only=args.update_meta,
        update_thumb=args.update_thumb,
        replace_existing=args.replace,
        sync_missing=args.sync_missing,
        skip_captions=args.no_captions,
        force=args.force,
        dry_run=args.dry_run,
    )
    if ok is False:
      break

  if args.publish and not args.dry_run:
    check_and_publish_propositions(
        prop_dirs=prop_dirs,
        client_secret_path=args.client_secret,
        token_path=args.token,
        target=args.target,
        book_rom=args.book,
        publish=True,
        wait=args.wait,
        poll_interval=args.poll_interval,
        skip_captions=args.no_captions,
    )


if __name__ == "__main__":
  main()
