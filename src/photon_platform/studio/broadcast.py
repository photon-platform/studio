"""Master YouTube Uploader, Global Pacific-Time Quota Ledger, and Cross-Project Release Scheduler."""

from __future__ import annotations

import fcntl
import json
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .render import ensure_compressed_thumbnail

PT_TZ = ZoneInfo("America/Los_Angeles")
CONFIG_DIR = Path.home() / ".config" / "geometor"
CLIENT_SECRET_PATH = CONFIG_DIR / "youtube_client_secret.json"
GLOBAL_LEDGER_PATH = CONFIG_DIR / "quota_ledger.json"
LEGACY_ETC_LEDGER_PATH = Path("/home/phi/PROJECTS/geometor/etc/resources/videos/quota_ledger.json")
GLOBAL_QUEUE_PATH = CONFIG_DIR / "release_queue.json"

CHANNEL_TOKENS = {
    "@geometor": CONFIG_DIR / "youtube_token.json",
    "@GOR-9-station": CONFIG_DIR / "youtube_token_gor9.json",
}

QUOTA_COSTS = {
    "videos.insert": 1600,
    "captions.insert": 400,
    "thumbnails.set": 50,
    "playlistItems.insert": 50,
    "playlistItems.delete": 50,
    "videos.update": 50,
    "playlists.insert": 50,
    "channels.list": 1,
    "playlists.list": 1,
    "playlistItems.list": 1,
    "videos.list": 1,
}

DAILY_QUOTA_LIMIT = 10000
DEFAULT_HERO_RESERVE = 1600  # Keep 1,600 units in reserve unless overridden

PROJECT_ROOTS = {
    "elements": Path("/home/phi/PROJECTS/geometor/elements"),
    "etc": Path("/home/phi/PROJECTS/geometor/etc"),
    "phyllotaxis": Path("/home/phi/PROJECTS/geometor/phyllotaxis"),
    "GOR-9": Path("/home/phi/PROJECTS/phiarchitect/GOR-9"),
}


def now_pt() -> datetime:
    return datetime.now(PT_TZ)


def today_pt_str() -> str:
    return now_pt().strftime("%Y-%m-%d")


def time_until_pt_reset() -> dict[str, Any]:
    now = now_pt()
    tomorrow_midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    delta = tomorrow_midnight - now
    total_sec = int(delta.total_seconds())
    hours, rem = divmod(total_sec, 3600)
    minutes, _ = divmod(rem, 60)
    return {
        "pt_date": now.strftime("%Y-%m-%d"),
        "pt_now_iso": now.isoformat(),
        "next_reset_pt_iso": tomorrow_midnight.isoformat(),
        "seconds_until_reset": total_sec,
        "human_countdown": f"{hours}h {minutes}m",
    }


def _load_or_migrate_ledger() -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not GLOBAL_LEDGER_PATH.exists() and LEGACY_ETC_LEDGER_PATH.exists():
        shutil.copy2(LEGACY_ETC_LEDGER_PATH, GLOBAL_LEDGER_PATH)

    if GLOBAL_LEDGER_PATH.exists():
        try:
            return json.loads(GLOBAL_LEDGER_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "timezone": "America/Los_Angeles",
        "daily_limit": DAILY_QUOTA_LIMIT,
        "days": {},
    }


