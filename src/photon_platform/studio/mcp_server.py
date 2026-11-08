"""MCP server for PHOTON Studio (`studio-mcp`).

Supports both `mcp.server.fastmcp.FastMCP` (when installed) and built-in zero-dependency
JSON-RPC 2.0 stdio transport on Python 3.13.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Callable

from .archive import (
    clean_scratch,
    scan_storage,
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


def studio_quota_status() -> dict[str, Any]:
    """Return today's shared Pacific-Time YouTube API quota usage, remaining units, and reset countdown."""
    return get_quota_status()


def studio_broadcast_status(project: str | None = None) -> dict[str, Any]:
    """Inspect upload, schedule, and repair status across elements, etc, phyllotaxis, and GOR-9."""
    summary = discover_all_packages(project_filter=project)
    return {
        "total_packages": summary["total_packages"],
        "by_state": summary["by_state"],
        "by_project": summary["by_project"],
        "repairs_needed": summary["repairs_needed"],
    }


def studio_broadcast_repair(package_dir: str) -> dict[str, Any]:
    """Backfill missing custom thumbnails or metadata on an already-uploaded package (costs 50 units)."""
    return repair_package(Path(package_dir))


def studio_broadcast_schedule(
    package_dirs: list[str],
    start_iso: str,
    step_hours: float = 4.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Assign staggered publishAt timestamps across package directories."""
    return schedule_packages(
        [Path(p) for p in package_dirs],
        start_iso=start_iso,
        step_hours=step_hours,
        dry_run=dry_run,
    )


def studio_verify(package_dir: str) -> dict[str, Any]:
    """Validate 16:9 (2560x1440 VP9) and 9:16 (1080x1920) MP4 streams and thumbnails with ffprobe."""
    return verify_package_dir(Path(package_dir))


def studio_render(
    package_dir: str,
    prefix: str | None = None,
    mode: str = "slides",
    tts: bool = False,
    purge_scratch: bool = True,
) -> dict[str, Any]:
    """Render a package directory (mode='slides' or 'motion') to 2560x1440 QHD + 1080x1920 Short, verify with ffprobe, and clean scratch."""
    return render_package(
        Path(package_dir),
        prefix=prefix,
        mode=mode,
        tts=tts,
        purge_scratch=purge_scratch,
    )


def studio_speak(script_path: str, output_wav: str | None = None) -> dict[str, Any]:
    """Parse unified script.txt and optionally synthesize Argon III-C audio (.wav + .json manifest)."""
    sp = Path(script_path)
    if output_wav:
        return synthesize_script(sp, Path(output_wav))
    return parse_unified_script(sp)


def studio_capture(
    html_or_url: str,
    output_png: str,
    width: int = 1920,
    height: int = 1080,
    wait_ms: int = 1500,
) -> dict[str, Any]:
    """Capture an HTML slide or local console URL in an isolated Chromium viewport to exact pixel dimensions."""
    return capture_html_viewport(
        html_or_url=html_or_url,
        output_png=Path(output_png),
        width=width,
        height=height,
        wait_ms=wait_ms,
    )


def studio_archive_scan(root_dir: str) -> dict[str, Any]:
    """Scan a directory and report Tier 1 (Git), Tier 2 (Master Vault), and Tier 3 (Scratch) sizes in MB."""
    return scan_storage(Path(root_dir))


def studio_archive_clean(root_dir: str, dry_run: bool = True) -> dict[str, Any]:
    """Clean Tier 3 build scratch (*_stage.html, slides_*/, frames_*/) inside rendered packages."""
    return clean_scratch(Path(root_dir), dry_run=dry_run, require_mp4=True)


def studio_archive_sync(
    local_dir: str,
    rel_subpath: str,
    direction: str = "push",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Sync Tier 2 master media (.mp4, .wav, thumbnails, clips, plates) with Google Drive via rclone."""
    return sync_archive(
        local_dir=Path(local_dir),
        rel_subpath=rel_subpath,
        direction=direction,
        dry_run=dry_run,
    )


TOOL_REGISTRY: dict[str, tuple[Callable[..., Any], dict[str, Any]]] = {
    "studio_quota_status": (
        studio_quota_status,
        {
            "description": "Return today's shared Pacific-Time YouTube API quota usage, remaining units, and reset countdown.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ),
    "studio_broadcast_status": (
        studio_broadcast_status,
        {
            "description": "Inspect upload, schedule, and repair status across elements, etc, phyllotaxis, and GOR-9.",
            "inputSchema": {
                "type": "object",
                "properties": {"project": {"type": "string", "description": "Optional project filter (elements, etc, GOR-9)"}},
            },
        },
    ),
    "studio_broadcast_repair": (
        studio_broadcast_repair,
        {
            "description": "Backfill missing custom thumbnails or metadata on an already-uploaded package (costs 50 units).",
            "inputSchema": {
                "type": "object",
                "properties": {"package_dir": {"type": "string", "description": "Absolute path to package directory"}},
                "required": ["package_dir"],
            },
        },
    ),
    "studio_broadcast_schedule": (
        studio_broadcast_schedule,
        {
            "description": "Assign staggered publishAt timestamps across package directories.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "package_dirs": {"type": "array", "items": {"type": "string"}},
                    "start_iso": {"type": "string", "description": "ISO-8601 UTC start timestamp (e.g. 2026-11-10T08:00:00Z)"},
                    "step_hours": {"type": "number", "default": 4.0},
                    "dry_run": {"type": "boolean", "default": False},
                },
                "required": ["package_dirs", "start_iso"],
            },
        },
    ),
    "studio_verify": (
        studio_verify,
        {
            "description": "Validate 16:9 (2560x1440 VP9) and 9:16 (1080x1920) MP4 streams and thumbnails with ffprobe.",
            "inputSchema": {
                "type": "object",
                "properties": {"package_dir": {"type": "string"}},
                "required": ["package_dir"],
            },
        },
    ),
    "studio_render": (
        studio_render,
        {
            "description": "Render a package directory (mode='slides' or 'motion') to 2560x1440 QHD + 1080x1920 Short, verify with ffprobe, and clean scratch.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "package_dir": {"type": "string"},
                    "prefix": {"type": "string"},
                    "mode": {"type": "string", "enum": ["slides", "motion"], "default": "slides"},
                    "tts": {"type": "boolean", "default": False},
                    "purge_scratch": {"type": "boolean", "default": True},
                },
                "required": ["package_dir"],
            },
        },
    ),
    "studio_speak": (
        studio_speak,
        {
            "description": "Parse unified script.txt and optionally synthesize Argon III-C audio (.wav + .json manifest).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "script_path": {"type": "string"},
                    "output_wav": {"type": "string"},
                },
                "required": ["script_path"],
            },
        },
    ),
    "studio_capture": (
        studio_capture,
        {
            "description": "Capture an HTML slide or local console URL in an isolated Chromium viewport to exact pixel dimensions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "html_or_url": {"type": "string"},
                    "output_png": {"type": "string"},
                    "width": {"type": "integer", "default": 1920},
                    "height": {"type": "integer", "default": 1080},
                    "wait_ms": {"type": "integer", "default": 1500},
                },
                "required": ["html_or_url", "output_png"],
            },
        },
    ),
    "studio_archive_scan": (
        studio_archive_scan,
        {
            "description": "Scan a directory and report Tier 1 (Git), Tier 2 (Master Vault), and Tier 3 (Scratch) sizes in MB.",
            "inputSchema": {
                "type": "object",
                "properties": {"root_dir": {"type": "string"}},
                "required": ["root_dir"],
            },
        },
    ),
    "studio_archive_clean": (
        studio_archive_clean,
        {
            "description": "Clean Tier 3 build scratch (*_stage.html, slides_*/, frames_*/) inside rendered packages.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "root_dir": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": True},
                },
                "required": ["root_dir"],
            },
        },
    ),
    "studio_archive_sync": (
        studio_archive_sync,
        {
            "description": "Sync Tier 2 master media (.mp4, .wav, thumbnails, clips, plates) with Google Drive via rclone.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "local_dir": {"type": "string"},
                    "rel_subpath": {"type": "string"},
                    "direction": {"type": "string", "enum": ["push", "pull"], "default": "push"},
                    "dry_run": {"type": "boolean", "default": False},
                },
                "required": ["local_dir", "rel_subpath"],
            },
        },
    ),
}


def run_stdio_jsonrpc() -> None:
    """Zero-dependency MCP JSON-RPC 2.0 stdio loop."""
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue

        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "photon-studio", "version": "0.1.0"},
                },
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        elif method == "tools/list":
            tools_list = [
                {"name": name, "description": meta["description"], "inputSchema": meta["inputSchema"]}
                for name, (_, meta) in TOOL_REGISTRY.items()
            ]
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools_list}}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        elif method == "tools/call":
            tname = params.get("name")
            targs = params.get("arguments") or {}
            if tname not in TOOL_REGISTRY:
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tname}"},
                }
            else:
                fn, _ = TOOL_REGISTRY[tname]
                try:
                    out = fn(**targs)
                    resp = {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(out, indent=2)}],
                            "isError": False,
                        },
                    }
                except Exception as exc:
                    resp = {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                            "isError": True,
                        },
                    }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


def main() -> None:
    run_stdio_jsonrpc()


if __name__ == "__main__":
    main()
