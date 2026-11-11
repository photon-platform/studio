"""Zero-dependency CLI for PHOTON Studio (`studio stage|speak|capture|motion|render|verify|archive|broadcast`)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


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
    p_render = sub.add_parser("render", help="Render package (mode=slides|motion), verify with ffprobe, and clean scratch")
    p_render.add_argument("package_dir", type=Path)
    p_render.add_argument("--prefix", default=None)
    p_render.add_argument("--mode", choices=["slides", "motion"], default="slides")
    p_render.add_argument("--tts", action="store_true")
    p_render.add_argument("--keep-scratch", action="store_true")

    # 3. motion (Mode 2 package or standalone capture)
    p_motion = sub.add_parser("motion", help="Mode 2: Render motion.json/camera_path.json package or standalone 3D WebGL HTML")
    p_motion.add_argument("target", help="Package directory, motion.json/camera_path.json, or standalone HTML file")
    p_motion.add_argument("output_mp4", nargs="?", type=Path, default=None, help="Output MP4 path (when target is an HTML file)")
    p_motion.add_argument("--prefix", default=None)
    p_motion.add_argument("--width", type=int, default=2560)
    p_motion.add_argument("--height", type=int, default=1440)
    p_motion.add_argument("--duration", type=float, default=30.0)
    p_motion.add_argument("--fps", type=int, default=30)
    p_motion.add_argument("--warmup-frames", type=int, default=15)
    p_motion.add_argument("--scale-factor", type=float, default=1.0)
    p_motion.add_argument("--audio-wav", type=Path, default=None)
    p_motion.add_argument("--no-ambient-drone", action="store_true")
    p_motion.add_argument("--thumb-png", type=Path, default=None)
    p_motion.add_argument("--thumb-sec", type=float, default=None)
    p_motion.add_argument("--encode-only", action="store_true", help="Encode existing frames/ directory into MP4")
    p_motion.add_argument("--test-frames", action="store_true", help="Capture diagnostic test frames without encoding MP4")
    p_motion.add_argument("--clean", action="store_true", help="Clean existing frames/ before rendering")

    # 4. speak
    p_speak = sub.add_parser("speak", help="Parse script.scr/script.txt or synthesize multi-voice Gemini TTS audio")
    p_speak.add_argument("script_path", type=Path)
    p_speak.add_argument("-o", "--output-wav", type=Path, default=None)
    p_speak.add_argument("--prefix", default=None)
    p_speak.add_argument("--variant", choices=["16x9", "9x16"], default="16x9")

    # 5. capture
    p_cap = sub.add_parser("capture", help="Capture HTML or URL in isolated Chromium viewport")
    p_cap.add_argument("html_or_url")
    p_cap.add_argument("output_png", type=Path)
    p_cap.add_argument("--width", type=int, default=1920)
    p_cap.add_argument("--height", type=int, default=1080)
    p_cap.add_argument("--wait-ms", type=int, default=1500)

    # 5b. stage (Mode 1 Interactive Browser Stage & Live Server)
    p_stage = sub.add_parser("stage", help="Build interactive stage.html, render MP4s from stage.html, or serve live Stage")
    p_stage.add_argument("target_path", type=Path, help="Proposition folder (e.g. videos/book_i/01) or videos/ root")
    p_stage.add_argument("--build-only", action="store_true", help="Write stage.html without starting HTTP server")
    p_stage.add_argument("--render", action="store_true", help="Render per-segment clips & assemble 16:9 QHD and 9:16 Short MP4s")
    p_stage.add_argument("--section", default=None, help="Target a single section (e.g. construction or 02_construction) for regeneration")
    p_stage.add_argument("--force", action="store_true", help="Force regeneration of all sections ignoring cache")
    p_stage.add_argument("--port", type=int, default=8765)
    p_stage.add_argument("--no-open", action="store_true")

    # 6. archive
    p_arch = sub.add_parser("archive", help="Manage 3-tier media storage, scratch cleanup, and Google Drive sync")
    arch_sub = p_arch.add_subparsers(dest="archive_cmd", required=True)

    p_ascan = arch_sub.add_parser("scan", help="Report Tier 1 (Git), Tier 2 (Vault), and Tier 3 (Scratch) sizes")
    p_ascan.add_argument("root_dir", type=Path)

    p_aclean = arch_sub.add_parser("clean", help="Remove Tier 3 build scratch (stage.html, segments/, slides_*/, frames/)")
    p_aclean.add_argument("root_dir", type=Path)
    p_aclean.add_argument("--execute", action="store_true", help="Delete scratch (default is dry-run)")

    p_async = arch_sub.add_parser("sync", help="Sync Tier 2 master media with Google Drive via rclone")
    p_async.add_argument("local_dir", type=Path)
    p_async.add_argument("rel_subpath")
    p_async.add_argument("--direction", choices=["push", "pull"], default="push")
    p_async.add_argument("--dry-run", action="store_true")

    p_async_all = arch_sub.add_parser("sync-all", help="Sync Tier 2 master media across all projects (elements, etc, phyllotaxis, GOR-9)")
    p_async_all.add_argument("--direction", choices=["push", "pull"], default="push")
    p_async_all.add_argument("--dry-run", action="store_true")

    # 7. broadcast
    p_bcast = sub.add_parser("broadcast", help="Coordinate shared YouTube API quota, schedules, auth, and uploads")
    bcast_sub = p_bcast.add_subparsers(dest="broadcast_cmd", required=True)

    bcast_sub.add_parser("quota", help="Show shared Pacific-Time YouTube API quota status")

    p_bstat = bcast_sub.add_parser("status", help="Show cross-project upload, schedule, and repair status")
    p_bstat.add_argument("--project", default=None)

    p_brep = bcast_sub.add_parser("repair", help="Repair missing thumbnails/playlists on an uploaded package")
    p_brep.add_argument("package_dir", type=Path)

    p_bpub = bcast_sub.add_parser("publish", help="Mark ONLY the specified uploaded packages as PUBLIC")
    p_bpub.add_argument("package_dirs", nargs="+", type=Path)
    p_bpub.add_argument("--channel", default=None, help="Channel handle (@geometor or @GOR-9-station)")
    p_bpub.add_argument("--dry-run", action="store_true")

    p_bauth = bcast_sub.add_parser("auth", help="Run OAuth 2.0 authorization flow for @geometor or @GOR-9-station")
    p_bauth.add_argument("--channel", default="@geometor", help="Channel handle (@geometor or @GOR-9-station)")
    p_bauth.add_argument("--port", type=int, default=8085)

    p_bsched = bcast_sub.add_parser("schedule", help="Assign staggered publishAt slots across package directories")
    p_bsched.add_argument("package_dirs", nargs="+", type=Path)
    p_bsched.add_argument("--start", required=True, help="ISO-8601 UTC start time, e.g. 2026-11-10T08:00:00Z")
    p_bsched.add_argument("--step-hours", type=float, default=4.0)
    p_bsched.add_argument("--dry-run", action="store_true")

    bcast_sub.add_parser("upload", help="Run central YouTube Data API v3 uploader (--centers, --props, --topics, --replace, --sync-missing, --publish)")

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if len(raw_argv) >= 2 and raw_argv[0] == "broadcast" and raw_argv[1] == "upload":
        from .uploader import main as uploader_main

        uploader_main(raw_argv[2:])
        return 0

    args = parser.parse_args(argv)

    if args.command == "verify":
        from .verify import verify_package_dir

        res = verify_package_dir(args.package_dir)
        _print_json(res)
        return 0 if res.get("all_valid") else 1

    if args.command == "render":
        from .render import render_package

        res = render_package(
            args.package_dir,
            prefix=args.prefix,
            mode=args.mode,
            tts=args.tts,
            purge_scratch=not args.keep_scratch,
        )
        _print_json(res)
        return 0 if res.get("status") == "ok" else 1

    if args.command == "motion":
        from .motion import capture_motion_viewport, render_motion_package

        target_p = Path(args.target.replace("file://", ""))
        if args.output_mp4 is not None and target_p.suffix.lower() in (".html", ".htm"):
            res = capture_motion_viewport(
                url_or_html=args.target,
                output_mp4=args.output_mp4,
                width=args.width,
                height=args.height,
                duration_sec=args.duration,
                fps=args.fps,
                warmup_frames=args.warmup_frames,
                scale_factor=args.scale_factor,
                audio_wav=args.audio_wav,
                ambient_drone=not args.no_ambient_drone,
                thumb_png=args.thumb_png,
                thumb_sec=args.thumb_sec,
            )
        else:
            res = render_motion_package(
                target_p,
                prefix=args.prefix,
                encode_only=args.encode_only,
                test_frames_only=args.test_frames,
                clean=args.clean,
            )
        _print_json(res)
        return 0 if res.get("ok") else 1

    if args.command == "speak":
        from .speak import synthesize_script
        from .stage import infer_package_prefix

        scr_p = Path(args.script_path).resolve()
        out_dir = args.output_wav.resolve().parent if args.output_wav else scr_p.parent
        pfx = args.prefix or (args.output_wav.stem if args.output_wav else infer_package_prefix(scr_p.parent))
        wav_out, json_out = synthesize_script(scr_p, out_dir=out_dir, prefix=pfx, variant=args.variant)
        _print_json({"status": "ok", "wav": str(wav_out), "manifest": str(json_out), "prefix": pfx})
        return 0

    if args.command == "capture":
        from .capture import capture_html_viewport

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

    if args.command == "stage":
        from .stage import build_stage_html, render_stage_package, serve_stage

        if args.render or args.section:
            res = render_stage_package(
                args.target_path,
                target_section=args.section,
                force=args.force,
            )
            _print_json(res)
            return 0 if res.get("status") == "ok" else 1
        if args.build_only:
            out = build_stage_html(args.target_path)
            _print_json({"status": "ok", "stage_html": str(out)})
            return 0
        if (args.target_path / "script.scr").exists() or (args.target_path / "scenes.json").exists():
            build_stage_html(args.target_path)
        serve_stage(args.target_path, port=args.port, open_browser=not args.no_open)
        return 0

    if args.command == "archive":
        from .archive import (
            clean_scratch,
            scan_storage,
            sync_all_projects,
            sync_archive,
        )

        if args.archive_cmd == "scan":
            _print_json(scan_storage(args.root_dir))
        elif args.archive_cmd == "clean":
            _print_json(clean_scratch(args.root_dir, dry_run=not args.execute))
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
        return 0

    if args.command == "broadcast":
        from .broadcast import (
            authorize_channel,
            discover_all_packages,
            get_quota_status,
            publish_packages,
            repair_package,
            schedule_packages,
        )

        if args.broadcast_cmd == "quota":
            _print_json(get_quota_status())
        elif args.broadcast_cmd == "status":
            summary = discover_all_packages(project_filter=args.project)
            _print_json({k: v for k, v in summary.items() if k != "packages"})
        elif args.broadcast_cmd == "repair":
            _print_json(repair_package(args.package_dir))
        elif args.broadcast_cmd == "publish":
            _print_json(publish_packages(args.package_dirs, channel=args.channel, dry_run=args.dry_run))
        elif args.broadcast_cmd == "auth":
            _print_json(authorize_channel(channel=args.channel, port=args.port))
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