def record_quota_call(
    endpoint: str,
    project: str,
    channel: str,
    package_id: str,
    status: str = "ok",
    cost_override: int | None = None,
) -> dict[str, Any]:
    """Thread/process-safe quota logger across all projects and channels."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = CONFIG_DIR / "quota_ledger.lock"
    cost = cost_override if cost_override is not None else QUOTA_COSTS.get(endpoint, 1)
    pt_day = today_pt_str()

    with open(lock_path, "w", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            ledger = _load_or_migrate_ledger()
            days = ledger.setdefault("days", {})
            day_entry = days.setdefault(
                pt_day,
                {
                    "used_units": 0,
                    "calls_count": 0,
                    "by_endpoint": {},
                    "by_project": {},
                    "events": [],
                },
            )
            if "total_units" in day_entry and "used_units" not in day_entry:
                day_entry["used_units"] = day_entry.get("total_units", 0)

            day_entry["used_units"] = int(day_entry.get("used_units", 0)) + cost
            day_entry["calls_count"] = int(day_entry.get("calls_count", 0)) + 1
            by_ep = day_entry.setdefault("by_endpoint", {})
            ep_val = by_ep.get(endpoint)
            if isinstance(ep_val, dict):
                ep_val["calls"] = int(ep_val.get("calls", 0)) + (1 if status == "ok" else 0)
                ep_val["failed"] = int(ep_val.get("failed", 0)) + (0 if status == "ok" else 1)
                ep_val["units"] = int(ep_val.get("units", 0)) + cost
            else:
                by_ep[endpoint] = {
                    "calls": 1 if status == "ok" else 0,
                    "failed": 0 if status == "ok" else 1,
                    "units": int(ep_val or 0) + cost,
                }
            by_proj = day_entry.setdefault("by_project", {})
            by_proj[project] = int(by_proj.get(project, 0)) + cost

            day_entry.setdefault("events", []).append(
                {
                    "ts_utc": datetime.now(timezone.utc).isoformat(),
                    "endpoint": endpoint,
                    "cost": cost,
                    "project": project,
                    "channel": channel,
                    "package": package_id,
                    "status": status,
                }
            )
            GLOBAL_LEDGER_PATH.write_text(
                json.dumps(ledger, indent=2) + "\n", encoding="utf-8"
            )
            return day_entry
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def get_quota_status(
    daily_limit: int = DAILY_QUOTA_LIMIT,
    hero_reserve: int = DEFAULT_HERO_RESERVE,
) -> dict[str, Any]:
    """Return today's Pacific-Time quota status across all channels and projects."""
    ledger = _load_or_migrate_ledger()
    reset_info = time_until_pt_reset()
    pt_day = reset_info["pt_date"]
    day_entry = ledger.get("days", {}).get(pt_day, {})
    used = int(day_entry.get("used_units", day_entry.get("total_units", 0)))
    remaining = max(0, daily_limit - used)
    usable_after_reserve = max(0, remaining - hero_reserve)

    return {
        "ledger_path": str(GLOBAL_LEDGER_PATH),
        "pt_date": pt_day,
        "daily_limit": daily_limit,
        "used_units": used,
        "remaining_units": remaining,
        "hero_reserve_units": hero_reserve,
        "usable_batch_units": usable_after_reserve,
        "safe_dual_packages_today": remaining // 3400,
        "safe_single_videos_today": remaining // 1700,
        "safe_repairs_today": remaining // 50,
        "reset": reset_info,
        "by_project_today": day_entry.get("by_project", {}),
        "by_endpoint_today": day_entry.get("by_endpoint", {}),
    }


def _resolve_channel_token_path(channel: str) -> tuple[str, Path]:
    from .uploader import normalize_channel_handle

    norm = normalize_channel_handle(channel)
    token_path = CHANNEL_TOKENS.get(norm)
    if token_path is None:
        token_path = CONFIG_DIR / ("youtube_token_gor9.json" if norm.lower() == "@gor-9-station" else "youtube_token.json")
    return norm, token_path


def get_access_token(channel: str = "@geometor", verify_handle: bool = True) -> str:
    """Load, auto-refresh, and optionally verify the OAuth 2.0 access token for `channel`."""
    from .uploader import get_valid_access_token, verify_authenticated_channel_handle

    norm_channel, token_path = _resolve_channel_token_path(channel)
    access_token = get_valid_access_token(CLIENT_SECRET_PATH, token_path)
    if verify_handle:
        verify_authenticated_channel_handle(access_token, norm_channel)
    return access_token


def authorize_channel(channel: str = "@geometor", port: int = 8085) -> dict[str, Any]:
    """Run resilient loopback OAuth 2.0 flow for `@geometor` or `@GOR-9-station`."""
    from .uploader import run_oauth_flow

    norm_channel, token_path = _resolve_channel_token_path(channel)
    token_data = run_oauth_flow(
        client_secret_path=CLIENT_SECRET_PATH,
        token_path=token_path,
        port=port,
        expected_channel=norm_channel,
    )
    return {
        "status": "ok",
        "channel": norm_channel,
        "token_path": str(token_path),
        "expires_at": token_data.get("expires_at"),
    }


