"""Isolated Chromium HTML/SVG slide screenshotter and WebGL/Canvas capture harness."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any


# Canonical YouTube resolutions and UI safe-zone boundaries
WIDE_QHD_WIDTH = 2560
WIDE_QHD_HEIGHT = 1440
WIDE_FHD_WIDTH = 1920
WIDE_FHD_HEIGHT = 1080
SHORT_WIDTH = 1080
SHORT_HEIGHT = 1920

# Safe zones (keep critical text/proof clauses inside these bounds)
SAFE_ZONE_16X9_QHD_Y_MAX = 1268   # Bottom 12% reserved for YouTube scrub-bar & controls
SAFE_ZONE_16X9_FHD_Y_MAX = 950
SAFE_ZONE_9X16_BOTTOM_PX = 220    # Shorts bottom title/caption overlay
SAFE_ZONE_9X16_RIGHT_PX = 120     # Shorts right-hand action rail


def find_chromium_binary() -> str:
    from .engines.shorts import find_chrome

    return str(find_chrome())


def capture_html_viewport(
    html_or_url: str | Path,
    output_png: Path,
    width: int = WIDE_FHD_WIDTH,
    height: int = WIDE_FHD_HEIGHT,
    wait_ms: int = 1500,
) -> dict[str, Any]:
    """Capture an HTML file or URL in an isolated ephemeral Chromium profile and crop to exact WxH."""
    import os

    chrome_bin = find_chromium_binary()
    output_png = Path(output_png).resolve()
    output_png.parent.mkdir(parents=True, exist_ok=True)

    target_url = str(html_or_url)
    if not target_url.startswith(("http://", "https://", "file://")):
        target_url = f"file://{Path(html_or_url).resolve()}"

    with tempfile.TemporaryDirectory(prefix="photon_studio_chrome_") as tmp_dir:
        tmp_root = Path(tmp_dir)
        raw_png = tmp_root / "_raw_capture.png"
        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = str(tmp_root / "config")
        env["XDG_CACHE_HOME"] = str(tmp_root / "cache")
        env["HOME"] = str(tmp_root / "home")
        Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
        cmd = [
            chrome_bin,
            "--headless=new",
            f"--user-data-dir={tmp_root / 'profile'}",
            "--disk-cache-size=1",
            "--media-cache-size=1",
            "--aggressive-cache-discard",
            "--disable-application-cache",
            "--allow-file-access-from-files",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={width},{height + 100}",
            f"--virtual-time-budget={wait_ms}",
            f"--screenshot={raw_png}",
            target_url,
        ]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
        if res.returncode != 0 or not raw_png.exists():
            return {
                "status": "error",
                "error": f"Chromium capture failed: {res.stderr.strip()}",
            }

        convert_bin = shutil.which("convert") or "/usr/bin/convert"
        crop_cmd = [
            convert_bin,
            str(raw_png),
            "-crop",
            f"{width}x{height}+0+0",
            "+repage",
            str(output_png),
        ]
        crop_res = subprocess.run(crop_cmd, capture_output=True, text=True, check=False)
        if crop_res.returncode != 0:
            return {
                "status": "error",
                "error": f"ImageMagick crop failed: {crop_res.stderr.strip()}",
            }

    return {
        "status": "ok",
        "output_png": str(output_png),
        "width": width,
        "height": height,
    }
