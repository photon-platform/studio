"""MCP server for PHOTON Studio (`studio-mcp`).

Supports both `mcp.server.fastmcp.FastMCP` (when installed) and built-in zero-dependency
JSON-RPC 2.0 stdio transport (supporting both Content-Length framed and line-delimited JSON-RPC)
on Python 3.13.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, BinaryIO, Callable

from .archive import (
    clean_scratch,
    scan_storage,
    sync_archive,
)
from .broadcast import (
    discover_all_packages,
    get_quota_status,
    publish_packages,
    repair_package,
    schedule_packages,
)
from .capture import capture_html_viewport
from .motion import capture_motion_viewport, render_motion_package
from .render import render_package
from .speak import parse_unified_script, synthesize_script
from .stage import build_stage_html, render_stage_package
from .verify import probe_media, verify_package_dir


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


def studio_broadcast_publish(
    package_dirs: list[str],
    channel: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Mark ONLY the specified uploaded packages as PUBLIC (verifying channel handle first)."""
    return publish_packages(
        [Path(p) for p in package_dirs],
        channel=channel,
        dry_run=dry_run,
    )


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


def studio_probe_media(media_path: str) -> dict[str, Any]:
    """Run ffprobe on a video or audio file and return stream and container metadata."""
    return probe_media(Path(media_path))


