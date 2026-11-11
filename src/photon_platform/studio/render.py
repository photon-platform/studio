"""Canonical FFmpeg 1440p QHD (VP9 trigger) and 9:16 Vertical Shorts encoder.

Codifies two distinct rendering modes:
  1. mode="slides": Timed static vector slide sequence (-f concat, -tune stillimage, -crf 18, -preset fast).
  2. mode="motion": 30 fps full-motion 3D/animation sequence (-framerate 30, -crf 16, -preset slow,
                    temporal dither noise=alls=2:allf=t, and dark-scene aq-mode=3:aq-strength=0.8).
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Any

from .archive import clean_scratch
from .verify import MAX_THUMBNAIL_BYTES, verify_package_dir


DEFAULT_SHORTS_PY = Path(__file__).resolve().parent / "engines" / "shorts.py"
DEFAULT_SPEAK_PY = Path(__file__).resolve().parent / "engines" / "speak.py"


def build_ffmpeg_encode_cmd(
    concat_or_input: Path,
    audio_wav: Path,
    output_mp4: Path,
    variant: str = "wide_16x9",
    mode: str = "slides",
    total_duration: float | None = None,
    is_concat: bool = True,
) -> list[str]:
    """Return the minimal FFmpeg command for Mode 1 (slides) or Mode 2 (motion).

    - Mode 2 (motion): `ffmpeg -y -framerate 30 -i frames/frame_%04d.png -i audio.wav output.mp4`
    - Mode 1 (slides): `ffmpeg -y -f concat -safe 0 -i concat.txt -i audio.wav -vsync vfr -pix_fmt yuv420p output.mp4`
    """
    cmd = ["/usr/bin/ffmpeg", "-y"]
    if is_concat:
        cmd.extend(["-f", "concat", "-safe", "0", "-i", str(concat_or_input)])
    else:
        cmd.extend(["-framerate", "30", "-i", str(concat_or_input)])

    if audio_wav and audio_wav.exists():
        cmd.extend(["-i", str(audio_wav)])

    if is_concat:
        cmd.extend(["-vsync", "vfr", "-pix_fmt", "yuv420p"])

    cmd.append(str(output_mp4))
    return cmd


def ensure_compressed_thumbnail(thumb_path: Path) -> Path:
    """Ensure a thumbnail is < 2 MB for YouTube API upload by converting PNG to JPEG if needed."""
    thumb_path = Path(thumb_path)
    if not thumb_path.exists():
        raise FileNotFoundError(f"Thumbnail not found: {thumb_path}")

    if thumb_path.suffix.lower() in (".jpg", ".jpeg") and thumb_path.stat().st_size <= MAX_THUMBNAIL_BYTES:
        return thumb_path

    jpg_path = thumb_path.with_suffix(".jpg")
    for qv in ("4", "6", "8", "12"):
        cmd = [
            "/usr/bin/ffmpeg",
            "-y",
            "-i",
            str(thumb_path),
            "-frames:v",
            "1",
            "-q:v",
            qv,
            str(jpg_path),
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=False)
        if jpg_path.exists() and jpg_path.stat().st_size <= MAX_THUMBNAIL_BYTES:
            return jpg_path

    return jpg_path if jpg_path.exists() else thumb_path


def render_package(
    package_dir: Path,
    prefix: str | None = None,
    mode: str = "slides",
    tts: bool = False,
    purge_scratch: bool = True,
) -> dict[str, Any]:
    """Render a dual-format package directory (mode='slides' or 'motion'), verify with ffprobe, and optionally purge Tier 3 scratch."""
    package_dir = Path(package_dir).resolve()

    if mode == "motion" or (package_dir / "motion.json").exists():
        from .motion import render_motion_package

        motion_res = render_motion_package(package_dir=package_dir, prefix=prefix)
        pfx = motion_res.get("prefix") or prefix or package_dir.name
        for png_thumb in sorted(package_dir.glob("*thumb*.png")):
            ensure_compressed_thumbnail(png_thumb)
        verification = verify_package_dir(package_dir)
        scratch_report = None
        if purge_scratch and verification.get("all_valid"):
            scratch_report = clean_scratch(package_dir, dry_run=False, require_mp4=True)
        return {
            "status": "ok" if verification.get("all_valid") else "verification_failed",
            "mode": "motion",
            "prefix": pfx,
            "package_dir": str(package_dir),
            "motion": motion_res,
            "verification": verification,
            "scratch_cleaned": scratch_report,
        }

    script_scr = package_dir / "script.scr"
    script_txt = script_scr if script_scr.exists() else (package_dir / "script.txt")
    scenes_source = script_scr if script_scr.exists() else (package_dir / "scenes.json")

    if not prefix:
        wavs = sorted(package_dir.glob("*.wav"))
        if wavs:
            prefix = wavs[0].stem
        elif package_dir.parent.name.startswith("book_"):
            rom = package_dir.parent.name.replace("book_", "").upper()
            prefix = f"{rom}_{package_dir.name}"
        else:
            prefix = package_dir.name

    wav_path = package_dir / f"{prefix}.wav"
    manifest_json = package_dir / f"{prefix}.json"

    if tts or not wav_path.exists() or not manifest_json.exists():
        if not script_txt.exists():
            return {"status": "error", "error": f"Missing {script_txt}"}
        res_tts = subprocess.run(
            [sys.executable, str(DEFAULT_SPEAK_PY), "-f", str(script_txt), "-o", str(wav_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if res_tts.returncode != 0:
            return {"status": "error", "stage": "speak", "stderr": res_tts.stderr.strip()}

    if not scenes_source.exists():
        return {"status": "error", "error": f"Missing {scenes_source}"}

    is_elements_prop = package_dir.parent.name.startswith("book_") or (
        script_scr.exists() and "prop:" in script_scr.read_text(encoding="utf-8")[:400]
    )

    if is_elements_prop:
        from .stage import render_stage_package

        stage_res = render_stage_package(package_dir, prefix=prefix)
        if stage_res.get("status") != "ok":
            return {"status": "error", "stage": "stage_render", "details": stage_res}
    else:
        res_render = subprocess.run(
            [
                sys.executable,
                str(DEFAULT_SHORTS_PY),
                "-s",
                str(scenes_source),
                "-m",
                str(manifest_json),
                "-o",
                str(package_dir),
                "-p",
                prefix,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if res_render.returncode != 0:
            return {"status": "error", "stage": "render", "stderr": res_render.stderr.strip()}
        for png_thumb in sorted(package_dir.glob("*thumb*.png")):
            ensure_compressed_thumbnail(png_thumb)

    verification = verify_package_dir(package_dir)
    scratch_report = None
    if purge_scratch and verification.get("all_valid"):
        scratch_report = clean_scratch(package_dir, dry_run=False, require_mp4=True)

    return {
        "status": "ok" if verification.get("all_valid") else "verification_failed",
        "mode": "slides",
        "prefix": prefix,
        "package_dir": str(package_dir),
        "verification": verification,
        "scratch_cleaned": scratch_report,
    }