def upload_thumbnail_curl(
    video_id: str,
    thumb_path: Path,
    channel: str,
    project: str,
    package_id: str,
) -> dict[str, Any]:
    """Compress thumbnail to < 2 MB JPEG and upload via IPv4 curl to YouTube Data API v3."""
    quota = get_quota_status(hero_reserve=0)
    if quota["remaining_units"] < QUOTA_COSTS["thumbnails.set"]:
        return {
            "status": "quota_blocked",
            "error": f"Insufficient quota ({quota['remaining_units']} < 50) for thumbnails.set",
        }

    jpg_path = ensure_compressed_thumbnail(thumb_path)
    access_token = get_access_token(channel=channel)
    url = f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={video_id}&uploadType=media"

    cmd = [
        "curl",
        "-sS",
        "-4",
        "--resolve",
        "www.googleapis.com:443:172.217.117.4",
        "-X",
        "POST",
        url,
        "-H",
        f"Authorization: Bearer {access_token}",
        "-H",
        "Content-Type: image/jpeg",
        "--data-binary",
        f"@{jpg_path}",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    ok = res.returncode == 0 and '"items"' in res.stdout
    record_quota_call(
        endpoint="thumbnails.set",
        project=project,
        channel=channel,
        package_id=package_id,
        status="ok" if ok else "error",
    )
    return {
        "status": "ok" if ok else "error",
        "video_id": video_id,
        "jpg_path": str(jpg_path),
        "jpg_size_kb": round(jpg_path.stat().st_size / 1024, 1),
        "response": res.stdout[:400],
        "stderr": res.stderr.strip(),
    }


def inspect_package(pkg_dir: Path, project: str) -> dict[str, Any]:
    """Inspect a package directory and return its upload/schedule/repair state."""
    pkg_dir = Path(pkg_dir).resolve()
    yt_json = pkg_dir / "youtube.json"
    if not yt_json.exists() and (pkg_dir / "youtube" / "youtube.json").exists():
        yt_json = pkg_dir / "youtube" / "youtube.json"

    data: dict[str, Any] = {}
    if yt_json.exists():
        try:
            data = json.loads(yt_json.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    default_channel = "@GOR-9-station" if project == "GOR-9" or "GOR-9" in pkg_dir.parts else "@geometor"
    channel = data.get("channel") or data.get("channel_handle") or default_channel

    # Legacy flat single-track schema (`video_id` at top level)
    if "video_id" in data and "uploads" not in data and "wide_1080p" not in data:
        vid = data.get("video_id")
        thumb_ok = bool(data.get("thumbnail_uploaded") or data.get("thumbnail_set"))
        priv = data.get("privacy_status") or data.get("privacyStatus") or "private"
        pub_at = data.get("publish_at") or data.get("publishAt")
        state = "pending_upload"
        if vid:
            state = "incomplete_repair_needed" if not thumb_ok else f"complete_{priv}"
        return {
            "project": project,
            "package_id": pkg_dir.name,
            "package_dir": str(pkg_dir),
            "youtube_json": str(yt_json),
            "channel": channel,
            "mode": "single_wide",
            "state": state,
            "publish_at": pub_at,
            "wide_video_id": vid,
            "short_video_id": None,
            "needs_repair": bool(vid and not thumb_ok),
        }

    uploads = data.get("uploads", data)
    files_cfg = data.get("files") or {}
    wide = uploads.get("wide_1080p", {}) if isinstance(uploads, dict) else {}
    short = uploads.get("short", {}) if isinstance(uploads, dict) else {}
    w_id = wide.get("video_id")
    s_id = short.get("video_id")
    w_thumb = bool(wide.get("thumbnail_set") or wide.get("thumbnail_uploaded"))
    s_thumb = bool(short.get("thumbnail_set") or short.get("thumbnail_uploaded"))
    w_pl = bool(wide.get("playlist_item_id"))
    s_pl = bool(short.get("playlist_item_id"))
    pub_at = wide.get("publishAt") or short.get("publishAt") or data.get("publishAt")
    priv = wide.get("privacyStatus") or data.get("privacyStatus") or "unscheduled"

    has_wide_mp4 = bool(
        any(pkg_dir.glob("*_1080p.mp4"))
        or any(pkg_dir.glob("gor9_*.mp4"))
        or (files_cfg.get("wide_1080p_mp4") and Path(files_cfg["wide_1080p_mp4"]).exists())
    )
    has_short_mp4 = bool(
        any(pkg_dir.glob("*_short.mp4"))
        or (files_cfg.get("short_mp4") and Path(files_cfg["short_mp4"]).exists())
    )

    # Single-track 16:9 package (e.g., GOR-9 videos/mk4_demo, drone_loop, satellite_orbit)
    is_single_wide = (
        not s_id
        and "short_mp4" not in files_cfg
        and not has_short_mp4
        and (project == "GOR-9" or "GOR-9" in pkg_dir.parts or bool(w_id) or has_wide_mp4)
    )
    if is_single_wide:
        require_pl = bool(wide.get("playlist_id"))
        needs_repair = bool(w_id and (not w_thumb or (require_pl and not w_pl)))
        if w_id and not needs_repair:
            state = "complete_scheduled" if (priv == "private" and pub_at) else f"complete_{priv}"
        elif needs_repair:
            state = "incomplete_repair_needed"
        elif has_wide_mp4:
            state = "pending_upload"
        else:
            state = "unrendered"
        return {
            "project": project,
            "package_id": data.get("short_code") or data.get("prop_id") or pkg_dir.name,
            "package_dir": str(pkg_dir),
            "youtube_json": str(yt_json) if yt_json.exists() else None,
            "channel": channel,
            "mode": "single_wide",
            "state": state,
            "publish_at": pub_at,
            "wide_video_id": w_id,
            "short_video_id": None,
            "needs_repair": needs_repair,
        }

    has_mp4 = has_wide_mp4 and has_short_mp4
    needs_repair = False
    if (w_id and (not w_thumb or not w_pl)) or (s_id and (not s_thumb or not s_pl)) or (bool(w_id) != bool(s_id)):
        needs_repair = True

    if w_id and s_id and not needs_repair:
        state = "complete_scheduled" if (priv == "private" and pub_at) else f"complete_{priv}"
    elif needs_repair:
        state = "incomplete_repair_needed"
    elif has_mp4:
        state = "pending_upload"
    else:
        state = "unrendered"

    return {
        "project": project,
        "package_id": data.get("short_code") or data.get("prop_id") or pkg_dir.name,
        "package_dir": str(pkg_dir),
        "youtube_json": str(yt_json) if yt_json.exists() else None,
        "channel": channel,
        "mode": "dual",
        "state": state,
        "publish_at": pub_at,
        "wide_video_id": w_id,
        "short_video_id": s_id,
        "needs_repair": needs_repair,
    }


def discover_all_packages(project_filter: str | None = None) -> dict[str, Any]:
    """Scan elements, etc, phyllotaxis, and GOR-9 and return a unified status summary."""
    packages: list[dict[str, Any]] = []

    if project_filter in (None, "elements"):
        elem_vids = PROJECT_ROOTS["elements"] / "videos"
        if elem_vids.exists():
            for book_dir in sorted(elem_vids.glob("book_*")):
                for prop_dir in sorted(book_dir.iterdir()):
                    if prop_dir.is_dir():
                        packages.append(inspect_package(prop_dir, "elements"))

    if project_filter in (None, "etc"):
        etc_vids = PROJECT_ROOTS["etc"] / "videos"
        if etc_vids.exists():
            for item in sorted(etc_vids.iterdir()):
                if item.is_dir() and item.name != "topics":
                    packages.append(inspect_package(item, "etc"))
            topics_dir = etc_vids / "topics"
            if topics_dir.exists():
                for tdir in sorted(topics_dir.iterdir()):
                    if tdir.is_dir():
                        packages.append(inspect_package(tdir, "etc"))

    if project_filter in (None, "phyllotaxis"):
        phyl_vids = PROJECT_ROOTS["phyllotaxis"] / "videos"
        if phyl_vids.exists():
            for pdir in sorted(phyl_vids.iterdir()):
                if pdir.is_dir() and any((pdir / fn).exists() for fn in ("youtube.json", "motion.json", "script.scr")):
                    packages.append(inspect_package(pdir, "phyllotaxis"))

    if project_filter in (None, "GOR-9"):
        gor_eps = PROJECT_ROOTS["GOR-9"] / "episodes"
        if gor_eps.exists():
            for ep_dir in sorted(gor_eps.iterdir()):
                if ep_dir.is_dir() and ((ep_dir / "youtube" / "youtube.json").exists() or (ep_dir / "youtube.json").exists()):
                    packages.append(inspect_package(ep_dir, "GOR-9"))
        gor_vids = PROJECT_ROOTS["GOR-9"] / "videos"
        if gor_vids.exists():
            for vdir in sorted(gor_vids.iterdir()):
                if vdir.is_dir() and any((vdir / fn).exists() for fn in ("youtube.json", "motion.json", "camera_path.json")):
                    packages.append(inspect_package(vdir, "GOR-9"))

    by_state: dict[str, int] = {}
    by_project: dict[str, dict[str, int]] = {}
    repairs_needed: list[dict[str, Any]] = []

    for pkg in packages:
        st = pkg["state"]
        pr = pkg["project"]
        by_state[st] = by_state.get(st, 0) + 1
        proj_map = by_project.setdefault(pr, {})
        proj_map[st] = proj_map.get(st, 0) + 1
        if pkg["needs_repair"]:
            repairs_needed.append(pkg)

    return {
        "total_packages": len(packages),
        "by_state": by_state,
        "by_project": by_project,
        "repairs_needed": repairs_needed,
        "packages": packages,
    }


def repair_package(package_dir: Path) -> dict[str, Any]:
    """Repair missing thumbnails and playlist items on an already-uploaded package."""
    package_dir = Path(package_dir).resolve()
    gor_yt = package_dir / "youtube" / "youtube.json"
    if gor_yt.exists():
        data = json.loads(gor_yt.read_text(encoding="utf-8"))
        vid = data.get("video_id")
        if vid and not data.get("thumbnail_uploaded"):
            thumb_candidates = (
                sorted((package_dir / "youtube").glob("*thumb*.*"))
                + sorted(package_dir.glob("*thumb*.*"))
            )
            if not thumb_candidates:
                return {"status": "error", "error": "No thumbnail file found"}
            res = upload_thumbnail_curl(
                video_id=vid,
                thumb_path=thumb_candidates[0],
                channel="@GOR-9-station",
                project="GOR-9",
                package_id=package_dir.name,
            )
            if res["status"] == "ok":
                data["thumbnail_uploaded"] = True
                gor_yt.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            return res
        return {"status": "noop", "message": "GOR-9 package already has thumbnail_uploaded=true"}

    if (package_dir / "youtube.json").exists():
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "photon_platform.studio.uploader",
                str(package_dir),
                "--sync-missing",
                "--no-captions",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "package_dir": str(package_dir),
            "stdout": proc.stdout[-1200:],
            "stderr": proc.stderr[-600:],
        }

    return {"status": "noop", "message": "No youtube.json found to repair"}


def publish_packages(
    package_dirs: list[Path],
    channel: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Set `privacyStatus='public'` ONLY for the specified video package directories."""
    from .uploader import resolve_package_channel_and_token

    if not package_dirs:
        return {"status": "noop", "published": [], "message": "No package directories provided."}

    pkg_paths = [Path(p).resolve() for p in package_dirs]
    norm_channel, _token_path = resolve_package_channel_and_token(pkg_paths[0], explicit_channel=channel)

    video_ids: list[str] = []
    yt_files: list[Path] = []
    for pkg_path in pkg_paths:
        pkg_chan, _ = resolve_package_channel_and_token(pkg_path, explicit_channel=channel)
        if pkg_chan.lower() != norm_channel.lower():
            raise RuntimeError(
                f"Cannot mix channels in a single publish batch: {pkg_path} uses {pkg_chan}, expected {norm_channel}."
            )
        yt_file = pkg_path / "youtube.json"
        if not yt_file.exists() and (pkg_path / "youtube" / "youtube.json").exists():
            yt_file = pkg_path / "youtube" / "youtube.json"
        if not yt_file.exists():
            continue
        yt_files.append(yt_file)
        d = json.loads(yt_file.read_text(encoding="utf-8"))
        if d.get("video_id"):
            vid = d["video_id"]
            if vid not in video_ids:
                video_ids.append(vid)
        for up in (d.get("uploads") or {}).values():
            if isinstance(up, dict) and up.get("video_id"):
                vid = up["video_id"]
                if vid not in video_ids:
                    video_ids.append(vid)

    if not video_ids:
        return {"status": "noop", "channel": norm_channel, "published": [], "message": "No uploaded video IDs found."}

    if dry_run:
        return {
            "status": "dry_run",
            "channel": norm_channel,
            "video_ids": video_ids,
            "packages": [str(p) for p in pkg_paths],
        }

    access_token = get_access_token(channel=norm_channel, verify_handle=True)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    project_name = "GOR-9" if norm_channel.lower() == "@gor-9-station" else "geometor"
    ids_param = ",".join(video_ids)
    v_url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,status&id={urllib.parse.quote(ids_param)}"
    v_req = urllib.request.Request(v_url, headers=headers, method="GET")
    with urllib.request.urlopen(v_req, timeout=30) as resp:
        v_data = json.loads(resp.read().decode("utf-8"))
    record_quota_call("videos.list", project=project_name, channel=norm_channel, package_id=pkg_paths[0].name)

    published: list[dict[str, str]] = []
    for v_item in v_data.get("items", []):
        vid = v_item["id"]
        snippet = v_item.get("snippet", {})
        status_obj = v_item.get("status", {})
        old_priv = status_obj.get("privacyStatus", "unknown")
        title = snippet.get("title", vid)

        if old_priv == "public":
            published.append({"video_id": vid, "title": title, "previous_privacy": "public", "status": "already_public"})
            continue

        update_payload: dict[str, Any] = {
            "id": vid,
            "snippet": {
                "title": title,
                "description": snippet.get("description", ""),
                "categoryId": str(snippet.get("categoryId", "27")),
                "defaultLanguage": snippet.get("defaultLanguage", "en"),
                "defaultAudioLanguage": snippet.get("defaultAudioLanguage", "en"),
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }
        if snippet.get("tags"):
            update_payload["snippet"]["tags"] = snippet["tags"]

        put_req = urllib.request.Request(
            "https://www.googleapis.com/youtube/v3/videos?part=snippet,status",
            data=json.dumps(update_payload).encode("utf-8"),
            headers=headers,
            method="PUT",
        )
        with urllib.request.urlopen(put_req, timeout=30) as resp:
            resp.read()
        record_quota_call("videos.update", project=project_name, channel=norm_channel, package_id=vid)
        published.append({"video_id": vid, "title": title, "previous_privacy": old_priv, "status": "published"})

    for yt_file in yt_files:
        d = json.loads(yt_file.read_text(encoding="utf-8"))
        d["privacyStatus"] = "public"
        if "privacy_status" in d:
            d["privacy_status"] = "public"
        for up in (d.get("uploads") or {}).values():
            if isinstance(up, dict):
                up["privacyStatus"] = "public"
        yt_file.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")

    return {
        "status": "ok",
        "channel": norm_channel,
        "published": published,
    }


def schedule_packages(
    package_dirs: list[Path],
    start_iso: str,
    step_hours: float = 4.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Assign staggered publishAt timestamps (e.g. 1 every 4 hours) across package directories."""
    start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    scheduled_items: list[dict[str, Any]] = []

    for idx, pdir in enumerate(package_dirs):
        pdir = Path(pdir).resolve()
        slot_dt = start_dt + timedelta(hours=idx * step_hours)
        slot_iso = slot_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        yt_json = pdir / "youtube.json"
        if not yt_json.exists() and (pdir / "youtube" / "youtube.json").exists():
            yt_json = pdir / "youtube" / "youtube.json"

        item_info = {
            "index": idx + 1,
            "package_dir": str(pdir),
            "publish_at": slot_iso,
            "youtube_json": str(yt_json),
        }
        scheduled_items.append(item_info)

        if not dry_run and yt_json.exists():
            data = json.loads(yt_json.read_text(encoding="utf-8"))
            data["privacyStatus"] = "private"
            data["publishAt"] = slot_iso
            uploads = data.setdefault("uploads", {})
            for track_key in ("wide_1080p", "short"):
                if track_key in uploads and isinstance(uploads[track_key], dict):
                    uploads[track_key]["publishAt"] = slot_iso
                    uploads[track_key]["privacyStatus"] = "private"
            yt_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    if not dry_run:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        queue_data: dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat(), "queue": []}
        if GLOBAL_QUEUE_PATH.exists():
            try:
                queue_data = json.loads(GLOBAL_QUEUE_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing_by_dir = {entry["package_dir"]: entry for entry in queue_data.get("queue", [])}
        for item in scheduled_items:
            existing_by_dir[item["package_dir"]] = item
        queue_data["queue"] = sorted(existing_by_dir.values(), key=lambda x: x["publish_at"])
        GLOBAL_QUEUE_PATH.write_text(json.dumps(queue_data, indent=2) + "\n", encoding="utf-8")

    return {
        "dry_run": dry_run,
        "count": len(scheduled_items),
        "start_iso": start_iso,
        "step_hours": step_hours,
        "first_slot": scheduled_items[0]["publish_at"] if scheduled_items else None,
        "last_slot": scheduled_items[-1]["publish_at"] if scheduled_items else None,
        "items": scheduled_items,
    }