def studio_stage(
    package_dir: str,
    build_only: bool = True,
    render: bool = False,
    section: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Build interactive `stage.html` or render per-segment clips and assemble QHD + Short MP4s."""
    pkg = Path(package_dir).resolve()
    if render or section:
        return render_stage_package(pkg, target_section=section, force=force)
    out_html = build_stage_html(pkg)
    return {"status": "ok", "stage_html": str(out_html)}


def studio_motion(
    target_path: str,
    output_mp4: str | None = None,
    duration_sec: float = 30.0,
    fps: int = 30,
    encode_only: bool = False,
    test_frames_only: bool = False,
    clean: bool = False,
) -> dict[str, Any]:
    """Render a Mode 2 (`motion`) package (`motion.json` / `camera_path.json`) or standalone HTML to 2560x1440 QHD MP4."""
    p = Path(target_path).resolve()
    if output_mp4 and p.suffix.lower() in (".html", ".htm"):
        return capture_motion_viewport(
            url_or_html=str(p),
            output_mp4=Path(output_mp4).resolve(),
            duration_sec=duration_sec,
            fps=fps,
        )
    return render_motion_package(
        p,
        encode_only=encode_only,
        test_frames_only=test_frames_only,
        clean=clean,
    )


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


def studio_speak(
    script_path: str,
    output_wav: str | None = None,
    prefix: str | None = None,
    variant: str = "16x9",
) -> dict[str, Any]:
    """Parse `script.scr` / `script.txt` or synthesize multi-voice Gemini TTS audio (`.wav` + `.json` manifest)."""
    sp = Path(script_path).resolve()
    if output_wav or prefix:
        out_dir = Path(output_wav).resolve().parent if output_wav else sp.parent
        pfx = prefix or (Path(output_wav).stem if output_wav else None)
        wav_out, json_out = synthesize_script(sp, out_dir=out_dir, prefix=pfx, variant=variant)
        return {
            "status": "ok",
            "wav": str(wav_out),
            "manifest": str(json_out),
        }
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


def studio_run_host_script(
    script_path: str,
    args: list[str] | None = None,
    cwd: str | None = None,
    timeout_sec: int = 1800,
) -> dict[str, Any]:
    """Execute a domain production Python script on the host GPU/audio stack using isolated file descriptors."""
    sp = Path(script_path).resolve()
    if not sp.exists():
        raise FileNotFoundError(f"Script not found: {sp}")
    work_dir = Path(cwd).resolve() if cwd else sp.parent.parent
    cmd = [sys.executable, str(sp), *(args or [])]
    env = os.environ.copy()
    t0 = time.monotonic()
    with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
        proc = subprocess.Popen(
            cmd,
            cwd=str(work_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out_f,
            stderr=err_f,
            close_fds=True,
            start_new_session=True,
        )
        proc.wait(timeout=timeout_sec)
        out_f.seek(0)
        err_f.seek(0)
        stdout_str = out_f.read().decode("utf-8", errors="replace")
        stderr_str = err_f.read().decode("utf-8", errors="replace")

    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "elapsed_sec": round(time.monotonic() - t0, 2),
        "stdout_tail": stdout_str[-4000:],
        "stderr_tail": stderr_str[-2000:],
    }


def studio_archive_scan(root_dir: str) -> dict[str, Any]:
    """Scan a directory and report Tier 1 (Git), Tier 2 (Master Vault), and Tier 3 (Scratch) sizes in MB."""
    return scan_storage(Path(root_dir))


def studio_archive_clean(root_dir: str, dry_run: bool = True) -> dict[str, Any]:
    """Clean Tier 3 build scratch (`stage.html`, `segments/`, `slides_*/`, `frames/`) inside rendered packages."""
    return clean_scratch(Path(root_dir), dry_run=dry_run, require_mp4=True)


def studio_archive_sync(
    local_dir: str,
    rel_subpath: str,
    direction: str = "push",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Sync Tier 2 master media (.mp4, .wav, thumbnails) with Google Drive via rclone."""
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
                "properties": {"project": {"type": "string", "description": "Optional project filter (elements, etc, phyllotaxis, GOR-9)"}},
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
    "studio_broadcast_publish": (
        studio_broadcast_publish,
        {
            "description": "Mark ONLY the specified uploaded packages as PUBLIC after verifying the channel handle.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "package_dirs": {"type": "array", "items": {"type": "string"}},
                    "channel": {"type": "string", "description": "Optional channel handle (@geometor or @GOR-9-station)"},
                    "dry_run": {"type": "boolean", "default": False},
                },
                "required": ["package_dirs"],
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
    "studio_probe_media": (
        studio_probe_media,
        {
            "description": "Run ffprobe on a media file (.mp4 or .wav) and return stream and format metadata.",
            "inputSchema": {
                "type": "object",
                "properties": {"media_path": {"type": "string"}},
                "required": ["media_path"],
            },
        },
    ),
    "studio_stage": (
        studio_stage,
        {
            "description": "Build interactive stage.html or render per-segment Mode 1 QHD + Short MP4s.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "package_dir": {"type": "string"},
                    "build_only": {"type": "boolean", "default": True},
                    "render": {"type": "boolean", "default": False},
                    "section": {"type": "string"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["package_dir"],
            },
        },
    ),
    "studio_motion": (
        studio_motion,
        {
            "description": "Render a Mode 2 (motion) package (motion.json / camera_path.json) or 3D HTML file to 2560x1440 QHD MP4.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target_path": {"type": "string"},
                    "output_mp4": {"type": "string"},
                    "duration_sec": {"type": "number", "default": 30.0},
                    "fps": {"type": "integer", "default": 30},
                    "encode_only": {"type": "boolean", "default": False},
                    "test_frames_only": {"type": "boolean", "default": False},
                    "clean": {"type": "boolean", "default": False},
                },
                "required": ["target_path"],
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
            "description": "Parse script.scr / script.txt or synthesize multi-voice Gemini TTS audio (.wav + .json manifest).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "script_path": {"type": "string"},
                    "output_wav": {"type": "string"},
                    "prefix": {"type": "string"},
                    "variant": {"type": "string", "enum": ["16x9", "9x16"], "default": "16x9"},
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
    "studio_run_host_script": (
        studio_run_host_script,
        {
            "description": "Run a domain production Python script on the host GPU/audio stack using isolated file descriptors.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "script_path": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "timeout_sec": {"type": "integer", "default": 1800},
                },
                "required": ["script_path"],
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
            "description": "Clean Tier 3 build scratch (stage.html, segments/, slides_*/, frames/) inside rendered packages.",
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
            "description": "Sync Tier 2 master media (.mp4, .wav, thumbnails) with Google Drive via rclone.",
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


def read_rpc_message(stream: BinaryIO) -> tuple[dict[str, Any] | None, str]:
    """Read one JSON-RPC message from `stream` supporting both Content-Length headers and line-delimited JSON."""
    while True:
        first_line = stream.readline()
        if not first_line:
            return None, "line"
        stripped = first_line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(b"content-length:"):
            length = int(stripped.split(b":", 1)[1].strip())
            while True:
                hdr = stream.readline()
                if not hdr or hdr in (b"\r\n", b"\n"):
                    break
            body = stream.read(length)
            return json.loads(body.decode("utf-8")), "framed"
        return json.loads(stripped.decode("utf-8")), "line"


def write_rpc_message(stream: BinaryIO, payload: dict[str, Any], mode: str = "line") -> None:
    """Write a JSON-RPC response matching the client's framing (`framed` or `line`)."""
    raw = json.dumps(payload).encode("utf-8")
    if mode == "framed":
        header = f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii")
        stream.write(header + raw)
    else:
        stream.write(raw + b"\n")
    stream.flush()


def handle_rpc_request(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Process a single JSON-RPC 2.0 request dict and return a response dict (or `None` for notifications)."""
    msg_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params") or {}

    if method.startswith("notifications/") or msg_id is None:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "photon-studio", "version": "0.1.0"},
            },
        }

    if method == "tools/list":
        tools_list = [
            {"name": name, "description": meta["description"], "inputSchema": meta["inputSchema"]}
            for name, (_, meta) in TOOL_REGISTRY.items()
        ]
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools_list}}

    if method == "tools/call":
        tname = params.get("name")
        targs = params.get("arguments") or {}
        if tname not in TOOL_REGISTRY:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tname}"},
            }
        fn, _ = TOOL_REGISTRY[tname]
        try:
            out = fn(**targs)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(out, indent=2)}],
                    "isError": False,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def run_stdio_jsonrpc() -> None:
    """Zero-dependency MCP JSON-RPC 2.0 stdio loop supporting Content-Length and line framing."""
    in_stream = sys.stdin.buffer
    out_stream = sys.stdout.buffer
    while True:
        try:
            msg, framing_mode = read_rpc_message(in_stream)
        except Exception:
            continue
        if msg is None:
            break
        resp = handle_rpc_request(msg)
        if resp is not None:
            write_rpc_message(out_stream, resp, mode=framing_mode)


def main() -> None:
    run_stdio_jsonrpc()


if __name__ == "__main__":
    main()
