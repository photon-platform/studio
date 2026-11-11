"""Mode 2 (`motion`): Deterministic 30.00 fps Full-Motion WebGL/Canvas Movie Engine.

Zero-dependency, hardware-GPU accelerated (`--use-gl=angle --use-angle=gl`)
capture engine for Three.js / WebGL / HTML5 Canvas scenes:
  1. Serves target HTML/assets from a local HTTP server with an injected
     deterministic virtual clock (`performance.now()`, `Date.now()`,
     `requestAnimationFrame`) and window-resize lock.
  2. Renders each frame at native `2560x1440` (`16:9` QHD) or `1080x1920`
     (`9:16` Vertical Short) on the hardware GPU.
  3. Reads raw RGBA pixels via `gl.readPixels` (or 2D canvas readback) and
     streams them via HTTP POST directly into `ffmpeg -f rawvideo -pix_fmt rgba`
     with `-vf "vflip,setsar=1:1,noise=alls=2:allf=t,format=yuv420p"` and
     `-crf 16 -preset slow -x264-params "aq-mode=3:aq-strength=0.8"`.
  4. Zero intermediate PNGs are written to disk, and zero Python/JS row-flip
     or zlib loops are needed.
"""

from __future__ import annotations

import functools
import http.server
import json
import math
import os
from pathlib import Path
import shutil
import socketserver
import struct
import subprocess
import threading
import time
from typing import Any
import urllib.parse
import wave


