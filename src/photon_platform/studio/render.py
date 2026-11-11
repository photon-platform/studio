"""Canonical FFmpeg 1440p QHD (VP9 trigger) and 9:16 Vertical Shorts encoder.

Codifies two distinct rendering modes:
  1. mode="slides" (`stage`): Declarative vector proof/center/console stage (`stage.py` per-segment pipeline).
  2. mode="motion" (`motion`): 30.00 fps full-motion 3D WebGL/Canvas/Blender/Compositor pipeline (`motion.py`).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .archive import clean_scratch
from .verify import MAX_THUMBNAIL_BYTES, verify_package_dir


def build_ffmpeg_encode_cmd(
    concat_or_input: Path,
    audio_wav: Path | None,
    output_mp4: Path,
    variant: str = "wide_16x9",
    mode: str = "slides",
    total_duration: float | None = None,
    is_concat: bool = True,
) -> list[str]:
    """Return the clean FFmpeg command for Mode 1 (`slides`) or Mode 2 (`motion`).

    - Mode 2 (`motion` PNG sequence): `ffmpeg -y -framerate 30 -i frames/frame_%04d.png -i audio.wav -pix_fmt yuv420p -movflags +faststart output.mp4`
    - Mode 1 (`slides` concat): `ffmpeg -y -f concat -safe 0 -i concat.txt -i audio.wav -vsync vfr -pix_fmt yuv420p -movflags +faststart output.mp4`
    """
    cmd = ["/usr/bin/ffmpeg", "-y"]
    if is_concat:
        cmd.extend(["-f", "concat", "-safe", "0", "-i", str(concat_or_input)])
    else:
        cmd.extend(["-framerate", "30", "-i", str(concat_or_input)])

    if audio_wav and audio_wav.exists():
        cmd.extend(["-i", str(audio_wav)])
        if not is_concat:
            cmd.append("-shortest")

    if total_duration is not None and not is_concat:
        cmd.extend(["-t", f"{total_duration:.3f}"])

    if is_concat:
        cmd.extend(["-vsync", "vfr"])

    cmd.extend(["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output_mp4)])
    return cmd


def ensure_compressed_thumbnail(thumb_path: Path) -> Path:
    """Ensure a thumbnail is < 2 MB for YouTube API upload by converting PNG to JPEG if needed."""
    thumb_path = Path(thumb_path)
    if not thumb_path.exists():
        raise FileNotFoundError(f"Thumbnail not found: {thumb_path}")

    if thumb_path.suffix.lower() in (".jpg", ".jpeg") and thumb_path.stat().st_size <= MAX_THUMBNAIL_BYTES:
        return thumb_path

    if thumb_path.suffix.lower() == ".png" and thumb_path.stat().st_size <= MAX_THUMBNAIL_BYTES:
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
    force: bool = False,
) -> dict[str, Any]:
    """Render a package directory (`mode='slides'` via `stage.py` or `mode='motion'` via `motion.py`),
    verify with `ffprobe`, and optionally purge Tier 3 scratch."""
    package_dir = Path(package_dir).resolve()

    has_motion_spec = (package_dir / "motion.json").exists() or (package_dir / "camera_path.json").exists()
    if mode == "motion" or has_motion_spec:
        from .motion import render_motion_package

        motion_res = render_motion_package(package_dir=package_dir, prefix=prefix, clean=force)
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

    from .speak import synthesize_script
    from .stage import infer_package_prefix, render_stage_package

    script_scr = package_dir / "script.scr"
    script_txt = script_scr if script_scr.exists() else (package_dir / "script.txt")
    scenes_source = script_scr if script_scr.exists() else (package_dir / "scenes.json")

    if not prefix:
        prefix = infer_package_prefix(package_dir)

    wav_path = package_dir / f"{prefix}.wav"
    manifest_json = package_dir / f"{prefix}.json"

    if tts or not wav_path.exists() or not manifest_json.exists():
        if not script_txt.exists():
            return {"status": "error", "error": f"Missing {script_txt}"}
        try:
            synthesize_script(script_txt, output_wav=wav_path, out_dir=package_dir, prefix=prefix)
        except Exception as exc:
            return {"status": "error", "stage": "speak", "error": str(exc)}

    if not scenes_source.exists() and not script_txt.exists():
        return {"status": "error", "error": f"Missing {scenes_source}"}

    stage_res = render_stage_package(package_dir, prefix=prefix, force=force)
    if stage_res.get("status") != "ok":
        return {"status": "error", "stage": "stage_render", "details": stage_res}

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
        "stage": stage_res,
        "verification": verification,
        "scratch_cleaned": scratch_report,
    }
