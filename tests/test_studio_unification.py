"""Unit tests for the unified PHOTON Studio engine (`photon_platform.studio`)."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest

from photon_platform.studio import (
    broadcast,
    mcp_server,
    motion,
    script_compiler,
    speak,
    stage,
    uploader,
)
from photon_platform.studio.engines import speak as legacy_speak_shim

# ---------------------------------------------------------------------------
# 1. Script Compiler (.scr -> scenes)
# ---------------------------------------------------------------------------


def test_compile_scr_variants_and_bells(tmp_path: Path) -> None:
    scr_text = """\
short_code: I.1
title: Equilateral Triangle
enunciation: On a given finite straight line to construct an equilateral triangle.

=== intro ===
[bell:intro]
[screen: INTRO | only: wide]
say: Welcome to Book One, Proposition One.

=== construction ===
[bell:construction]
[screen: CONSTRUCT | active: / A B / | punchline: true]
symbolic: / A B / = / A C /
clause: Let the circle B C D be described.
just: Post. 3 | I.Post.3 | Circle from center and radius
say: Describe the circle from A to B [sfx:circle].
say_short: Sweep the circle from A through B [sfx:circle].
"""
    scr_file = tmp_path / "script.scr"
    scr_file.write_text(scr_text, encoding="utf-8")

    wide_scenes = script_compiler.load_scenes_or_compile_scr(scr_file, variant="16x9")
    short_scenes = script_compiler.load_scenes_or_compile_scr(scr_file, variant="9x16")

    wide_steps = [s for sc in wide_scenes for s in sc["subslides"]]
    short_steps = [s for sc in short_scenes for s in sc["subslides"]]

    # Intro screen is `only: wide`, so 16x9 gets 2 subslides and 9x16 gets 1
    assert len(wide_steps) == 2
    assert len(short_steps) == 1
    assert wide_steps[0]["is_intro"] is True
    assert wide_steps[1]["is_punchline"] is True
    assert wide_steps[1]["just_tag"] == "Post. 3"
    assert "[sfx:circle]" in wide_steps[1]["sfx_cues"]
    assert "Sweep the circle" in short_steps[0]["say_raw"]


# ---------------------------------------------------------------------------
# 2. Stage Subscript Formatting (Python & Embedded JS)
# ---------------------------------------------------------------------------


def test_stage_format_subscripts_html() -> None:
    assert (
        stage.format_subscripts_html("Center X_1 and X_19")
        == "Center X<sub>1</sub> and X<sub>19</sub>"
    )
    assert (
        stage.format_subscripts_html("Midpoint M_a, V_2")
        == "Midpoint M<sub>a</sub>, V<sub>2</sub>"
    )
    # Multi-digit proposition prefixes like I_01 must NOT be turned into subscripts
    assert stage.format_subscripts_html("I_01_1080p") == "I_01_1080p"
    # Attributes inside HTML tags must NOT be mutated
    html_in = '<span class="tok-gold" data-id="X_1">X_1 = M_b</span>'
    html_out = stage.format_subscripts_html(html_in)
    assert 'data-id="X_1"' in html_out
    assert ">X<sub>1</sub> = M<sub>b</sub></span>" in html_out
    assert "function formatSubscriptsHtml(" in stage.STAGE_HTML_TEMPLATE


# ---------------------------------------------------------------------------
# 3. Speak Engine (Multi-Character Inline Voice Tags, SFX, Legacy Shim)
# ---------------------------------------------------------------------------


def test_speak_shim_and_multi_voice_parsing(tmp_path: Path) -> None:
    assert legacy_speak_shim.synthesize_script is speak.synthesize_script
    profile = speak.load_profile()
    assert "styles" in profile

    scr_path = tmp_path / "dialogue.scr"
    scr_path.write_text(
        "title: Station Log\n"
        "=== act_1 ===\n"
        "[bell:intro]\n"
        "[screen: CONSTRUCT | voice: Fenrir]\n"
        "say: [voice: Kore | style: brisk] Carrier locked at 1420 MHz [sfx:point].\n",
        encoding="utf-8",
    )
    parsed = speak.parse_unified_script(scr_path)
    assert parsed["header"]["title"] == "Station Log"
    assert len(parsed["bells"]) == 1
    assert parsed["bells"][0]["bell"] == "intro"

    segments = speak.parse_structured_scr_segments(
        scr_path.read_text(encoding="utf-8"),
        profile,
        variant="16x9",
    )
    assert segments is not None
    phrases = [p for _bell, ph_list in segments for p in ph_list]
    assert len(phrases) == 1
    assert "[voice: Kore | style: brisk]" in phrases[0][1]

    cues, cleaned = speak.extract_sfx_cues("Draw segment A B [sfx:line] now.")
    assert cues == ["line"]
    assert cleaned == "Draw segment A B now."
    assert len(speak.make_sfx_pcm("point")) > 0
    assert len(speak.make_harmonic_bell_pcm([440.0, 660.0], duration=0.05)) > 0


# ---------------------------------------------------------------------------
# 4. Motion Engine (Clean FFmpeg Flags, Spec Resolution, Torn Frame Audit)
# ---------------------------------------------------------------------------


def test_motion_ffmpeg_clean_flags_no_grain(tmp_path: Path) -> None:
    out_mp4 = tmp_path / "out.mp4"
    cmd_vflip = motion.build_motion_ffmpeg_cmd(
        out_mp4, 2560, 1440, fps=30, input_format="rawvideo_vflip"
    )
    joined_vflip = " ".join(cmd_vflip)
    assert "-pix_fmt yuv420p" in joined_vflip
    assert "-movflags +faststart" in joined_vflip
    assert "noise=" not in joined_vflip
    assert "setsar=" not in joined_vflip
    assert "bt709" not in joined_vflip
    assert cmd_vflip[cmd_vflip.index("-vf") + 1] == "vflip"

    cmd_png = motion.build_motion_ffmpeg_cmd(
        out_mp4, 2560, 1440, fps=30, input_format="image2pipe"
    )
    assert "-vf" not in cmd_png


def test_motion_spec_and_torn_frame_detection(tmp_path: Path) -> None:
    # Verify phyllotaxis motion.json resolves cleanly
    phyl_pkg = Path("/home/phi/PROJECTS/geometor/phyllotaxis/videos/unfolding")
    pkg_dir, spec_file, spec = motion.resolve_motion_spec(phyl_pkg)
    assert pkg_dir == phyl_pkg.resolve()
    assert spec_file.name == "motion.json"
    assert spec["wide_mp4"] == "phyllotaxis_unfolding_1080p.mp4"
    model_html = motion._resolve_model_html_from_spec(pkg_dir, spec)
    assert model_html.name == "propagation_3d.html"

    # Verify duplicate top-32-row CRC torn frame detection
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    w, h = 64, 64
    buf_a = bytes([(i * 7) % 256 for i in range(w * h * 4)])
    buf_b = bytearray(buf_a)
    # Mutate only bottom of output PNG (raw WebGL y=0..9), keeping top-32 rows identical
    for i in range(10 * w * 4):
        buf_b[i] = buf_b[i] ^ 0xFF
    buf_c = bytes([(i * 13 + 99) % 256 for i in range(w * h * 4)])

    encode_png = motion._rgba_bottom_up_to_png_bytes
    (frames_dir / "frame_0001.png").write_bytes(encode_png(buf_a, w, h))
    (frames_dir / "frame_0002.png").write_bytes(encode_png(bytes(buf_b), w, h))
    (frames_dir / "frame_0003.png").write_bytes(encode_png(buf_c, w, h))

    torn = motion.purge_torn_frames(frames_dir, width=w, audit_only=True)
    assert torn == [1, 2]
    assert (frames_dir / "frame_0001.png").exists()

    purged = motion.purge_torn_frames(frames_dir, width=w, audit_only=False)
    assert purged == [1, 2]
    assert not (frames_dir / "frame_0001.png").exists()
    assert not (frames_dir / "frame_0002.png").exists()
    assert (frames_dir / "frame_0003.png").exists()


# ---------------------------------------------------------------------------
# 5. Multi-Channel Uploader & Broadcast Guardrails
# ---------------------------------------------------------------------------


def test_channel_routing_and_guardrail(tmp_path: Path) -> None:
    assert uploader.normalize_channel_handle("gor-9-station") == "@GOR-9-station"
    assert uploader.normalize_channel_handle("@GEOMETOR") == "@geometor"

    gor_pkg = Path("/home/phi/PROJECTS/phiarchitect/GOR-9/videos/mk4_demo")
    ch_gor, tok_gor = uploader.resolve_package_channel_and_token(gor_pkg)
    assert ch_gor == "@GOR-9-station"
    assert tok_gor.name == "youtube_token_gor9.json"

    phyl_pkg = Path("/home/phi/PROJECTS/geometor/phyllotaxis/videos/unfolding")
    ch_phyl, tok_phyl = uploader.resolve_package_channel_and_token(phyl_pkg)
    assert ch_phyl == "@geometor"
    assert tok_phyl.name == "youtube_token.json"

    with patch.object(
        uploader,
        "get_authenticated_channel",
        return_value={"snippet": {"title": "GEOMETOR", "customUrl": "@geometor"}},
    ):
        ok_ch = uploader.verify_authenticated_channel_handle("fake-token", "@geometor")
        assert ok_ch["snippet"]["customUrl"] == "@geometor"
        with pytest.raises(RuntimeError, match="guardrail blocked upload"):
            uploader.verify_authenticated_channel_handle("fake-token", "@GOR-9-station")


def test_broadcast_inspect_single_and_dual_packages() -> None:
    gor_info = broadcast.inspect_package(
        Path("/home/phi/PROJECTS/phiarchitect/GOR-9/videos/mk4_demo"),
        project="GOR-9",
    )
    assert gor_info["channel"] == "@GOR-9-station"
    assert gor_info["mode"] == "single_wide"
    assert gor_info["wide_video_id"] == "4Pv6BXxQSi8"

    phyl_info = broadcast.inspect_package(
        Path("/home/phi/PROJECTS/geometor/phyllotaxis/videos/unfolding"),
        project="phyllotaxis",
    )
    assert phyl_info["channel"] == "@geometor"
    assert phyl_info["mode"] == "dual"


# ---------------------------------------------------------------------------
# 6. MCP Server (Tool Registry, Dual Framing, Isolated Host Script Runner)
# ---------------------------------------------------------------------------


def test_mcp_server_registry_and_dual_framing(tmp_path: Path) -> None:
    expected_tools = {
        "studio_quota_status",
        "studio_broadcast_status",
        "studio_broadcast_repair",
        "studio_broadcast_publish",
        "studio_broadcast_schedule",
        "studio_verify",
        "studio_probe_media",
        "studio_speak",
        "studio_stage",
        "studio_motion",
        "studio_render",
        "studio_capture",
        "studio_run_host_script",
        "studio_archive_scan",
        "studio_archive_clean",
        "studio_archive_sync",
    }
    assert expected_tools.issubset(set(mcp_server.TOOL_REGISTRY.keys()))

    # Test Content-Length framed round-trip
    req = {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}}
    buf = io.BytesIO()
    mcp_server.write_rpc_message(buf, req, mode="framed")
    buf.seek(0)
    decoded, mode = mcp_server.read_rpc_message(buf)
    assert mode == "framed"
    assert decoded == req
    resp = mcp_server.handle_rpc_request(decoded)
    assert resp is not None
    assert resp["id"] == 7
    tool_names = {t["name"] for t in resp["result"]["tools"]}
    assert "studio_motion" in tool_names

    # Test line-delimited round-trip
    line_buf = io.BytesIO()
    init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    mcp_server.write_rpc_message(line_buf, init_req, mode="line")
    line_buf.seek(0)
    decoded_line, mode_line = mcp_server.read_rpc_message(line_buf)
    assert mode_line == "line"
    init_resp = mcp_server.handle_rpc_request(decoded_line)
    assert init_resp is not None
    assert init_resp["result"]["serverInfo"]["name"] == "photon-studio"

    # Test isolated FD host script execution
    tiny_script = tmp_path / "hello_host.py"
    tiny_script.write_text(
        "import sys\nprint('HOST_OK:' + ','.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    run_res = mcp_server.studio_run_host_script(
        str(tiny_script), args=["alpha", "beta"], cwd=str(tmp_path)
    )
    assert run_res["ok"] is True
    assert "HOST_OK:alpha,beta" in run_res["stdout_tail"]
