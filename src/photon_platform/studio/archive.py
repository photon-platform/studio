"""3-Tier Storage & Google Drive Archive Manager (No Symlinks).

Tier 1 (Git-Tracked Source):
  - script.scr, script.txt, *.svg, *.srt, *.vtt, youtube.json, youtube.txt
Tier 2 (Local Git-Ignored Master Media, backed up via rclone to gdrive:PHOTON-MEDIA):
  - *_1080p.mp4 (2560x1440 QHD), *_short.mp4 (1080x1920), *.wav, *_thumb.png, *_thumb.jpg
Tier 3 (Ephemeral Build Scratch — Purged after verification):
  - *_stage.html, slides_16x9/, slides_9x16/, frames_*/, _raw_*.png
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


DEFAULT_RCLONE_REMOTE = "gdrive:PHOTON-MEDIA"

TIER1_EXTENSIONS = {".scr", ".txt", ".json", ".srt", ".vtt", ".svg", ".md", ".py", ".rst"}
TIER2_EXTENSIONS = {".mp4", ".wav", ".flac", ".m4a", ".mov", ".mkv"}
SCRATCH_DIR_PREFIXES = ("slides_", "frames_", "_tmp_", "work_")

PROJECT_ARCHIVE_MAP: dict[str, tuple[Path, str]] = {
    "elements": (Path("/home/phi/PROJECTS/geometor/elements/videos"), "geometor/elements/videos"),
    "etc": (Path("/home/phi/PROJECTS/geometor/etc/videos"), "geometor/etc/videos"),
    "phyllotaxis": (Path("/home/phi/PROJECTS/geometor/phyllotaxis/videos"), "geometor/phyllotaxis/videos"),
    "GOR-9": (Path("/home/phi/PROJECTS/phiarchitect/GOR-9/episodes"), "phiarchitect/GOR-9/episodes"),
}


def classify_file(path: Path) -> str:
    """Classify a file into 'tier1_git', 'tier2_master', or 'tier3_scratch'."""
    name = path.name
    suffix = path.suffix.lower()

    for parent in path.parents:
        if any(parent.name.startswith(pref) for pref in SCRATCH_DIR_PREFIXES):
            return "tier3_scratch"

    if name.endswith("_stage.html") or name.startswith("_raw_"):
        return "tier3_scratch"

    if suffix in TIER2_EXTENSIONS:
        return "tier2_master"

    if suffix in (".png", ".jpg", ".jpeg") and "thumb" in name.lower():
        return "tier2_master"

    if suffix in TIER1_EXTENSIONS:
        return "tier1_git"

    return "tier3_scratch"


def scan_storage(root_dir: Path) -> dict[str, Any]:
    """Scan a directory tree and report byte totals across Tier 1, Tier 2, and Tier 3."""
    root_dir = Path(root_dir).resolve()
    totals = {"tier1_git": 0, "tier2_master": 0, "tier3_scratch": 0}
    counts = {"tier1_git": 0, "tier2_master": 0, "tier3_scratch": 0}
    scratch_items: list[str] = []

    if not root_dir.exists():
        return {"error": f"Path does not exist: {root_dir}"}

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dpath = Path(dirpath)
        for dname in list(dirnames):
            if any(dname.startswith(pref) for pref in SCRATCH_DIR_PREFIXES):
                sdir = dpath / dname
                size = sum(f.stat().st_size for f in sdir.rglob("*") if f.is_file() and not f.is_symlink())
                totals["tier3_scratch"] += size
                counts["tier3_scratch"] += 1
                scratch_items.append(str(sdir))
                dirnames.remove(dname)

        for fname in filenames:
            fpath = dpath / fname
            if fpath.is_symlink() or not fpath.is_file():
                continue
            tier = classify_file(fpath)
            fsize = fpath.stat().st_size
            totals[tier] += fsize
            counts[tier] += 1
            if tier == "tier3_scratch":
                scratch_items.append(str(fpath))

    return {
        "root_dir": str(root_dir),
        "bytes": totals,
        "megabytes": {k: round(v / (1024 * 1024), 2) for k, v in totals.items()},
        "counts": counts,
        "scratch_paths": scratch_items,
    }


def clean_scratch(root_dir: Path, dry_run: bool = True, require_mp4: bool = True) -> dict[str, Any]:
    """Purge Tier 3 build scratch (`*_stage.html`, `slides_*/`, `frames_*/`)."""
    root_dir = Path(root_dir).resolve()
    removed: list[str] = []
    skipped: list[str] = []
    freed_bytes = 0

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dpath = Path(dirpath)
        has_mp4 = any(f.endswith(".mp4") for f in filenames)

        for dname in list(dirnames):
            if any(dname.startswith(pref) for pref in SCRATCH_DIR_PREFIXES):
                sdir = dpath / dname
                if require_mp4 and not has_mp4:
                    skipped.append(str(sdir))
                else:
                    size = sum(f.stat().st_size for f in sdir.rglob("*") if f.is_file() and not f.is_symlink())
                    freed_bytes += size
                    removed.append(str(sdir))
                    if not dry_run:
                        shutil.rmtree(sdir, ignore_errors=True)
                dirnames.remove(dname)

        for fname in filenames:
            if fname.endswith("_stage.html") or fname.startswith("_raw_"):
                fpath = dpath / fname
                if require_mp4 and not has_mp4:
                    skipped.append(str(fpath))
                else:
                    freed_bytes += fpath.stat().st_size
                    removed.append(str(fpath))
                    if not dry_run:
                        fpath.unlink(missing_ok=True)

    return {
        "root_dir": str(root_dir),
        "dry_run": dry_run,
        "removed_count": len(removed),
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "removed": removed,
        "skipped": skipped,
    }


def sync_archive(
    local_dir: Path,
    rel_subpath: str,
    direction: str = "push",
    remote_root: str = DEFAULT_RCLONE_REMOTE,
    transfers: int = 4,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Push or pull git-ignored Tier 2 master media directly between the project folder and Google Drive via rclone copy.

    Never creates symlinks; files remain real local files in the repository.
    """
    rclone_bin = shutil.which("rclone") or "/home/phi/.local/bin/rclone"
    if not Path(rclone_bin).exists():
        return {"status": "error", "error": "rclone is not installed."}

    local_dir = Path(local_dir).resolve()
    remote_target = f"{remote_root.rstrip('/')}/{rel_subpath.strip('/')}"

    if direction == "push":
        src, dst = str(local_dir), remote_target
    elif direction == "pull":
        src, dst = remote_target, str(local_dir)
    else:
        raise ValueError(f"Invalid direction: {direction}")

    cmd = [
        rclone_bin,
        "copy",
        src,
        dst,
        "--include",
        "*.{mp4,wav,flac,png,jpg}",
        "--exclude",
        "slides_*/**",
        "--exclude",
        "frames_*/**",
        "--exclude",
        ".tts_cache/**",
        "--transfers",
        str(transfers),
    ]
    if dry_run:
        cmd.append("--dry-run")

    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return {
        "status": "ok" if res.returncode == 0 else "error",
        "returncode": res.returncode,
        "command": " ".join(cmd),
        "stdout": res.stdout.strip(),
        "stderr": res.stderr.strip(),
    }


def sync_all_projects(
    direction: str = "push",
    remote_root: str = DEFAULT_RCLONE_REMOTE,
    transfers: int = 4,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Sync Tier 2 master media across all active projects directly with Google Drive (no symlinks)."""
    results: dict[str, Any] = {}
    for proj_name, (local_dir, rel_subpath) in PROJECT_ARCHIVE_MAP.items():
        if not local_dir.exists() and direction == "push":
            results[proj_name] = {"status": "skipped", "reason": f"Missing {local_dir}"}
            continue
        local_dir.mkdir(parents=True, exist_ok=True)
        results[proj_name] = sync_archive(
            local_dir=local_dir,
            rel_subpath=rel_subpath,
            direction=direction,
            remote_root=remote_root,
            transfers=transfers,
            dry_run=dry_run,
        )
    return {
        "direction": direction,
        "remote_root": remote_root,
        "dry_run": dry_run,
        "projects": results,
    }