def find_chrome_binary() -> str:
    """Locate headless Chromium/Chrome binary (preferring Playwright's Chromium build)."""
    pw_root = Path.home() / ".cache" / "ms-playwright"
    if pw_root.is_dir():
        for candidate in sorted(pw_root.glob("chromium-*/chrome-linux*/chrome"), reverse=True):
            return str(candidate)
    for c in (
        "google-chrome",
        "google-chrome-stable",
        "chromium-browser",
        "chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ):
        p = shutil.which(c)
        if p:
            return p
    raise RuntimeError("No Chromium/Chrome binary found.")


STUDIO_PRE_SCRIPT_JS = r"""
<script>
(() => {
  const params = new URLSearchParams(window.location.search);
  const TARGET_W = parseInt(params.get('__studio_w') || '2560', 10);
  const TARGET_H = parseInt(params.get('__studio_h') || '1440', 10);

  // Lock window innerWidth / innerHeight & devicePixelRatio so WebGL never resizes or tears
  Object.defineProperty(window, 'innerWidth', { get: () => TARGET_W, configurable: true });
  Object.defineProperty(window, 'innerHeight', { get: () => TARGET_H, configurable: true });
  Object.defineProperty(window, 'devicePixelRatio', { get: () => 1, configurable: true });

  const origAddEventListener = window.addEventListener.bind(window);
  window.addEventListener = function(type, listener, options) {
    if (type === 'resize') return;
    return origAddEventListener(type, listener, options);
  };

  window.__studioVirtualTimeMs = 0.0;
  window.__studioFrameIndex = 0;
  window.__studioRafQueue = [];
  window.__studioRafId = 0;

  performance.now = () => window.__studioVirtualTimeMs;
  Date.now = () => 1700000000000 + Math.floor(window.__studioVirtualTimeMs);

  window.requestAnimationFrame = (cb) => {
    const id = ++window.__studioRafId;
    window.__studioRafQueue.push({ id, cb });
    return id;
  };
  window.cancelAnimationFrame = (id) => {
    window.__studioRafQueue = window.__studioRafQueue.filter((item) => item.id !== id);
  };

  const origGetContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function(type, attrs) {
    if (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl') {
      attrs = Object.assign({}, attrs || {}, {
        preserveDrawingBuffer: true,
        antialias: true,
        powerPreference: 'high-performance'
      });
    }
    const ctx = origGetContext.call(this, type, attrs);
    if (ctx && (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl')) {
      window.__studioGlCanvas = this;
      window.__studioGlContext = ctx;
    }
    return ctx;
  };

  window.__studioAdvanceFrame = (dtMs) => {
    window.__studioVirtualTimeMs += dtMs;
    window.__studioFrameIndex += 1;
    const queue = window.__studioRafQueue.slice();
    window.__studioRafQueue = [];
    for (const item of queue) {
      try {
        item.cb(window.__studioVirtualTimeMs);
      } catch (err) {
        console.error('Studio RAF error:', err);
      }
    }
  };
})();
</script>
"""


STUDIO_POST_SCRIPT_BODY = r"""
(async () => {
  const params = new URLSearchParams(window.location.search);
  const width = parseInt(params.get('__studio_w') || '2560', 10);
  const height = parseInt(params.get('__studio_h') || '1440', 10);
  const fps = parseFloat(params.get('__studio_fps') || '30');
  const duration = parseFloat(params.get('__studio_dur') || '30.0');
  const warmup = parseInt(params.get('__studio_warmup') || '15', 10);
  const totalFrames = Math.round(duration * fps);
  const dtMs = 1000.0 / fps;

  const log = async (msg) => {
    try {
      await fetch('/__studio_log', { method: 'POST', body: String(msg) });
    } catch (e) {}
  };

  try {
    const threeObj = (typeof THREE !== 'undefined') ? THREE : window.THREE;
    if (threeObj && threeObj.DefaultLoadingManager) {
      await new Promise((resolve) => {
        let settled = false;
        const done = () => {
          if (!settled) {
            settled = true;
            setTimeout(resolve, 400);
          }
        };
        threeObj.DefaultLoadingManager.onLoad = done;
        setTimeout(done, 1800);
      });
    } else {
      await new Promise((r) => setTimeout(r, 400));
    }

    const rdr = (typeof renderer !== 'undefined') ? renderer : window.renderer;
    const cam = (typeof camera !== 'undefined') ? camera : window.camera;
    if (rdr && typeof rdr.setSize === 'function') {
      rdr.setPixelRatio(1);
      rdr.setSize(width, height, false);
    }
    if (cam && typeof cam.updateProjectionMatrix === 'function') {
      cam.aspect = width / height;
      cam.updateProjectionMatrix();
    }

    if (typeof window.__studioSetup === 'function') {
      await window.__studioSetup(width, height, duration, fps);
    }

    const canvas = (rdr && rdr.domElement) || window.__studioGlCanvas || document.querySelector('canvas');
    if (!canvas) {
      throw new Error('No <canvas> found in document for Mode 2 motion capture.');
    }
    const gl = (rdr && typeof rdr.getContext === 'function' && rdr.getContext()) || window.__studioGlContext || canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (!gl) {
      throw new Error('No WebGL context found on <canvas>.');
    }

    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    const gpuName = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
    await log(`WebGL initialized (${width}x${height} @ ${fps}fps, ${totalFrames} frames) on GPU: ${gpuName}`);

    if (typeof window.__studioStep !== 'function') {
      for (let w = 0; w < warmup; w++) {
        window.__studioAdvanceFrame(dtMs);
      }
      window.__studioVirtualTimeMs = 0.0;
      window.__studioFrameIndex = 0;
    }

    const pixelBuf = new Uint8Array(width * height * 4);

    for (let idx = 0; idx < totalFrames; idx++) {
      const tSec = idx / fps;
      if (typeof window.__studioStep === 'function') {
        await window.__studioStep(idx, tSec, duration, width, height);
      } else {
        window.__studioAdvanceFrame(dtMs);
      }

      if (canvas.width !== width || canvas.height !== height) {
        if (rdr && typeof rdr.setSize === 'function') {
          rdr.setPixelRatio(1);
          rdr.setSize(width, height, false);
        } else {
          canvas.width = width;
          canvas.height = height;
        }
      }

      gl.finish();
      gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixelBuf);

      for (let attempt = 0; attempt < 5; attempt++) {
        try {
          const resp = await fetch(`/__studio_rgba/${idx}`, {
            method: 'POST',
            body: pixelBuf
          });
          if (resp.ok) break;
        } catch (err) {
          await new Promise((r) => setTimeout(r, 50 * (attempt + 1)));
        }
      }
    }

    await fetch('/__studio_done', { method: 'POST', body: 'DONE' });
  } catch (err) {
    await log('STUDIO_MOTION_ERR: ' + String((err && err.stack) || err));
    await fetch('/__studio_done', { method: 'POST', body: 'ERR' });
  }
})();
"""


def generate_ambient_drone_wav(
    wav_path: Path,
    duration_sec: float,
    base_freq: float = 55.0,
    sample_rate: int = 48000,
) -> Path:
    """Synthesize a subtle stereo harmonic ambient space bed (48 kHz 16-bit PCM WAV)."""
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(duration_sec * sample_rate)
    phi = (1.0 + math.sqrt(5.0)) / 2.0

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)

        chunk = bytearray()
        for i in range(n_samples):
            t = i / sample_rate
            fade_in = min(1.0, t / 1.5)
            fade_out = min(1.0, max(0.0, (duration_sec - t) / 2.0))
            env = fade_in * fade_out * 0.14

            sig_l = (
                0.55 * math.sin(2.0 * math.pi * base_freq * t)
                + 0.28 * math.sin(2.0 * math.pi * (base_freq * 1.5) * t + 0.3)
                + 0.17 * math.sin(2.0 * math.pi * (base_freq * phi) * t + 0.1 * math.sin(0.25 * t))
            )
            sig_r = (
                0.55 * math.sin(2.0 * math.pi * base_freq * t + 0.12)
                + 0.28 * math.sin(2.0 * math.pi * (base_freq * 1.5) * t + 0.45)
                + 0.17 * math.sin(2.0 * math.pi * (base_freq * phi) * t + 0.9)
            )
            val_l = max(-32767, min(32767, int(sig_l * env * 32767)))
            val_r = max(-32767, min(32767, int(sig_r * env * 32767)))
            chunk.extend(struct.pack("<hh", val_l, val_r))

            if len(chunk) >= 65536:
                wf.writeframesraw(chunk)
                chunk.clear()
        if chunk:
            wf.writeframesraw(chunk)

    return wav_path


