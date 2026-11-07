"""Canonical FFmpeg 1440p QHD (VP9 trigger) and 9:16 Vertical Shorts encoder."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Any

from .archive import clean_scratch
from .verify import MAX_THUMBNAIL_BYTES, verify_package_dir, verify_video_file


DEFAULT_SHORTS_PY = Path(__file__).resolve().parent / "engines" / "shorts.py"
DEFAULT_SPEAK_PY = Path(__file__).resolve().parent / "engines" / "speak.py"


def build_ffmpeg_encode_cmd(
    concat_or_input: Path,
    audio_wav: Path,
    output_mp4: Path,
    variant: str = "wide_16x9",
    total_duration: float | None = None,
    is_concat: bool = True,
) -> list[str]:
    """Return the locked FFmpeg command for YouTube 1440p QHD (VP9) or 9:16 Vertical Shorts."""
    cmd = ["/usr/bin/ffmpeg", "-y"]
    if is_concat:
        cmd.extend(["-f", "concat", "-safe", "0", "-i", str(concat_or_input)])
    else:
        cmd.extend(["-i", str(concat_or_input)])

    cmd.extend([
        "-i",
        str(audio_wav),
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-b:a",
        "192k",
        "-shortest",
    ])
    if total_duration is not None and total_duration > 0:
        cmd.extend(["-t", f"{total_duration:.3f}"])

    if variant == "wide_16x9":
        cmd.extend([
            "-vf",
            "fps=30,scale=2560:1440:flags=lanczos,setsar=1:1,format=yuv420p",
            "-vsync",
            "cfr",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-profile:v",
            "high",
            "-level:v",
            "5.1",
            "-crf",
            "18",
            "-maxrate",
            "16M",
            "-bufsize",
            "32M",
        ])
    elif variant == "short_9x16":
        cmd.extend([
            "-vf",
            "fps=30,scale=1080:1920:flags=lanczos,setsar=1:1,format=yuv420p",
            "-vsync",
            "cfr",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-profile:v",
            "high",
            "-level:v",
            "4.1",
            "-crf",
            "18",
            "-maxrate",
            "10M",
            "-bufsize",
            "20M",
        ])
    else:
        raise ValueError(f"Unknown variant: {variant}")

    cmd.extend([
        "-g",
        "90",
        "-keyint_min",
        "90",
        "-sc_threshold",
        "0",
        "-bf",
        "2",
        "-flags",
        "+cgop",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_range",
        "tv",
        "-movflags",
        "+faststart",
        str(output_mp4),
    ])
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
    tts: bool = False,
    purge_scratch: bool = True,
) -> dict[str, Any]:
    """Render a dual-format package directory, verify with ffprobe, and optionally purge Tier 3 scratch."""
    package_dir = Path(package_dir).resolve()
    script_scr = package_dir / "script.scr"
    script_txt = script_scr if script_scr.exists() else (package_dir / "script.txt")
    scenes_source = script_scr if script_scr.exists() else (package_dir / "scenes.json")

    if not prefix:
        # Infer prefix from existing .wav/.json or parent folder
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

    # Pre-compress thumbnails to < 2 MB .jpg
    for png_thumb in sorted(package_dir.glob("*thumb*.png")):
        ensure_compressed_thumbnail(png_thumb)

    verification = verify_package_dir(package_dir)
    scratch_report = None
    if purge_scratch and verification.get("all_valid"):
        scratch_report = clean_scratch(package_dir, dry_run=False, require_mp4=True)

    return {
        "status": "ok" if verification.get("all_valid") else "verification_failed",
        "prefix": prefix,
        "package_dir": str(package_dir),
        "verification": verification,
        "scratch_cleaned": scratch_report,
    }
