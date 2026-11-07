"""Zero-dependency CLI for PHOTON Studio (`studio speak|capture|render|verify|archive|broadcast`)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .archive import (
    clean_scratch,
    generate_systemd_mount_unit,
    link_all_projects,
    link_to_vault,
    scan_storage,
    sync_all_projects,
    sync_archive,
)
from .broadcast import (
    discover_all_packages,
    get_quota_status,
    repair_package,
    schedule_packages,
)
from .capture import capture_html_viewport
from .render import render_package
from .speak import parse_unified_script, synthesize_script
from .verify import verify_package_dir


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2))


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="studio",
        description="PHOTON Studio: Media capture, 1440p QHD rendering, archiving, and YouTube broadcast.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # 1. verify
    p_verify = sub.add_parser("verify", help="Verify 16:9 (2560x1440 VP9) and 9:16 (1080x1920) masters")
    p_verify.add_argument("package_dir", type=Path)

    # 2. render
    p_render = sub.add_parser("render", help="Render package, verify with ffprobe, and clean scratch")
    p_render.add_argument("package_dir", type=Path)
    p_render.add_argument("--prefix", default=None)
    p_render.add_argument("--tts", action="store_true")
    p_render.add_argument("--keep-scratch", action="store_true")

    # 3. speak
    p_speak = sub.add_parser("speak", help="Parse script.txt or synthesize Argon III-C audio")
    p_speak.add_argument("script_path", type=Path)
    p_speak.add_argument("-o", "--output-wav", type=Path, default=None)

    # 4. capture
    p_cap = sub.add_parser("capture", help="Capture HTML or URL in isolated Chromium viewport")
    p_cap.add_argument("html_or_url")
    p_cap.add_argument("output_png", type=Path)
    p_cap.add_argument("--width", type=int, default=1920)
    p_cap.add_argument("--height", type=int, default=1080)
    p_cap.add_argument("--wait-ms", type=int, default=1500)

    # 5. archive
    p_arch = sub.add_parser("archive", help="Manage 3-tier media storage, scratch cleanup, and Google Drive sync")
    arch_sub = p_arch.add_subparsers(dest="archive_cmd", required=True)

    p_ascan = arch_sub.add_parser("scan", help="Report Tier 1 (Git), Tier 2 (Vault), and Tier 3 (Scratch) sizes")
    p_ascan.add_argument("root_dir", type=Path)

    p_aclean = arch_sub.add_parser("clean", help="Remove Tier 3 build scratch (*_stage.html, slides_*/, frames_*/)")
    p_aclean.add_argument("root_dir", type=Path)
    p_aclean.add_argument("--execute", action="store_true", help="Delete scratch (default is dry-run)")

    p_amount = arch_sub.add_parser("mount-unit", help="Generate systemd --user mount service for /home/phi/MEDIA")
    p_amount.add_argument("--write", action="store_true")

    p_async = arch_sub.add_parser("sync", help="Sync Tier 2 master media with Google Drive via rclone")
    p_async.add_argument("local_dir", type=Path)
    p_async.add_argument("rel_subpath")
    p_async.add_argument("--direction", choices=["push", "pull"], default="push")
    p_async.add_argument("--dry-run", action="store_true")

    p_async_all = arch_sub.add_parser("sync-all", help="Sync Tier 2 master media across all 4 projects (elements, etc, phyllotaxis, GOR-9)")
    p_async_all.add_argument("--direction", choices=["push", "pull"], default="push")
    p_async_all.add_argument("--dry-run", action="store_true")

    p_alink = arch_sub.add_parser("link", help="Move Tier 2 binaries to /home/phi/MEDIA and replace local files with symlinks")
    p_alink.add_argument("local_dir", type=Path)
    p_alink.add_argument("rel_subpath")
    p_alink.add_argument("--execute", action="store_true", help="Perform symlink conversion (default is dry-run)")

    p_alink_all = arch_sub.add_parser("link-all", help="Move Tier 2 binaries across all 4 projects to /home/phi/MEDIA and replace with symlinks")
    p_alink_all.add_argument("--execute", action="store_true", help="Perform symlink conversion (default is dry-run)")

    # 6. broadcast
    p_bcast = sub.add_parser("broadcast", help="Coordinate shared YouTube API quota, schedules, and uploads")
    bcast_sub = p_bcast.add_subparsers(dest="broadcast_cmd", required=True)

    bcast_sub.add_parser("quota", help="Show shared Pacific-Time YouTube API quota status")

    p_bstat = bcast_sub.add_parser("status", help="Show cross-project upload, schedule, and repair status")
    p_bstat.add_argument("--project", default=None)

    p_brep = bcast_sub.add_parser("repair", help="Repair missing thumbnails/playlists on an uploaded package")
    p_brep.add_argument("package_dir", type=Path)

    p_bsched = bcast_sub.add_parser("schedule", help="Assign staggered publishAt slots across package directories")
    p_bsched.add_argument("package_dirs", nargs="+", type=Path)
    p_bsched.add_argument("--start", required=True, help="ISO-8601 UTC start time, e.g. 2026-11-10T08:00:00Z")
    p_bsched.add_argument("--step-hours", type=float, default=4.0)
    p_bsched.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "verify":
        res = verify_package_dir(args.package_dir)
        _print_json(res)
        return 0 if res.get("all_valid") else 1

    if args.command == "render":
        res = render_package(
            args.package_dir,
            prefix=args.prefix,
            tts=args.tts,
            purge_scratch=not args.keep_scratch,
        )
        _print_json(res)
        return 0 if res.get("status") == "ok" else 1

    if args.command == "speak":
        if args.output_wav:
            _print_json(synthesize_script(args.script_path, args.output_wav))
        else:
            _print_json(parse_unified_script(args.script_path))
        return 0

    if args.command == "capture":
        _print_json(
            capture_html_viewport(
                args.html_or_url,
                args.output_png,
                width=args.width,
                height=args.height,
                wait_ms=args.wait_ms,
            )
        )
        return 0

    if args.command == "archive":
        if args.archive_cmd == "scan":
            _print_json(scan_storage(args.root_dir))
        elif args.archive_cmd == "clean":
            _print_json(clean_scratch(args.root_dir, dry_run=not args.execute))
        elif args.archive_cmd == "mount-unit":
            _print_json(generate_systemd_mount_unit(write_unit=args.write))
        elif args.archive_cmd == "sync":
            _print_json(
                sync_archive(
                    args.local_dir,
                    args.rel_subpath,
                    direction=args.direction,
                    dry_run=args.dry_run,
                )
            )
        elif args.archive_cmd == "sync-all":
            _print_json(
                sync_all_projects(
                    direction=args.direction,
                    dry_run=args.dry_run,
                )
            )
        elif args.archive_cmd == "link":
            _print_json(
                link_to_vault(
                    args.local_dir,
                    args.rel_subpath,
                    dry_run=not args.execute,
                )
            )
        elif args.archive_cmd == "link-all":
            _print_json(
                link_all_projects(
                    dry_run=not args.execute,
                )
            )
        return 0

    if args.command == "broadcast":
        if args.broadcast_cmd == "quota":
            _print_json(get_quota_status())
        elif args.broadcast_cmd == "status":
            summary = discover_all_packages(project_filter=args.project)
            _print_json({k: v for k, v in summary.items() if k != "packages"})
        elif args.broadcast_cmd == "repair":
            _print_json(repair_package(args.package_dir))
        elif args.broadcast_cmd == "schedule":
            _print_json(
                schedule_packages(
                    args.package_dirs,
                    start_iso=args.start,
                    step_hours=args.step_hours,
                    dry_run=args.dry_run,
                )
            )
        return 0

    return 0


def run() -> None:
    raise SystemExit(cli())


if __name__ == "__main__":
    run()
