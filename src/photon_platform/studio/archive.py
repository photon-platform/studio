"""3-Tier Media Storage Manager: Scratch cleanup and Google Drive rclone vault sync."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from typing import Any


DEFAULT_MEDIA_MOUNT = Path("/home/phi/MEDIA")
DEFAULT_GDRIVE_REMOTE = "gdrive:PHOTON-MEDIA"
DEFAULT_FILTER_FILE = Path.home() / ".config" / "geometor" / "rclone_media.filter"

RCLONE_FILTER_CONTENT = """# PHOTON Studio Tier 2 Master Media Filter for rclone
# Exclude Tier 3 Ephemeral Build Scratch
- **/slides_16x9/**
- **/slides_9x16/**
- **/frames_cold_open_3d/**
- **/slides_cold_open_3d/**
- **/previews/**
- **/*_work/**
- **/*_stage.html
- **/*_16x9_*.png
- **/*_9x16_*.png
- **/_*_concat.txt
- **/__pycache__/**

# Include Tier 2 Master Media & Reusable Generative Assets
+ **/*.mp4
+ **/*.mov
+ **/*.wav
+ **/*thumb*.jpg
+ **/*thumb*.png
+ **/clips/**
+ **/plates/**
+ **/console_plates/**
+ **/youtube.json

# Exclude everything else (Tier 1 text/code already tracked in Git)
- *
"""

SCRATCH_DIRS = (
    "slides_16x9",
    "slides_9x16",
    "frames_cold_open_3d",
    "slides_cold_open_3d",
    "previews",
)


def classify_file(path: Path) -> str:
    """Classify a file inside a video/episode folder into tier1_git, tier2_vault, or tier3_scratch."""
    parts = set(path.parts)
    name = path.name
    for d in SCRATCH_DIRS:
        if d in parts or name.endswith("_work"):
            return "tier3_scratch"
    if (
        name.endswith("_stage.html")
        or (name.startswith("_") and name.endswith("_concat.txt"))
        or ("_16x9_" in name and name.endswith(".png") and "thumb" not in name)
        or ("_9x16_" in name and name.endswith(".png") and "thumb" not in name)
    ):
        return "tier3_scratch"

    if (
        name.endswith((".mp4", ".mov", ".wav"))
        or "thumb" in name
        or "clips" in parts
        or "plates" in parts
        or "console_plates" in parts
    ):
        return "tier2_vault"

    return "tier1_git"


def scan_storage(root_dir: Path) -> dict[str, Any]:
    """Scan a directory tree and return byte/MB totals for Tier 1, Tier 2, and Tier 3."""
    root_dir = Path(root_dir).resolve()
    totals = {"tier1_git": 0, "tier2_vault": 0, "tier3_scratch": 0}
    counts = {"tier1_git": 0, "tier2_vault": 0, "tier3_scratch": 0}

    if not root_dir.exists():
        return {"root": str(root_dir), "exists": False}

    for p in root_dir.rglob("*"):
        if not p.is_file():
            continue
        size = p.stat().st_size
        tier = classify_file(p)
        totals[tier] += size
        counts[tier] += 1

    return {
        "root": str(root_dir),
        "exists": True,
        "tier1_git_mb": round(totals["tier1_git"] / (1024 * 1024), 2),
        "tier2_vault_mb": round(totals["tier2_vault"] / (1024 * 1024), 2),
        "tier3_scratch_mb": round(totals["tier3_scratch"] / (1024 * 1024), 2),
        "total_mb": round(sum(totals.values()) / (1024 * 1024), 2),
        "counts": counts,
    }


def _package_has_rendered_mp4(pkg_dir: Path) -> bool:
    """Return True if the package folder already has its rendered .mp4 master(s)."""
    return any(pkg_dir.glob("*.mp4"))


def clean_scratch(
    root_dir: Path,
    dry_run: bool = True,
    require_mp4: bool = True,
) -> dict[str, Any]:
    """Remove Tier 3 build scratch (slides_*/, frames_*/, *_stage.html, previews/).

    If require_mp4 is True, only cleans scratch inside package folders that already
    have their rendered .mp4 master(s).
    """
    root_dir = Path(root_dir).resolve()
    reclaimed_bytes = 0
    removed_paths: list[str] = []
    skipped_unrendered: list[str] = []

    # 1. Remove scratch directories
    for dname in SCRATCH_DIRS:
        for dpath in sorted(root_dir.rglob(dname)):
            if not dpath.is_dir():
                continue
            pkg_dir = dpath.parent
            if require_mp4 and not _package_has_rendered_mp4(pkg_dir):
                skipped_unrendered.append(str(dpath))
                continue
            dir_bytes = sum(f.stat().st_size for f in dpath.rglob("*") if f.is_file())
            reclaimed_bytes += dir_bytes
            removed_paths.append(str(dpath))
            if not dry_run:
                shutil.rmtree(dpath, ignore_errors=True)

    # 2. Remove individual scratch files (*_stage.html, _*_concat.txt, *_16x9_*.png) outside SCRATCH_DIRS
    for fpath in sorted(root_dir.rglob("*")):
        if not fpath.is_file():
            continue
        if any(d in fpath.parts for d in SCRATCH_DIRS) or any(part.endswith("_work") for part in fpath.parts[:-1]):
            continue
        if classify_file(fpath) != "tier3_scratch":
            continue
        pkg_dir = fpath.parent
        if require_mp4 and not _package_has_rendered_mp4(pkg_dir):
            skipped_unrendered.append(str(fpath))
            continue
        fsize = fpath.stat().st_size
        reclaimed_bytes += fsize
        removed_paths.append(str(fpath))
        if not dry_run:
            fpath.unlink(missing_ok=True)

    return {
        "root": str(root_dir),
        "dry_run": dry_run,
        "reclaimed_mb": round(reclaimed_bytes / (1024 * 1024), 2),
        "removed_count": len(removed_paths),
        "removed_sample": removed_paths[:25],
        "skipped_unrendered_count": len(skipped_unrendered),
    }


def ensure_rclone_filter(filter_path: Path = DEFAULT_FILTER_FILE) -> Path:
    """Write the canonical rclone Tier 2 media filter file."""
    filter_path = Path(filter_path)
    filter_path.parent.mkdir(parents=True, exist_ok=True)
    filter_path.write_text(RCLONE_FILTER_CONTENT, encoding="utf-8")
    return filter_path


def generate_systemd_mount_unit(
    remote: str = DEFAULT_GDRIVE_REMOTE,
    mount_dir: Path = DEFAULT_MEDIA_MOUNT,
    write_unit: bool = False,
) -> dict[str, Any]:
    """Generate a systemd --user service unit for mounting Google Drive at /home/phi/MEDIA."""
    rclone_bin = shutil.which("rclone") or str(Path.home() / ".local" / "bin" / "rclone")
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_path = unit_dir / "photon-media-mount.service"
    content = f"""[Unit]