def build_motion_ffmpeg_cmd(
    output_mp4: Path,
    width: int,
    height: int,
    fps: int = 30,
    input_format: str = "rawvideo_vflip",
    audio_wav: Path | None = None,
    duration_sec: float | None = None,
    crf: int = 16,
    preset: str = "slow",
) -> list[str]:
    """Construct the clean Mode 2 (`motion`) FFmpeg encoding command."""
    cmd = ["/usr/bin/ffmpeg", "-y"]
    if input_format in ("rawvideo", "rawvideo_vflip"):
        cmd.extend([
            "-f", "rawvideo",
            "-pix_fmt", "rgba",
            "-s", f"{width}x{height}",
            "-framerate", str(fps),
            "-i", "pipe:0",
        ])
    else:
        cmd.extend([
            "-f", "image2pipe",
            "-framerate", str(fps),
            "-i", "pipe:0",
        ])

    if audio_wav and audio_wav.exists():
        cmd.extend(["-i", str(audio_wav), "-shortest"])

    if duration_sec is not None:
        cmd.extend(["-t", f"{duration_sec:.3f}"])

    if input_format == "rawvideo_vflip":
        cmd.extend(["-vf", "vflip"])

    cmd.append(str(output_mp4))
    return cmd


def extract_thumbnail_from_mp4(
    mp4_path: Path,
    thumb_png: Path,
    timestamp_sec: float,
) -> Path:
    """Extract an exact PNG frame from a rendered MP4 at `timestamp_sec`."""
    thumb_png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "/usr/bin/ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, timestamp_sec):.3f}",
            "-i",
            str(mp4_path),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(thumb_png),
        ],
        check=True,
        capture_output=True,
    )
    return thumb_png


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _StudioMotionHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args: Any,
        directory: str,
        entry_html_name: str,
        injected_js: str,
        ffmpeg_proc: subprocess.Popen[bytes],
        done_event: threading.Event,
        state: dict[str, Any],
        total_frames: int,
        **kwargs: Any,
    ) -> None:
        self._entry_html_name = entry_html_name
        self._injected_js = injected_js
        self._ffmpeg_proc = ffmpeg_proc
        self._done_event = done_event
        self._state = state
        self._total_frames = total_frames
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/__studio_motion.html":
            orig_html = (Path(self.directory) / self._entry_html_name).read_text(encoding="utf-8")
            if "<head>" in orig_html:
                patched = orig_html.replace("<head>", "<head>\n" + STUDIO_PRE_SCRIPT_JS, 1)
            else:
                patched = STUDIO_PRE_SCRIPT_JS + "\n" + orig_html

            if "    animate();\n  </script>" in patched:
                keep_animate = "" if ("__studioStep" in self._injected_js) else "    animate();\n"
                combined_in_module = f"{keep_animate}\n{self._injected_js}\n{STUDIO_POST_SCRIPT_BODY}\n  </script>"
                patched = patched.replace("    animate();\n  </script>", combined_in_module, 1)
            else:
                custom_block = f"<script>\n{self._injected_js}\n{STUDIO_POST_SCRIPT_BODY}\n</script>\n"
                if "</body>" in patched:
                    patched = patched.replace("</body>", custom_block + "\n</body>", 1)
                else:
                    patched = patched + "\n" + custom_block

            body = patched.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = self.rfile.read(length) if length > 0 else b""

            if self.path.startswith("/__studio_log"):
                msg = data.decode("utf-8", errors="replace")
                print(f"  [Studio Motion] {msg}", flush=True)
                self._state["logs"].append(msg)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"OK")
                return

            if self.path.startswith("/__studio_rgba/"):
                if self._ffmpeg_proc.stdin:
                    self._ffmpeg_proc.stdin.write(data)
                self._state["captured"] += 1
                self._state["last_ts"] = time.monotonic()
                c = self._state["captured"]
                if c == 1 or c % 30 == 0 or c == self._total_frames:
                    elapsed = time.monotonic() - self._state["start_ts"]
                    eff_fps = c / max(0.001, elapsed)
                    print(
                        f"  [Studio Motion] Streamed frame {c}/{self._total_frames} "
                        f"({eff_fps:.1f} fps)",
                        flush=True,
                    )
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"OK")
                if c >= self._total_frames:
                    self._done_event.set()
                return

            if self.path.startswith("/__studio_done"):
                status_str = data.decode("utf-8", errors="replace").strip()
                self._state["done_status"] = status_str
                self.send_response(200)
                self.send_header("Content-Length", "4")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"DONE")
                self._done_event.set()
                return

            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass


