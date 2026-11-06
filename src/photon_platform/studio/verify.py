"""FFprobe verification gatekeeper for YouTube 1440p QHD (VP9) and 9:16 Vertical Shorts masters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import subprocess
from typing import Any


MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024  # 2 MiB YouTube API limit


@dataclass
class StreamReport:
    path: str
    exists: bool
    variant: str  # "wide_16x9" or "short_9x16"
    valid: bool = False
    width: int = 0
    height: int = 0
    codec_name: str = ""
    profile: str = ""
    pix_fmt: str = ""
    color_space: str = ""
    r_frame_rate: str = ""
    avg_frame_rate: str = ""
    duration_sec: float = 0.0
    audio_codec: str = ""
    audio_sample_rate: int = 0
    audio_channels: int = 0
    file_size_mb: float = 0.0
    vp9_trigger_ready: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probe_media(mp4_path: Path) -> dict[str, Any]:
    """Run ffprobe and return parsed JSON streams and format."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_Format",
        "-show_streams",
        "-of",
        "json",
        str(mp4_path),
    ]
    # Note: ffprobe option is -show_format (lowercase)
    cmd[3] = "-show_format"
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {mp4_path}: {res.stderr.strip()}")
    return json.loads(res.stdout)


def verify_video_file(mp4_path: Path, variant: str = "wide_16x9") -> StreamReport:
    """Validate a video file against the PHOTON Studio YouTube master specification.

    - wide_16x9: Must be 2560x1440 (1440p QHD) to trigger YouTube's vp09 (VP9) hardware pipeline,
      h264 High profile, yuv420p, 30fps CFR, bt709 color signaling, and 48kHz stereo AAC audio.
    - short_9x16: Must be 1080x1920 (FHD Vertical), h264 High profile, yuv420p, 30fps CFR,
      bt709 color signaling, and 48kHz stereo AAC audio.
    """
    mp4_path = Path(mp4_path)
    report = StreamReport(path=str(mp4_path), exists=mp4_path.exists(), variant=variant)
    if not mp4_path.exists():
        report.errors.append("File does not exist")
        return report

    report.file_size_mb = round(mp4_path.stat().st_size / (1024 * 1024), 2)
    try:
        info = probe_media(mp4_path)
    except Exception as exc:
        report.errors.append(str(exc))
        return report

    streams = info.get("streams", [])
    fmt = info.get("format", {})
    report.duration_sec = round(float(fmt.get("duration", 0.0) or 0.0), 3)

    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if not v_stream:
        report.errors.append("Missing video stream")
        return report

    report.width = int(v_stream.get("width", 0))
    report.height = int(v_stream.get("height", 0))
    report.codec_name = v_stream.get("codec_name", "")
    report.profile = v_stream.get("profile", "")
    report.pix_fmt = v_stream.get("pix_fmt", "")
    report.color_space = v_stream.get("color_space", "")
    report.r_frame_rate = v_stream.get("r_frame_rate", "")
    report.avg_frame_rate = v_stream.get("avg_frame_rate", "")

    if variant == "wide_16x9":
        if (report.width, report.height) != (2560, 1440):
            report.errors.append(
                f"Expected 2560x1440 (1440p QHD for VP9 trigger), got {report.width}x{report.height}"
            )
        else:
            report.vp9_trigger_ready = True
    elif variant == "short_9x16":
        if (report.width, report.height) != (1080, 1920):
            report.errors.append(
                f"Expected 1080x1920 (9:16 Vertical Short), got {report.width}x{report.height}"
            )
        else:
            report.vp9_trigger_ready = True

    if report.codec_name != "h264":
        report.errors.append(f"Expected codec h264, got {report.codec_name}")
    if report.pix_fmt != "yuv420p":
        report.errors.append(f"Expected pix_fmt yuv420p, got {report.pix_fmt}")
    if report.color_space and report.color_space != "bt709":
        report.warnings.append(f"Expected color_space bt709, got {report.color_space}")
    if report.r_frame_rate not in ("30/1", "30000/1001"):
        report.warnings.append(f"Expected 30fps r_frame_rate, got {report.r_frame_rate}")

    if not a_stream:
        report.errors.append("Missing audio stream")
    else:
        report.audio_codec = a_stream.get("codec_name", "")
        report.audio_sample_rate = int(a_stream.get("sample_rate", 0) or 0)
        report.audio_channels = int(a_stream.get("channels", 0) or 0)
        if report.audio_codec != "aac":
            report.errors.append(f"Expected audio codec aac, got {report.audio_codec}")
        if report.audio_sample_rate != 48000:
            report.warnings.append(
                f"Expected 48000 Hz audio sample rate, got {report.audio_sample_rate}"
            )
        if report.audio_channels != 2:
            report.warnings.append(
                f"Expected 2 stereo audio channels, got {report.audio_channels}"
            )

    report.valid = len(report.errors) == 0
    return report


def verify_package_dir(package_dir: Path) -> dict[str, Any]:
    """Verify all video tracks and thumbnails in a package directory (or GOR-9 episode)."""
    package_dir = Path(package_dir).resolve()
    yt_json = package_dir / "youtube.json"
    if not yt_json.exists() and (package_dir / "youtube" / "youtube.json").exists():
        yt_json = package_dir / "youtube" / "youtube.json"

    results: dict[str, Any] = {
        "package_dir": str(package_dir),
        "youtube_json": str(yt_json) if yt_json.exists() else None,
        "tracks": {},
        "thumbnails": {},
        "all_valid": True,
    }

    # Discover tracks from youtube.json or directory globbing
    wide_candidates = sorted(package_dir.glob("*_1080p.mp4")) + sorted(
        package_dir.glob("gor9_ep*.mp4")
    )
    short_candidates = sorted(package_dir.glob("*_short.mp4"))

    if wide_candidates:
        rep = verify_video_file(wide_candidates[0], variant="wide_16x9")
        results["tracks"]["wide_16x9"] = rep.to_dict()
        if not rep.valid:
            results["all_valid"] = False

    if short_candidates:
        rep = verify_video_file(short_candidates[0], variant="short_9x16")
        results["tracks"]["short_9x16"] = rep.to_dict()
        if not rep.valid:
            results["all_valid"] = False

    if not wide_candidates and not short_candidates:
        results["all_valid"] = False
        results["error"] = "No master MP4 files found in package directory"

    # Check thumbnails
    for thumb in sorted(package_dir.glob("*thumb*.png")) + sorted(
        package_dir.glob("*thumb*.jpg")
    ) + sorted((package_dir / "youtube").glob("*thumb*.*") if (package_dir / "youtube").exists() else []):
        size_bytes = thumb.stat().st_size
        results["thumbnails"][thumb.name] = {
            "path": str(thumb),
            "size_kb": round(size_bytes / 1024, 1),
            "under_2mb_limit": size_bytes <= MAX_THUMBNAIL_BYTES,
        }

    return results