Description=PHOTON Studio Media Vault (rclone Google Drive mount at {mount_dir})
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStartPre=/usr/bin/mkdir -p {mount_dir}
ExecStart={rclone_bin} mount {remote} {mount_dir} \\
    --vfs-cache-mode full \\
    --vfs-cache-max-size 50G \\
    --vfs-cache-max-age 168h \\
    --dir-cache-time 5m \\
    --transfers 4
ExecStop=/bin/fusermount -u {mount_dir}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""
    if write_unit:
        unit_dir.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(content, encoding="utf-8")
        ensure_rclone_filter()
        mount_dir.mkdir(parents=True, exist_ok=True)

    return {
        "unit_path": str(unit_path),
        "mount_dir": str(mount_dir),
        "remote": remote,
        "rclone_bin": rclone_bin,
        "written": write_unit,
        "content": content,
        "enable_command": "systemctl --user daemon-reload && systemctl --user enable --now photon-media-mount.service",
    }


def sync_archive(
    local_dir: Path,
    rel_subpath: str,
    direction: str = "push",
    remote: str = DEFAULT_GDRIVE_REMOTE,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Sync Tier 2 master media between local_dir and Google Drive (or /home/phi/MEDIA)."""
    rclone_bin = shutil.which("rclone") or str(Path.home() / ".local" / "bin" / "rclone")
    filter_file = ensure_rclone_filter()
    local_dir = Path(local_dir).resolve()
    remote_target = f"{remote.rstrip('/')}/{rel_subpath.strip('/')}"

    if not Path(rclone_bin).exists():
        return {
            "status": "error",
            "error": "rclone is not installed yet.",
            "filter_file": str(filter_file),
            "planned_remote": remote_target,
        }

    if direction == "push":
        src, dst = str(local_dir), remote_target
    elif direction == "pull":
        src, dst = remote_target, str(local_dir)
    else:
        raise ValueError(f"Unsupported direction: {direction}")

    cmd = [
        rclone_bin,
        "copy",
        src,
        dst,
        "--filter-from",
        str(filter_file),
        "--transfers",
        "4",
        "--stats-one-line",
    ]
    if dry_run:
        cmd.append("--dry-run")

    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return {
        "status": "ok" if res.returncode == 0 else "error",
        "returncode": res.returncode,
        "command": cmd,
        "stdout": res.stdout.strip(),
        "stderr": res.stderr.strip(),
    }


PROJECT_ARCHIVE_TARGETS: list[tuple[str, Path, str]] = [
    ("elements", Path("/home/phi/PROJECTS/geometor/elements/videos"), "geometor/elements/videos"),
    ("etc", Path("/home/phi/PROJECTS/geometor/etc/videos"), "geometor/etc/videos"),
    ("phyllotaxis", Path("/home/phi/PROJECTS/geometor/phyllotaxis/videos"), "geometor/phyllotaxis/videos"),
    ("GOR-9", Path("/home/phi/PROJECTS/phiarchitect/GOR-9/episodes"), "phiarchitect/GOR-9/episodes"),
]


def sync_all_projects(
    direction: str = "push",
    remote: str = DEFAULT_GDRIVE_REMOTE,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Sync Tier 2 master media across all 4 projects (elements, etc, phyllotaxis, GOR-9)."""
    results: dict[str, Any] = {}
    all_ok = True
    for name, local_dir, rel_subpath in PROJECT_ARCHIVE_TARGETS:
        if not local_dir.exists():
            continue
        res = sync_archive(
            local_dir=local_dir,
            rel_subpath=rel_subpath,
            direction=direction,
            remote=remote,
            dry_run=dry_run,
        )
        results[name] = res
        if res.get("status") != "ok":
            all_ok = False
    return {
        "status": "ok" if all_ok else "error",
        "direction": direction,
        "remote": remote,
        "dry_run": dry_run,
        "projects": results,
    }