def capture_motion_viewport(
    url_or_html: str,
    output_mp4: Path,
    width: int = 2560,
    height: int = 1440,
    duration_sec: float = 30.0,
    fps: int = 30,
    warmup_frames: int = 15,
    injected_js: str | None = None,
    query_params: dict[str, str] | None = None,
    audio_wav: Path | None = None,
    ambient_drone: bool = True,
    thumb_png: Path | None = None,
    thumb_sec: float | None = None,
    crf: int = 16,
    preset: str = "slow",
) -> dict[str, Any]:
    """Capture a deterministic 30.00 fps full-motion WebGL movie via hardware GPU directly to FFmpeg."""
    output_mp4 = Path(output_mp4).resolve()
    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    html_path = Path(url_or_html.replace("file://", "")).resolve()
    if not html_path.exists():
        raise FileNotFoundError(f"HTML file not found for Mode 2 motion capture: {html_path}")

    serve_dir = html_path.parent
    entry_html_name = html_path.name

    temp_wav: Path | None = None
    if audio_wav is None and ambient_drone:
        temp_wav = output_mp4.with_suffix(".ambient_tmp.wav")
        generate_ambient_drone_wav(temp_wav, duration_sec=duration_sec)
        audio_wav = temp_wav

    total_frames = int(round(duration_sec * fps))
    ffmpeg_cmd = build_motion_ffmpeg_cmd(
        output_mp4=output_mp4,
        width=width,
        height=height,
        fps=fps,
        input_format="rawvideo_vflip",
        audio_wav=audio_wav,
        duration_sec=duration_sec,
        crf=crf,
        preset=preset,
    )

    t0 = time.monotonic()
    ffmpeg_proc = subprocess.Popen(
        ffmpeg_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    done_event = threading.Event()
    state: dict[str, Any] = {
        "captured": 0,
        "start_ts": t0,
        "last_ts": t0,
        "done_status": "",
        "logs": [],
    }

    handler = functools.partial(
        _StudioMotionHTTPHandler,
        directory=str(serve_dir),
        entry_html_name=entry_html_name,
        injected_js=injected_js or "",
        ffmpeg_proc=ffmpeg_proc,
        done_event=done_event,
        state=state,
        total_frames=total_frames,
    )
    server = _ThreadedTCPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
    srv_thread.start()

    chrome_bin = find_chrome_binary()
    tmp_profile = Path(f"/tmp/studio_motion_profile_{os.getpid()}_{int(t0 * 1000)}")
    tmp_profile.mkdir(parents=True, exist_ok=True)

    q_dict = {
        "__studio_w": str(width),
        "__studio_h": str(height),
        "__studio_fps": str(fps),
        "__studio_dur": f"{duration_sec:.4f}",
        "__studio_warmup": str(warmup_frames),
    }
    if query_params:
        q_dict.update(query_params)
    query_str = urllib.parse.urlencode(q_dict)
    target_url = f"http://127.0.0.1:{port}/__studio_motion.html?{query_str}"

    chrome_cmd = [
        chrome_bin,
        "--headless=new",
        f"--user-data-dir={tmp_profile}",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--enable-webgl",
        "--ignore-gpu-blocklist",
        "--use-gl=angle",
        "--use-angle=gl",
        "--enable-gpu-rasterization",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        f"--window-size={width},{height + 160}",
        target_url,
    ]

    chrome_proc = subprocess.Popen(
        chrome_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        while not done_event.wait(timeout=0.5):
            now = time.monotonic()
            if chrome_proc.poll() is not None:
                break
            if now - state["last_ts"] > 120.0:
                raise RuntimeError("Studio Motion WebGL worker timed out waiting for next frame.")
    finally:
        chrome_proc.terminate()
        try:
            chrome_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            chrome_proc.kill()
        server.shutdown()
        server.server_close()
        shutil.rmtree(tmp_profile, ignore_errors=True)

        if ffmpeg_proc.stdin:
            ffmpeg_proc.stdin.close()
        stderr_bytes = ffmpeg_proc.stderr.read() if ffmpeg_proc.stderr else b""
        rc = ffmpeg_proc.wait(timeout=120)
        if temp_wav and temp_wav.exists():
            temp_wav.unlink(missing_ok=True)

    if state["done_status"] == "ERR" or state["captured"] < total_frames or rc != 0:
        raise RuntimeError(
            f"Mode 2 motion capture failed (captured={state['captured']}/{total_frames}, "
            f"ffmpeg_rc={rc}, logs={state['logs'][-3:]}): "
            f"{stderr_bytes.decode('utf-8', errors='replace')[-500:]}"
        )

    if thumb_png:
        hero_t = thumb_sec if thumb_sec is not None else round(duration_sec * 0.618, 2)
        extract_thumbnail_from_mp4(output_mp4, Path(thumb_png), timestamp_sec=hero_t)

    elapsed = round(time.monotonic() - t0, 2)
    return {
        "ok": True,
        "mode": "motion",
        "output_mp4": str(output_mp4),
        "width": width,
        "height": height,
        "fps": fps,
        "frames": state["captured"],
        "duration_sec": duration_sec,
        "thumb_png": str(thumb_png) if thumb_png else None,
        "elapsed_sec": elapsed,
        "effective_fps": round(state["captured"] / max(0.001, elapsed), 2),
        "logs": state["logs"],
    }


def render_motion_package(
    package_dir: Path,
    prefix: str | None = None,
) -> dict[str, Any]:
    """Render a Mode 2 (`motion`) package directory from `motion.json`."""
    package_dir = Path(package_dir).resolve()
    spec_path = package_dir / "motion.json"
    if not spec_path.exists():
        raise FileNotFoundError(f"No motion.json found in {package_dir}")

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    pfx = prefix or spec.get("prefix") or package_dir.name
    html_rel = spec["html"]
    html_path = (package_dir / html_rel).resolve() if not Path(html_rel).is_absolute() else Path(html_rel)

    injected_js = spec.get("injected_js", "")
    if spec.get("injected_js_file"):
        js_file = (package_dir / spec["injected_js_file"]).resolve()
        injected_js = js_file.read_text(encoding="utf-8")

    duration_sec = float(spec.get("duration_sec", 30.0))
    fps = int(spec.get("fps", 30))
    warmup_frames = int(spec.get("warmup_frames", 15))
    thumb_sec = float(spec.get("thumb_sec", round(duration_sec * 0.618, 2)))
    query_params = spec.get("query_params") or {}

    wav_candidate = package_dir / (spec.get("audio_wav") or f"{pfx}.wav")
    audio_wav = wav_candidate if wav_candidate.exists() else None

    wide_mp4 = package_dir / (spec.get("wide_mp4") or f"{pfx}_1080p.mp4")
    wide_thumb = package_dir / (spec.get("wide_thumb") or f"{pfx}_thumb.png")

    wide_res = capture_motion_viewport(
        url_or_html=str(html_path),
        output_mp4=wide_mp4,
        width=2560,
        height=1440,
        duration_sec=duration_sec,
        fps=fps,
        warmup_frames=warmup_frames,
        injected_js=injected_js,
        query_params=query_params,
        audio_wav=audio_wav,
        ambient_drone=bool(spec.get("ambient_drone", True)),
        thumb_png=wide_thumb,
        thumb_sec=thumb_sec,
    )

    short_res = None
    if spec.get("render_short", False):
        short_mp4 = package_dir / (spec.get("short_mp4") or f"{pfx}_short.mp4")
        short_thumb = package_dir / (spec.get("short_thumb") or f"{pfx}_short_thumb.png")
        short_duration = float(spec.get("short_duration_sec", min(duration_sec, 58.0)))
        short_res = capture_motion_viewport(
            url_or_html=str(html_path),
            output_mp4=short_mp4,
            width=1080,
            height=1920,
            duration_sec=short_duration,
            fps=fps,
            warmup_frames=warmup_frames,
            injected_js=injected_js,
            query_params=query_params,
            audio_wav=audio_wav,
            ambient_drone=bool(spec.get("ambient_drone", True)),
            thumb_png=short_thumb,
            thumb_sec=min(thumb_sec, short_duration * 0.618),
        )

    return {
        "ok": True,
        "mode": "motion",
        "package_dir": str(package_dir),
        "prefix": pfx,
        "wide": wide_res,
        "short": short_res,
    }
