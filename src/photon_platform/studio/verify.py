"""FFprobe verification gatekeeper for YouTube 1440p QHD (VP9) and 9:16 Vertical Shorts masters."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
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
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(mp4_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {mp4_path}: {res.stderr.strip()}")
    return json.loads(res.stdout)


def verify_video_file(mp4_path: Path, variant: str = "wide_16x9") -> StreamReport:
    """Validate a video file against the PHOTON Studio YouTube master specification.

    - wide_16x9: Must be 2560x1440 (1440p QHD) to trigger YouTube's vp09 (VP9) hardware pipeline,
      h264 codec, yuv420p, 30fps CFR, and AAC audio.
    - short_9x16: Must be 1080x1920 (FHD Vertical), h264 codec, yuv420p, 30fps CFR,
      and AAC audio.
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
    if report.pix_fmt not in ("yuv420p", "yuv444p"):
        report.warnings.append(f"Unexpected pix_fmt {report.pix_fmt}")
    if report.r_frame_rate not in ("30/1", "30000/1001", "25/1"):
        report.warnings.append(f"Expected 25fps or 30fps r_frame_rate, got {report.r_frame_rate}")

    if not a_stream:
        report.errors.append("Missing audio stream")
    else:
        report.audio_codec = a_stream.get("codec_name", "")
        report.audio_sample_rate = int(a_stream.get("sample_rate", 0) or 0)
        report.audio_channels = int(a_stream.get("channels", 0) or 0)
        if report.audio_codec != "aac":
            report.errors.append(f"Expected audio codec aac, got {report.audio_codec}")

    report.valid = len(report.errors) == 0
    return report


def _resolve_pkg_file(package_dir: Path, raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    p = Path(raw_path)
    if p.exists():
        return p.resolve()
    cand = package_dir / p.name
    if cand.exists():
        return cand.resolve()
    cand_rel = package_dir / p
    if cand_rel.exists():
        return cand_rel.resolve()
    return None


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        rp = p.resolve()
        if rp not in seen and rp.exists():
            seen.add(rp)
            out.append(rp)
    return out


def verify_package_dir(package_dir: Path) -> dict[str, Any]:
    """Verify all video tracks, thumbnails, segment status, and frame tear status in a package directory."""
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

    wide_raw: list[Path] = []
    short_raw: list[Path] = []

    if yt_json.exists():
        try:
            yt_data = json.loads(yt_json.read_text(encoding="utf-8"))
            files_cfg = yt_data.get("files") or {}
            w_p = _resolve_pkg_file(package_dir, files_cfg.get("wide_1080p_mp4") or yt_data.get("video_file"))
            s_p = _resolve_pkg_file(package_dir, files_cfg.get("short_mp4"))
            if w_p:
                wide_raw.append(w_p)
            if s_p:
                short_raw.append(s_p)
        except Exception:
            pass

    for spec_name in ("motion.json", "camera_path.json"):
        spec_file = package_dir / spec_name
        if spec_file.exists():
            try:
                s_data = json.loads(spec_file.read_text(encoding="utf-8"))
                w_p = _resolve_pkg_file(package_dir, s_data.get("wide_mp4") or s_data.get("mp4_name"))
                s_p = _resolve_pkg_file(package_dir, s_data.get("short_mp4"))
                if w_p:
                    wide_raw.append(w_p)
                if s_p:
                    short_raw.append(s_p)
            except Exception:
                pass

    wide_raw.extend(sorted(package_dir.glob("*_1080p.mp4")))
    wide_raw.extend(sorted(package_dir.glob("gor9_*.mp4")))
    wide_raw.extend(sorted(package_dir.glob("phyllotaxis_*_1080p.mp4")))
    short_raw.extend(sorted(package_dir.glob("*_short.mp4")))
    short_raw.extend(sorted(package_dir.glob("phyllotaxis_*_short.mp4")))

    wide_candidates = _dedupe_paths([p for p in wide_raw if not p.name.endswith("_short.mp4")])
    short_candidates = _dedupe_paths(short_raw)

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
    thumb_candidates = (
        sorted(package_dir.glob("*thumb*.png"))
        + sorted(package_dir.glob("*thumb*.jpg"))
        + sorted((package_dir / "youtube").glob("*thumb*.*") if (package_dir / "youtube").exists() else [])
    )
    for thumb in _dedupe_paths(thumb_candidates):
        size_bytes = thumb.stat().st_size
        results["thumbnails"][thumb.name] = {
            "path": str(thumb),
            "size_kb": round(size_bytes / 1024, 1),
            "under_2mb_limit": size_bytes <= MAX_THUMBNAIL_BYTES,
        }

    # Optional Mode 1 segment status & Mode 2 frame tear audit
    if (package_dir / "segments").is_dir():
        try:
            from .stage import get_package_status

            results["segments_status"] = get_package_status(package_dir)
        except Exception as exc:
            results["segments_status_error"] = str(exc)

    if (package_dir / "frames").is_dir():
        try:
            from .motion import purge_torn_frames

            torn = purge_torn_frames(package_dir / "frames", mode=package_dir.name, audit_only=True)
            results["torn_frames_count"] = len(torn)
            if torn:
                results["torn_frame_indices"] = torn[:25]
        except Exception as exc:
            results["torn_frames_error"] = str(exc)

    return results
