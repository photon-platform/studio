"""Interactive Broadcast Stage & Live Preview Server for GEOMETOR Elements (Mode 1: slides).

Provides:
  1. `build_stage_data(package_dir)`: Compiles `script.scr` + SVG `data-ref` vocabulary + audio timings into a unified JSON payload.
  2. `build_stage_html(package_dir, out_html)`: Generates a standalone, zero-dependency `stage.html` inside the proposition folder that works both:
     - Interactively in any browser (`file://` or `http://`) with responsive viewport scaling (`Fit Window`, `50%`, `75%`, `100%`, `133% 1440p`), `16:9` / `9:16` / `Thumbnail` switching, audio-synced playback, safe-zone & golden-ratio guides, SVG `data-ref` hover inspector, and `script.scr` step inspector.
     - Headlessly (`?headless=1&format=16x9&step=0`) for exact `2560x1440` QHD and `1080x1920` Short PNG frame capture.
  3. `serve_stage(target_path, port, open_browser)`: Lightweight local HTTP server with live `script.scr` hot-reload and a Proposition Switcher across all packages in `elements/videos/`.
"""

from __future__ import annotations

import html
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, urlparse
import webbrowser
import xml.etree.ElementTree as ET

from .script_compiler import (
    load_scenes_or_compile_scr,
    resolve_script_source_for_variant,
)
from .svg_utils import (
    STEP_KIND_COLORS,
    clean_caption_text,
    find_chrome,
    prepare_euclid_dark_svg,
)

_SUBSCRIPT_X_RE = re.compile(r"\bX_(\d+|[a-z])\b")
_SUBSCRIPT_SYM_RE = re.compile(r"\b([A-Za-z])_([abc]|[1-9])\b")


def format_subscripts_html(html_or_text: str, *, escape_first: bool = False) -> str:
    """Format mathematical subscripts (`X_1` -> `X<sub>1</sub>`, `M_a` -> `M<sub>a</sub>`) outside HTML tags."""
    s = html.escape(html_or_text) if escape_first else str(html_or_text)

    def _sub_chunk(chunk: str) -> str:
        chunk = _SUBSCRIPT_X_RE.sub(r"X<sub>\1</sub>", chunk)
        return _SUBSCRIPT_SYM_RE.sub(r"\1<sub>\2</sub>", chunk)

    return "".join(
        part if part.startswith("<") else _sub_chunk(part)
        for part in re.split(r"(<[^>]+>)", s)
    )


def infer_package_prefix(package_dir: Path) -> str:
    package_dir = Path(package_dir).resolve()
    wavs = [w for w in sorted(package_dir.glob("*.wav")) if not w.stem.endswith(("_spoken", "_wide", "_short"))]
    if wavs:
        return wavs[0].stem
    if package_dir.parent.name.startswith("book_"):
        rom = package_dir.parent.name.replace("book_", "").upper()
        return f"{rom}_{package_dir.name}"
    m_x = re.match(r"^x-(\d+)$", package_dir.name, re.I)
    if m_x:
        return f"X_{int(m_x.group(1)):05d}"
    return package_dir.name


def extract_svg_vocabulary(svg_path: Path) -> list[dict[str, Any]]:
    """Extract all addressable geometric elements (`id`, `data-ref`, `data-aliases`, `kind`) from an Euclid or ETC SVG."""
    if not svg_path or not svg_path.exists():
        return []
    items: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    try:
        root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
        for elem in root.iter():
            el_id = elem.attrib.get("id", "") or elem.attrib.get("data-id", "")
            if not el_id or el_id.startswith(("hl-", "lbl-")):
                continue
            data_ref = elem.attrib.get("data-ref", "").strip()
            aliases_raw = elem.attrib.get("data-aliases", "").strip()
            ref = data_ref or aliases_raw or (el_id if not el_id.startswith(("el-", "defs", "svg", "points", "elements", "graphics", "labels")) else "")
            if not ref or ref in seen_refs:
                continue
            seen_refs.add(ref)
            if ref.startswith("/"):
                kind = "segment"
            elif ref.startswith("(") or "circle" in ref.lower():
                kind = "circle"
            elif ref.startswith("<"):
                kind = "polygon"
            elif ref.startswith("["):
                kind = "line"
            else:
                kind = "point"
            items.append({
                "id": el_id,
                "ref": ref,
                "aliases": aliases_raw,
                "kind": kind,
            })
    except Exception:
        pass
    return items


def _infer_next_prop(short_code: str, explicit_next: str = "") -> str:
    if explicit_next:
        return explicit_next
    m = re.match(r"^([IVX]+)\.(\d+)$", short_code.strip())
    if m:
        return f"{m.group(1)}.{int(m.group(2)) + 1}"
    mx = re.match(r"^X_(\d+)$", short_code.strip(), re.I)
    if mx:
        return f"X_{int(mx.group(1)) + 1}"
    return "Next Video"


def get_package_status(package_dir: Path, prefix: str | None = None) -> dict[str, Any]:
    """Return comprehensive live status telemetry for a video package directory."""
    package_dir = Path(package_dir).resolve()
    pfx = prefix or infer_package_prefix(package_dir)

    scr_path = package_dir / "script.scr"
    wide_scr_path = package_dir / "script.wide.scr"
    short_scr_path = package_dir / "script.short.scr"
    txt_path = package_dir / "script.txt"
    scenes_path = package_dir / "scenes.json"

    primary_scr = scr_path if scr_path.exists() else (txt_path if txt_path.exists() else scenes_path)
    scr_mtime = primary_scr.stat().st_mtime if primary_scr.exists() else 0.0
    scr_content = primary_scr.read_text(encoding="utf-8") if primary_scr.exists() and primary_scr.suffix in (".scr", ".txt") else ""

    has_wide_override = (
        (wide_scr_path.exists() and bool(wide_scr_path.read_text(encoding="utf-8").strip()))
        or bool(re.search(r"^\s*(?:say_wide|say_16x9|clause_wide)\s*:|\bonly\s*:\s*(?:wide|16x9)\b", scr_content, re.I | re.M))
    )
    has_short_override = (
        (short_scr_path.exists() and bool(short_scr_path.read_text(encoding="utf-8").strip()))
        or bool(re.search(r"^\s*(?:say_short|say_9x16|clause_short)\s*:|\bonly\s*:\s*(?:short|9x16)\b", scr_content, re.I | re.M))
    )

    wav_path = package_dir / f"{pfx}.wav"
    json_path = package_dir / f"{pfx}.json"
    audio_dur = 0.0
    if json_path.exists():
        try:
            audio_dur = round(float(json.loads(json_path.read_text(encoding="utf-8")).get("total_duration_sec", 0.0)), 1)
        except Exception:
            pass

    if not wav_path.exists() or not json_path.exists():
        audio_status = "missing"
    elif scr_mtime > json_path.stat().st_mtime + 1.0:
        audio_status = "stale"
    else:
        audio_status = "ready"

    mp4_16x9 = package_dir / f"{pfx}_1080p.mp4"
    mp4_9x16 = package_dir / f"{pfx}_short.mp4"
    mp4_16_exists = mp4_16x9.exists()
    mp4_9_exists = mp4_9x16.exists()
    mp4_16_mb = round(mp4_16x9.stat().st_size / (1024 * 1024), 2) if mp4_16_exists else 0.0
    mp4_9_mb = round(mp4_9x16.stat().st_size / (1024 * 1024), 2) if mp4_9_exists else 0.0

    ref_mtime = max(scr_mtime, wav_path.stat().st_mtime if wav_path.exists() else 0.0)
    if not mp4_16_exists and not mp4_9_exists:
        render_status = "missing"
    elif not (mp4_16_exists and mp4_9_exists):
        render_status = "partial"
    elif ref_mtime > min(mp4_16x9.stat().st_mtime, mp4_9x16.stat().st_mtime) + 2.0:
        render_status = "stale"
    else:
        render_status = "ready"

    yt_json = package_dir / "youtube.json"
    yt_status = "local"
    yt_wide_id = ""
    yt_short_id = ""
    if yt_json.exists():
        try:
            yd = json.loads(yt_json.read_text(encoding="utf-8"))
            uploads = yd.get("uploads", {})
            w_up = uploads.get("wide_1080p") or uploads.get("1080p") or {}
            s_up = uploads.get("short") or uploads.get("9x16") or {}
            yt_wide_id = w_up.get("video_id", "")
            yt_short_id = s_up.get("video_id", "")
            if yt_wide_id or yt_short_id:
                yt_status = w_up.get("privacyStatus") or yd.get("privacyStatus") or "unlisted"
        except Exception:
            pass

    return {
        "prefix": pfx,
        "script_file": primary_scr.name if primary_scr.exists() else "missing",
        "has_scr": scr_path.exists(),
        "has_wide_override": has_wide_override,
        "has_short_override": has_short_override,
        "audio_status": audio_status,
        "audio_duration_sec": audio_dur,
        "render_status": render_status,
        "mp4_16x9_exists": mp4_16_exists,
        "mp4_9x16_exists": mp4_9_exists,
        "mp4_16x9_mb": mp4_16_mb,
        "mp4_9x16_mb": mp4_9_mb,
        "youtube_status": yt_status,
        "yt_wide_id": yt_wide_id,
        "yt_short_id": yt_short_id,
        "yt_wide_url": f"https://youtu.be/{yt_wide_id}" if yt_wide_id else "",
        "yt_short_url": f"https://youtube.com/shorts/{yt_short_id}" if yt_short_id else "",
    }


def _ensure_intro_review_outro(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """If a package's scenes list only has core acts,
    automatically wrap it with `intro`, `review` (sound-only replay), and `outro` (pleas for attention)."""
    if not scenes:
        return scenes

    all_subs = [sub for sc in scenes for sub in (sc.get("subslides") or [sc])]
    if not all_subs:
        return scenes

    has_intro = any(sub.get("is_intro") or str(sub.get("step_kind", "")).upper() == "INTRO" for sub in all_subs)
    has_review = any(sub.get("is_review") or str(sub.get("step_kind", "")).upper() == "REVIEW" for sub in all_subs)
    has_outro = any(sub.get("is_outro") or str(sub.get("step_kind", "")).upper() == "OUTRO" for sub in all_subs)

    if has_intro and has_review and has_outro:
        return scenes

    first_sub = all_subs[0]
    punch_sub = next((s for s in reversed(all_subs) if s.get("is_punchline")), all_subs[-1])
    core_subs = [
        s
        for s in all_subs
        if str(s.get("step_kind", "")).upper() not in ("INTRO", "REVIEW", "REVIEW_AUTO", "OUTRO")
    ]
    core_total = len(core_subs)

    out_scenes: list[dict[str, Any]] = []

    if not has_intro:
        intro_sub = {
            **punch_sub,
            "section_id": "intro",
            "bell": "bell:proposition",
            "step_num": 0,
            "step_total": core_total,
            "step_kind": "INTRO",
            "is_intro": True,
            "is_review": False,
            "is_outro": False,
            "is_punchline": False,
            "sound_only": False,
            "fixed_duration_sec": 2.75,
            "weight": 0.45,
            "symbolic": f"{first_sub.get('prop_id', 'PROPOSITION')} • {first_sub.get('short_title', '')}".strip(" •"),
            "say_raw": "Euclid's Elements.",
            "sfx_cues": ["[bell:proposition]"],
        }
        out_scenes.append({
            "id": "intro",
            "bell": "bell:proposition",
            "subslides": [intro_sub],
        })

    out_scenes.extend(scenes)

    if not has_review and core_subs:
        review_ledger = [
            {
                "step_num": int(cs.get("step_num", i + 1)),
                "step_kind": str(cs.get("step_kind", "STEP")),
                "symbolic": cs.get("symbolic") or cs.get("raw_clause") or cs.get("just_id") or "",
                "sfx": [c for c in cs.get("sfx_cues", []) if c.startswith("[sfx:")],
            }
            for i, cs in enumerate(core_subs)
        ]
        rev_subs: list[dict[str, Any]] = []
        for r_idx, cs in enumerate(core_subs):
            only_sfx = [c for c in cs.get("sfx_cues", []) if c.startswith("[sfx:")]
            if not only_sfx:
                only_sfx = ["[sfx:point]"]
            cues_step = list(only_sfx)
            if r_idx == 0:
                cues_step.insert(0, "[bell:construction]")
            dur_s = 1.23 if r_idx == len(core_subs) - 1 else 0.85
            rev_subs.append({
                **cs,
                "section_id": "review",
                "bell": "bell:construction",
                "step_num": int(cs.get("step_num", r_idx + 1)),
                "step_total": core_total,
                "step_kind": "REVIEW",
                "orig_step_kind": str(cs.get("step_kind", "CONSTRUCT")),
                "is_intro": False,
                "is_review": True,
                "is_outro": False,
                "is_punchline": False,
                "sound_only": True,
                "fixed_duration_sec": dur_s,
                "weight": dur_s,
                "say_raw": " ".join(only_sfx),
                "sfx_cues": cues_step,
                "review_ledger": review_ledger,
            })
        out_scenes.append({
            "id": "review",
            "bell": "bell:construction",
            "subslides": rev_subs,
        })

    if not has_outro:
        outro_sub = {
            **punch_sub,
            "section_id": "outro",
            "bell": "bell:insight",
            "step_num": core_total,
            "step_total": core_total,
            "step_kind": "OUTRO",
            "is_intro": False,
            "is_review": False,
            "is_outro": True,
            "is_punchline": False,
            "sound_only": True,
            "fixed_duration_sec": 3.6,
            "weight": 1.0,
            "symbolic": "@geometor • Subscribe, Like & Explore the Codex",
            "say_raw": "[sfx:qed]",
            "sfx_cues": ["[bell:insight]", "[sfx:qed]"],
        }
        out_scenes.append({
            "id": "outro",
            "bell": "bell:insight",
            "subslides": [outro_sub],
        })

    return out_scenes


def _align_scenes_with_manifest(
    scenes: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    extra_kind_colors: dict[str, tuple[str, str]],
) -> tuple[list[dict[str, Any]], float, int]:
    expanded_slides: list[dict[str, Any]] = []
    running_time = 0.0

    spoken_scene_indices = [
        i
        for i, sc in enumerate(scenes)
        if sc.get("id") not in ("intro", "review", "outro")
    ]
    split_intro_from_seg0 = (
        len(segments) == len(spoken_scene_indices)
        and len(scenes) > len(segments)
        and scenes[0].get("id") == "intro"
        and len(segments) > 0
    )

    if split_intro_from_seg0:
        seg_map = {sc_idx: segments[pos] for pos, sc_idx in enumerate(spoken_scene_indices)}
        seg0 = segments[0]
        seg0_subs = seg0.get("subsegments", [])
        intro_dur = 2.75
        if seg0_subs:
            first_sub_dur = float(seg0_subs[0].get("duration_sec", 5.5))
            intro_dur = min(2.75, round(first_sub_dur * 0.47, 3))

        for idx, scene in enumerate(scenes):
            sec_id = scene.get("id", "")
            subs = scene.get("subslides") or [scene]
            if sec_id == "intro":
                sub = subs[0]
                merged = {k: v for k, v in scene.items() if k != "subslides"}
                merged.update(sub)
                expanded_slides.append({
                    "scene": merged,
                    "seg": seg0_subs[0] if seg0_subs else seg0,
                    "duration": intro_dur,
                    "start_sec": 0.0,
                    "end_sec": intro_dur,
                    "use_synth_audio": False,
                })
                running_time = intro_dur
            elif idx in seg_map:
                seg = seg_map[idx]
                subsegs = seg.get("subsegments", [])
                if len(subsegs) == len(subs):
                    for s_i, (sub, sseg) in enumerate(zip(subs, subsegs)):
                        merged = {k: v for k, v in scene.items() if k != "subslides"}
                        merged.update(sub)
                        st_sec = float(sseg["start_sec"])
                        en_sec = float(sseg["end_sec"])
                        if idx == spoken_scene_indices[0] and s_i == 0:
                            st_sec = intro_dur
                        dur_sec = max(0.3, en_sec - st_sec)
                        expanded_slides.append({
                            "scene": merged,
                            "seg": sseg,
                            "duration": dur_sec,
                            "start_sec": st_sec,
                            "end_sec": en_sec,
                            "use_synth_audio": False,
                        })
                        running_time = en_sec
                else:
                    total_w = sum(float(s.get("weight", 1.0)) for s in subs) or 1.0
                    seg_start = max(running_time, float(seg.get("start_sec", running_time)))
                    seg_end = float(seg.get("end_sec", seg_start + 6.0))
                    eff_dur = max(1.0, seg_end - seg_start)
                    cur_t = seg_start
                    for sub in subs:
                        merged = {k: v for k, v in scene.items() if k != "subslides"}
                        merged.update(sub)
                        w = float(sub.get("weight", 1.0))
                        sub_dur = eff_dur * (w / total_w)
                        expanded_slides.append({
                            "scene": merged,
                            "seg": seg,
                            "duration": sub_dur,
                            "start_sec": cur_t,
                            "end_sec": cur_t + sub_dur,
                            "use_synth_audio": False,
                        })
                        cur_t += sub_dur
                    running_time = cur_t
            else:
                cur_t = running_time
                for sub in subs:
                    merged = {k: v for k, v in scene.items() if k != "subslides"}
                    merged.update(sub)
                    sub_dur = float(sub.get("fixed_duration_sec") or sub.get("weight") or 0.85)
                    expanded_slides.append({
                        "scene": merged,
                        "seg": None,
                        "duration": sub_dur,
                        "start_sec": cur_t,
                        "end_sec": cur_t + sub_dur,
                        "use_synth_audio": True,
                    })
                    cur_t += sub_dur
                running_time = cur_t
    else:
        for idx, scene in enumerate(scenes):
            seg = segments[idx] if idx < len(segments) else None
            seg_dur = float(seg["duration_sec"]) if seg and "duration_sec" in seg else float(scene.get("duration_sec", 6.0))
            seg_start = float(seg["start_sec"]) if seg and "start_sec" in seg else running_time
            subs = scene.get("subslides")
            if subs and isinstance(subs, list):
                subsegs = seg.get("subsegments", []) if seg else []
                if len(subsegs) == len(subs):
                    for sub, sseg in zip(subs, subsegs):
                        merged = {k: v for k, v in scene.items() if k != "subslides"}
                        merged.update(sub)
                        expanded_slides.append({
                            "scene": merged,
                            "seg": sseg,
                            "duration": float(sseg["duration_sec"]),
                            "start_sec": float(sseg["start_sec"]),
                            "end_sec": float(sseg["end_sec"]),
                            "use_synth_audio": False,
                        })
                    running_time = float(subsegs[-1]["end_sec"])
                else:
                    cur_t = seg_start
                    total_w = sum(float(s.get("weight", 1.0)) for s in subs) or 1.0
                    eff_seg_dur = seg_dur if seg else max(3.5, total_w * 3.8)
                    for sub in subs:
                        merged = {k: v for k, v in scene.items() if k != "subslides"}
                        merged.update(sub)
                        if not seg and sub.get("fixed_duration_sec"):
                            sub_dur = float(sub["fixed_duration_sec"])
                        else:
                            w = float(sub.get("weight", 1.0))
                            sub_dur = eff_seg_dur * (w / total_w)
                        expanded_slides.append({
                            "scene": merged,
                            "seg": seg,
                            "duration": sub_dur,
                            "start_sec": cur_t,
                            "end_sec": cur_t + sub_dur,
                            "use_synth_audio": seg is None,
                        })
                        cur_t += sub_dur
                    running_time = cur_t
            else:
                expanded_slides.append({
                    "scene": scene,
                    "seg": seg,
                    "duration": seg_dur,
                    "start_sec": seg_start,
                    "end_sec": seg_start + seg_dur,
                    "use_synth_audio": seg is None,
                })
                running_time = seg_start + seg_dur

    slides_out: list[dict[str, Any]] = []
    total_slides = len(expanded_slides)
    punchline_idx = total_slides - 1 if total_slides > 0 else 0

    for s_idx, item in enumerate(expanded_slides):
        sc = item["scene"]
        seg = item["seg"] or {}
        if sc.get("is_punchline"):
            punchline_idx = s_idx

        step_num = int(sc.get("step_num", s_idx + 1))
        step_total = int(sc.get("step_total", total_slides))
        step_kind = str(sc.get("step_kind", "CONSTRUCT")).upper()
        if step_kind in ("Q.E.F.", "Q.E.D.", "CONCLUDE", "∴ CONCLUDE"):
            step_kind = "∴"
        orig_kind = str(sc.get("orig_step_kind") or step_kind).upper()
        if orig_kind in ("Q.E.F.", "Q.E.D.", "CONCLUDE", "∴ CONCLUDE"):
            orig_kind = "∴"

        kind_fg, kind_bg = extra_kind_colors.get(
            orig_kind if sc.get("is_review") else step_kind,
            ("#e8b85a", "rgba(232, 184, 90, 0.15)"),
        )
        say_raw = sc.get("say_raw") or seg.get("text", "")
        say_clean = clean_caption_text(say_raw)
        if sc.get("is_review"):
            say_clean = f"🔊 Sound-Only Replay • {' '.join(sc.get('sfx_cues', []))}"
        elif sc.get("is_outro"):
            say_clean = "🔔 Subscribe to @geometor • Like, Comment & Explore the Codex"

        just_id = re.sub(r"\s*\(Q\.E\.[DF]\.\)", "", str(sc.get("just_id", ""))).strip()
        svg_p = Path(sc["svg_path"]) if sc.get("svg_path") else None

        raw_ledger = sc.get("review_ledger") or []
        enriched_ledger = []
        for row in raw_ledger:
            rk = str(row.get("step_kind", "STEP")).upper()
            if rk in ("Q.E.F.", "Q.E.D.", "CONCLUDE", "∴ CONCLUDE"):
                rk = "∴"
            rfg, rbg = extra_kind_colors.get(rk, ("#e8b85a", "rgba(232, 184, 90, 0.15)"))
            enriched_ledger.append({
                "step_num": row.get("step_num", 1),
                "step_kind": rk,
                "kind_fg": rfg,
                "kind_bg": rbg,
                "symbolic": row.get("symbolic", ""),
                "sfx": row.get("sfx", []),
            })

        slides_out.append({
            "index": s_idx,
            "step_num": step_num,
            "step_total": step_total,
            "section_id": sc.get("section_id") or sc.get("id", "section"),
            "bell": sc.get("bell", ""),
            "only": sc.get("only", ""),
            "step_kind": step_kind,
            "orig_step_kind": orig_kind,
            "is_intro": bool(sc.get("is_intro") or step_kind == "INTRO"),
            "is_review": bool(sc.get("is_review") or step_kind == "REVIEW"),
            "is_outro": bool(sc.get("is_outro") or step_kind == "OUTRO"),
            "sound_only": bool(sc.get("sound_only", False)),
            "use_synth_audio": bool(item.get("use_synth_audio", False)),
            "kind_fg": kind_fg,
            "kind_bg": kind_bg,
            "svg_name": svg_p.name if svg_p else "",
            "svg_path": str(svg_p) if svg_p else "",
            "symbolic": sc.get("symbolic", ""),
            "raw_clause": sc.get("raw_clause", ""),
            "clause_html": format_subscripts_html(sc["clause_html"]) if sc.get("clause_html") else "",
            "just_tag": sc.get("just_tag", "∵ PREREQUISITE"),
            "just_id": just_id,
            "just_title": sc.get("just_title", ""),
            "footer_note": sc.get("footer_note", ""),
            "visible_ids": sc.get("visible_ids", ["*"]),
            "hidden_ids": sc.get("hidden_ids", []),
            "active_ids": sc.get("active_ids", []),
            "coincide_ids": sc.get("coincide_ids", []),
            "equal_ids": sc.get("equal_ids", []),
            "tick_ids": sc.get("tick_ids", []),
            "strict_step_ids": bool(sc.get("strict_step_ids", False)),
            "zoom": sc.get("zoom", []),
            "thumb_zoom": sc.get("thumb_zoom", []),
            "raw_show": sc.get("raw_show", ["*"]),
            "raw_active": sc.get("raw_active", []),
            "raw_equal": sc.get("raw_equal", []),
            "raw_ticks": sc.get("raw_ticks", []),
            "say_raw": say_raw,
            "say_shared_raw": sc.get("say_shared_raw", say_raw),
            "say_wide_raw": sc.get("say_wide_raw", ""),
            "say_short_raw": sc.get("say_short_raw", ""),
            "say_clean": say_clean,
            "sfx_cues": sc.get("sfx_cues", []),
            "review_ledger": enriched_ledger,
            "start_sec": round(float(item["start_sec"]), 3),
            "end_sec": round(float(item["end_sec"]), 3),
            "duration": round(float(item["duration"]), 3),
            "is_punchline": bool(sc.get("is_punchline", False)),
        })

    return slides_out, running_time, punchline_idx


def _sanitize_section_id(sec_id: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", str(sec_id or "section").strip().lower()).strip("_")
    return cleaned or "section"


def _slide_visual_signature(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_num": s.get("step_num"),
        "step_total": s.get("step_total"),
        "section_id": s.get("section_id"),
        "step_kind": s.get("step_kind"),
        "orig_step_kind": s.get("orig_step_kind"),
        "is_intro": s.get("is_intro"),
        "is_review": s.get("is_review"),
        "is_outro": s.get("is_outro"),
        "is_punchline": s.get("is_punchline"),
        "svg_name": s.get("svg_name"),
        "symbolic": s.get("symbolic"),
        "raw_clause": s.get("raw_clause"),
        "clause_html": s.get("clause_html"),
        "just_tag": s.get("just_tag"),
        "just_id": s.get("just_id"),
        "just_title": s.get("just_title"),
        "footer_note": s.get("footer_note"),
        "visible_ids": s.get("visible_ids"),
        "hidden_ids": s.get("hidden_ids"),
        "active_ids": s.get("active_ids"),
        "coincide_ids": s.get("coincide_ids"),
        "equal_ids": s.get("equal_ids"),
        "tick_ids": s.get("tick_ids"),
        "strict_step_ids": s.get("strict_step_ids"),
        "zoom": s.get("zoom"),
        "thumb_zoom": s.get("thumb_zoom"),
        "review_ledger": s.get("review_ledger"),
    }


def _compute_section_hashes(
    sec_idx: int,
    sec_id: str,
    bell: str,
    slides_16_sec: list[dict[str, Any]],
    slides_9_sec: list[dict[str, Any]],
    meta_header: dict[str, Any],
) -> dict[str, str]:
    import hashlib

    audio_payload = {
        "has_leading_gap": sec_idx > 0,
        "bell": bell or "",
        "steps": [
            {
                "step_kind": s.get("step_kind"),
                "sound_only": bool(s.get("sound_only", False)),
                "say_raw": s.get("say_raw", ""),
                "sfx_cues": s.get("sfx_cues", []),
                "hold_dur": round(float(s.get("duration", 0.85)), 3) if s.get("sound_only") else 0.0,
            }
            for s in slides_16_sec
        ],
    }
    vis16_payload = {
        "template_v": "clean_v2",
        "meta": meta_header,
        "slides": [_slide_visual_signature(s) for s in slides_16_sec],
    }
    vis9_payload = {
        "template_v": "clean_v2",
        "meta": meta_header,
        "slides": [_slide_visual_signature(s) for s in slides_9_sec],
    }

    audio_hash = hashlib.sha256(json.dumps(audio_payload, sort_keys=True).encode("utf-8")).hexdigest()[:20]
    vis16_hash = hashlib.sha256(json.dumps(vis16_payload, sort_keys=True).encode("utf-8")).hexdigest()[:20]
    vis9_hash = hashlib.sha256(json.dumps(vis9_payload, sort_keys=True).encode("utf-8")).hexdigest()[:20]

    clip_payload = {
        "audio_hash": audio_hash,
        "vis16_hash": vis16_hash,
        "vis9_hash": vis9_hash,
        "durs_16": [round(float(s.get("duration", 0.0)), 3) for s in slides_16_sec],
        "durs_9": [round(float(s.get("duration", 0.0)), 3) for s in slides_9_sec],
    }
    clip_hash = hashlib.sha256(json.dumps(clip_payload, sort_keys=True).encode("utf-8")).hexdigest()[:20]

    return {
        "audio_hash": audio_hash,
        "visual_hash_16x9": vis16_hash,
        "visual_hash_9x16": vis9_hash,
        "clip_hash": clip_hash,
    }


def _extract_section_scr_blocks(
    script_text: str,
    ordered_sec_ids: list[str],
    slides_by_sec_16: dict[str, list[dict[str, Any]]],
) -> dict[str, str]:
    header_lines: list[str] = []
    sec_lines_map: dict[str, list[str]] = {}
    cur_sec: str | None = None

    for raw_line in script_text.splitlines():
        m_sec = re.match(r"^\s*(?:#\s*)?===+\s*(.+?)\s*===+\s*$", raw_line)
        if m_sec:
            cur_sec = _sanitize_section_id(m_sec.group(1))
            sec_lines_map.setdefault(cur_sec, []).append(f"=== {m_sec.group(1).strip()} ===")
            continue
        if cur_sec is None:
            header_lines.append(raw_line)
        else:
            sec_lines_map.setdefault(cur_sec, []).append(raw_line)

    header_block = "\n".join(header_lines).strip()
    out: dict[str, str] = {}
    for sec_id in ordered_sec_ids:
        key = _sanitize_section_id(sec_id)
        if key in sec_lines_map and sec_lines_map[key]:
            body = "\n".join(sec_lines_map[key]).strip()
        else:
            s_list = slides_by_sec_16.get(sec_id, [])
            gen_lines = [f"=== {sec_id} ==="]
            if s_list and s_list[0].get("bell"):
                gen_lines.append(f"[{s_list[0]['bell']}]")
            if sec_id == "review" and s_list:
                pace = s_list[0].get("duration", 0.85)
                gen_lines.append(f"[review: auto | pace: {pace}]")
            else:
                for sl in s_list:
                    sk = sl.get("step_kind", "CONSTRUCT")
                    punch = " | punchline: true" if sl.get("is_punchline") else ""
                    gen_lines.append(f"\n[screen: {sk}{punch}]")
                    gen_lines.append(f"show: {', '.join(sl.get('raw_show') or ['*'])}")
                    if sl.get("raw_active"):
                        gen_lines.append(f"active: {', '.join(sl['raw_active'])}")
                    if sl.get("raw_equal"):
                        gen_lines.append(f"equal: {', '.join(sl['raw_equal'])}")
                    if sl.get("symbolic"):
                        gen_lines.append(f"symbolic: {sl['symbolic']}")
                    if sl.get("raw_clause"):
                        gen_lines.append(f"clause: {sl['raw_clause']}")
                    if sl.get("just_id"):
                        jt = f" | {sl['just_title']}" if sl.get("just_title") else ""
                        gen_lines.append(f"just: {sl['just_id']}{jt}")
                    if sl.get("say_raw"):
                        gen_lines.append(f"say: {sl['say_raw']}")
            body = "\n".join(gen_lines).strip()

        if header_block:
            out[sec_id] = f"{header_block}\n\n{body}\n"
        else:
            out[sec_id] = f"{body}\n"
    return out


def inspect_package_segments(
    package_dir: Path,
    slides_16x9: list[dict[str, Any]],
    slides_9x16: list[dict[str, Any]],
    script_text: str,
    meta_header: dict[str, Any],
) -> list[dict[str, Any]]:
    """Group slides by `section_id`, compute hashes, and inspect `<package_dir>/segments/<NN>_<section_id>/`."""
    ordered_secs: list[str] = []
    slides_by_16: dict[str, list[dict[str, Any]]] = {}
    slides_by_9: dict[str, list[dict[str, Any]]] = {}

    for s in slides_16x9:
        sid = str(s.get("section_id") or "section")
        if sid not in slides_by_16:
            ordered_secs.append(sid)
            slides_by_16[sid] = []
        slides_by_16[sid].append(s)

    for s in slides_9x16:
        sid = str(s.get("section_id") or "section")
        if sid not in slides_by_9:
            if sid not in ordered_secs:
                ordered_secs.append(sid)
            slides_by_9[sid] = []
        slides_by_9[sid].append(s)

    scr_blocks = _extract_section_scr_blocks(script_text, ordered_secs, slides_by_16)
    segments_root = package_dir / "segments"
    section_folders: list[dict[str, Any]] = []

    for sec_idx, sec_id in enumerate(ordered_secs):
        safe_id = _sanitize_section_id(sec_id)
        folder_name = f"{sec_idx:02d}_{safe_id}"
        sec_dir = segments_root / folder_name
        rel_dir = f"segments/{folder_name}"

        s16 = slides_by_16.get(sec_id, [])
        s9 = slides_by_9.get(sec_id, [])
        bell = (s16[0].get("bell") if s16 else (s9[0].get("bell") if s9 else "")) or ""

        hashes = _compute_section_hashes(sec_idx, sec_id, bell, s16, s9, meta_header)
        sec_json_path = sec_dir / "section.json"
        saved_meta: dict[str, Any] = {}
        if sec_json_path.exists():
            try:
                saved_meta = json.loads(sec_json_path.read_text(encoding="utf-8"))
            except Exception:
                saved_meta = {}

        audio_wav = sec_dir / "audio.wav"
        clip_16 = sec_dir / "clip_16x9.mp4"
        clip_9 = sec_dir / "clip_9x16.mp4"
        steps_dir = sec_dir / "steps"

        step_wavs_ok = bool(s16) and all((steps_dir / f"step_{k:02d}.wav").exists() for k in range(len(s16)))
        pngs_16_ok = bool(s16) and all((steps_dir / f"step_{k:02d}_16x9.png").exists() for k in range(len(s16)))
        pngs_9_ok = bool(s9) and all((steps_dir / f"step_{k:02d}_9x16.png").exists() for k in range(len(s9)))

        audio_ready = (
            audio_wav.exists()
            and step_wavs_ok
            and saved_meta.get("audio_hash") == hashes["audio_hash"]
        )
        slides_16_ready = pngs_16_ok and saved_meta.get("visual_hash_16x9") == hashes["visual_hash_16x9"]
        slides_9_ready = pngs_9_ok and saved_meta.get("visual_hash_9x16") == hashes["visual_hash_9x16"]
        clips_ready = (
            audio_ready
            and slides_16_ready
            and slides_9_ready
            and clip_16.exists()
            and clip_9.exists()
            and saved_meta.get("clip_hash") == hashes["clip_hash"]
        )

        any_artifact = sec_dir.exists() and (audio_wav.exists() or clip_16.exists() or clip_9.exists())
        if clips_ready:
            status = "ready"
        elif any_artifact:
            status = "dirty"
        else:
            status = "missing"

        start_sec = round(float(s16[0]["start_sec"]), 3) if s16 else 0.0
        end_sec = round(float(s16[-1]["end_sec"]), 3) if s16 else 0.0
        dur_sec = round(max(0.0, end_sec - start_sec), 3)

        for k, sl in enumerate(s16):
            sl["section_folder"] = folder_name
            sl["section_index"] = sec_idx
            sl["step_in_section"] = k
            sl["section_status"] = status
        for k, sl in enumerate(s9):
            sl["section_folder"] = folder_name
            sl["section_index"] = sec_idx
            sl["step_in_section"] = k
            sl["section_status"] = status

        section_folders.append({
            "index": sec_idx,
            "section_id": sec_id,
            "folder_name": folder_name,
            "rel_dir": rel_dir,
            "bell": bell,
            "step_count_16x9": len(s16),
            "step_count_9x16": len(s9),
            "start_sec": start_sec,
            "end_sec": end_sec,
            "duration_sec": dur_sec,
            "status": status,
            "audio_ready": audio_ready,
            "slides_16x9_ready": slides_16_ready,
            "slides_9x16_ready": slides_9_ready,
            "clips_ready": clips_ready,
            "has_audio": audio_wav.exists(),
            "has_clip_16x9": clip_16.exists(),
            "has_clip_9x16": clip_9.exists(),
            "audio_url": f"{rel_dir}/audio.wav",
            "clip_16x9_url": f"{rel_dir}/clip_16x9.mp4",
            "clip_9x16_url": f"{rel_dir}/clip_9x16.mp4",
            "hashes": hashes,
            "section_scr": scr_blocks.get(sec_id, ""),
        })

    return section_folders


def build_stage_data(package_dir: Path, prefix: str | None = None) -> dict[str, Any]:
    """Compile `script.scr` (or `scenes.json`) + `<prefix>.json` audio timings + SVG vocabulary into a Stage dictionary."""
    package_dir = Path(package_dir).resolve()
    prefix = prefix or infer_package_prefix(package_dir)

    source_16x9 = resolve_script_source_for_variant(package_dir, "16x9")
    source_9x16 = resolve_script_source_for_variant(package_dir, "9x16")
    if not source_16x9.exists():
        raise FileNotFoundError(f"No script.scr, script.txt, or scenes.json found in {package_dir}")

    raw_scenes_16x9 = load_scenes_or_compile_scr(source_16x9, variant="16x9")
    raw_scenes_9x16 = load_scenes_or_compile_scr(source_9x16, variant="9x16")
    scenes_16x9 = _ensure_intro_review_outro(raw_scenes_16x9)
    scenes_9x16 = _ensure_intro_review_outro(raw_scenes_9x16)

    manifest_path = package_dir / f"{prefix}.json"
    wav_path = package_dir / f"{prefix}.wav"
    segments: list[dict[str, Any]] = []
    audio_file_duration = 0.0
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            segments = manifest.get("segments", [])
            audio_file_duration = float(manifest.get("total_duration_sec", 0.0))
        except Exception:
            segments = []

    extra_kind_colors = {
        **STEP_KIND_COLORS,
        "INTRO": ("#e8b85a", "rgba(232, 184, 90, 0.16)"),
        "REVIEW": ("#38bdf8", "rgba(56, 189, 248, 0.16)"),
        "OUTRO": ("#5ec27e", "rgba(94, 194, 126, 0.16)"),
    }

    slides_16x9, running_time_16x9, punchline_idx_16x9 = _align_scenes_with_manifest(
        scenes_16x9, segments, extra_kind_colors
    )
    slides_9x16, running_time_9x16, punchline_idx_9x16 = _align_scenes_with_manifest(
        scenes_9x16, segments, extra_kind_colors
    )

    first_sc = (scenes_16x9[0].get("subslides") or [scenes_16x9[0]])[0] if scenes_16x9 else {}
    prop_id = first_sc.get("prop_id", "BOOK I • PROP. 1")
    m = re.search(r"BOOK\s+([IVX]+)\s*•\s*PROP\.\s*(\d+)", prop_id, re.I)
    if m:
        short_code = f"{m.group(1).upper()}.{m.group(2)}"
    else:
        short_code = first_sc.get("short_code") or prop_id.split("•")[-1].strip()

    g_id = first_sc.get("g_id", "G.17")
    folio = first_sc.get("folio", "Folio 0255 • p. 241")
    folio_display = (
        folio
        if any(k in folio.lower() for k in ("heath", "kimberling", "etc", "geometor"))
        else f"Heath (1908) • {folio}"
    )
    enunciation = first_sc.get("enunciation", "")
    short_title = first_sc.get("short_title", "")
    next_prop = _infer_next_prop(short_code, first_sc.get("next_prop", ""))

    # Prepare all SVGs referenced across slides (supports multi-SVG ETC packages & single-SVG Euclid packages)
    svgs_map: dict[str, str] = {}
    primary_svg_path: Path | None = None
    for s in slides_16x9 + slides_9x16:
        sp_str = s.get("svg_path", "")
        if not sp_str:
            continue
        sp = Path(sp_str)
        if not sp.exists():
            continue
        if primary_svg_path is None:
            primary_svg_path = sp
        if sp.name not in svgs_map:
            svgs_map[sp.name] = prepare_euclid_dark_svg(sp)

    svg_markup = svgs_map.get(primary_svg_path.name, "") if primary_svg_path else ""
    svg_vocab = extract_svg_vocabulary(primary_svg_path) if primary_svg_path else []

    scr_file = package_dir / "script.scr"
    txt_file = package_dir / "script.txt"
    wide_scr_file = package_dir / "script.wide.scr"
    short_scr_file = package_dir / "script.short.scr"
    primary_editable = scr_file if scr_file.exists() else (txt_file if txt_file.exists() else source_16x9)
    script_text = primary_editable.read_text(encoding="utf-8") if primary_editable.exists() else ""
    script_wide_text = wide_scr_file.read_text(encoding="utf-8") if wide_scr_file.exists() else ""
    script_short_text = short_scr_file.read_text(encoding="utf-8") if short_scr_file.exists() else ""

    meta_header = {
        "prop_id": prop_id,
        "short_code": short_code,
        "short_title": short_title,
        "next_prop": next_prop,
        "g_id": g_id,
        "folio_display": folio_display,
        "enunciation": enunciation,
    }
    section_folders = inspect_package_segments(
        package_dir=package_dir,
        slides_16x9=slides_16x9,
        slides_9x16=slides_9x16,
        script_text=script_text,
        meta_header=meta_header,
    )

    mtimes = [
        p.stat().st_mtime
        for p in (source_16x9, source_9x16, scr_file, wide_scr_file, short_scr_file, wav_path, manifest_path)
        if p.exists()
    ]
    mtime = max(mtimes) if mtimes else 0.0
    pkg_status = get_package_status(package_dir, prefix=prefix)
    pkg_status["segments_total"] = len(section_folders)
    pkg_status["segments_ready"] = sum(1 for sf in section_folders if sf["status"] == "ready")
    pkg_status["segments_dirty"] = sum(1 for sf in section_folders if sf["status"] == "dirty")
    is_etc = prop_id.upper().startswith("ETC") or "kimberling" in folio_display.lower()

    return {
        "prefix": prefix,
        "project_kind": "etc" if is_etc else "elements",
        "package_dir": str(package_dir),
        "source_file": primary_editable.name,
        "mtime": mtime,
        "status": pkg_status,
        "section_folders": [
            {k: v for k, v in sf.items() if k != "section_scr"}
            for sf in section_folders
        ],
        "script_text": script_text,
        "script_wide_text": script_wide_text,
        "script_short_text": script_short_text,
        "prop_id": prop_id,
        "short_code": short_code,
        "short_title": short_title,
        "next_prop": next_prop,
        "g_id": g_id,
        "folio_display": folio_display,
        "enunciation": enunciation,
        "svg_name": primary_svg_path.name if primary_svg_path else "",
        "svg_markup": svg_markup,
        "svgs_map": svgs_map,
        "svg_vocab": svg_vocab,
        "has_audio": wav_path.exists(),
        "audio_filename": wav_path.name,
        "audio_file_duration": round(audio_file_duration, 3),
        "total_duration": round(running_time_16x9, 3),
        "total_duration_9x16": round(running_time_9x16, 3),
        "punchline_index": punchline_idx_16x9,
        "punchline_index_9x16": punchline_idx_9x16,
        "slides": slides_16x9,
        "slides_16x9": slides_16x9,
        "slides_9x16": slides_9x16,
    }


STAGE_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>GEOMETOR Stage • __SHORT_CODE__ (__PREFIX__)</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg-studio: #06080d;
    --bg-panel: #0d111a;
    --bg-card: #131926;
    --border-gold: rgba(232, 184, 90, 0.32);
    --border-subtle: rgba(148, 163, 184, 0.2);
    --gold: #e8b85a;
    --green: #5ec27e;
    --blue: #38bdf8;
    --text-main: #f4f1ea;
    --text-muted: #94a3b8;
  }

  html, body {
    width: 100%;
    height: 100%;
    overflow: hidden;
    background: var(--bg-studio);
    color: var(--text-main);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }

  /* ============================================================================
     HEADLESS CAPTURE MODE (?headless=1)
     Locks viewport to exact 1920x1080 (scaled 1.3333x -> 2560x1440) or 1080x1920
     with zero studio UI chrome.
     ============================================================================ */
  body.headless-mode .director-topbar,
  body.headless-mode .director-sidebar,
  body.headless-mode .director-transport,
  body.headless-mode .stage-bezel-label,
  body.headless-mode .safe-zone-overlay,
  body.headless-mode .svg-hover-tooltip {
    display: none !important;
  }
  body.headless-mode .director-workspace,
  body.headless-mode .stage-viewport {
    position: fixed !important;
    inset: 0 !important;
    padding: 0 !important;
    margin: 0 !important;
    display: block !important;
    background: transparent !important;
  }
  body.headless-mode #stage-scaler,
  body.headless-mode #stage-mount {
    position: static !important;
    transform: none !important;
    width: auto !important;
    height: auto !important;
    box-shadow: none !important;
    border: none !important;
    border-radius: 0 !important;
  }

  /* ============================================================================
     INTERACTIVE DIRECTOR STUDIO SHELL
     ============================================================================ */
  .director-app {
    display: flex;
    flex-direction: column;
    width: 100vw;
    height: 100vh;
    overflow: hidden;
  }

  /* Top Control Bar */
  .director-topbar {
    height: 52px;
    flex-shrink: 0;
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border-gold);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 14px;
    gap: 8px;
    z-index: 20;
  }
  .topbar-left, .topbar-center, .topbar-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .studio-brand {
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--gold);
    background: rgba(232, 184, 90, 0.12);
    border: 1px solid rgba(232, 184, 90, 0.45);
    padding: 5px 10px;
    border-radius: 6px;
    white-space: nowrap;
  }
  .prop-select {
    font-family: "JetBrains Mono", monospace;
    font-size: 12.5px;
    font-weight: 600;
    color: var(--text-main);
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: 6px;
    padding: 5px 8px;
    max-width: 260px;
    cursor: pointer;
    outline: none;
  }
  .prop-select:focus {
    border-color: var(--gold);
  }

  .btn-group {
    display: flex;
    background: #080b11;
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    padding: 2px;
    gap: 2px;
  }
  .tb-btn {
    font-family: "JetBrains Mono", monospace;
    font-size: 11.5px;
    font-weight: 600;
    color: var(--text-muted);
    background: transparent;
    border: none;
    padding: 5px 9px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.15s ease;
    white-space: nowrap;
  }
  .tb-btn:hover {
    color: var(--text-main);
    background: rgba(148, 163, 184, 0.1);
  }
  .tb-btn.active {
    color: #0b0e15;
    background: var(--gold);
    font-weight: 700;
  }
  .tb-btn.toggle-on {
    color: var(--gold);
    background: rgba(232, 184, 90, 0.16);
    border: 1px solid rgba(232, 184, 90, 0.45);
  }

  .res-readout {
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    color: var(--blue);
    background: rgba(56, 189, 248, 0.1);
    border: 1px solid rgba(56, 189, 248, 0.35);
    padding: 4px 8px;
    border-radius: 6px;
    white-space: nowrap;
  }

  /* Main Split Workspace: Center Viewport + Right Inspector */
  .director-workspace {
    flex: 1;
    display: flex;
    min-height: 0;
    position: relative;
  }

  .stage-viewport {
    flex: 1;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: auto;
    background:
      radial-gradient(circle at 50% 50%, #101521 0%, #06080d 75%);
    padding: 32px 24px 20px 24px;
  }

  .stage-bezel-label {
    position: absolute;
    top: 8px;
    left: 16px;
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    color: #64748b;
    letter-spacing: 0.08em;
    pointer-events: none;
    z-index: 10;
  }

  #stage-scaler {
    position: relative;
    box-shadow:
      0 0 0 1.5px rgba(232, 184, 90, 0.45),
      0 28px 80px rgba(0, 0, 0, 0.85);
    border-radius: 4px;
    flex-shrink: 0;
    overflow: hidden;
  }
  #stage-mount {
    position: absolute;
    top: 0;
    left: 0;
    transform-origin: 0 0;
  }

  /* Safe-Zone & Golden Ratio Composition Guides */
  .safe-zone-overlay {
    position: absolute;
    inset: 0;
    pointer-events: none;
    z-index: 50;
    display: none;
  }
  .safe-zone-overlay.visible {
    display: block;
  }
  .guide-line-v, .guide-line-h {
    position: absolute;
    background: rgba(56, 189, 248, 0.28);
  }
  .guide-line-v { top: 0; bottom: 0; width: 1px; }
  .guide-line-h { left: 0; right: 0; height: 1px; }
  .safe-scrub-16x9 {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 64px;
    background: repeating-linear-gradient(
      -45deg,
      rgba(239, 68, 68, 0.12),
      rgba(239, 68, 68, 0.12) 10px,
      rgba(239, 68, 68, 0.04) 10px,
      rgba(239, 68, 68, 0.04) 20px
    );
    border-top: 1.5px dashed rgba(239, 68, 68, 0.65);
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: "JetBrains Mono", monospace;
    font-size: 14px;
    color: #fca5a5;
    letter-spacing: 0.12em;
  }
  .safe-shorts-bottom {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 440px;
    background: repeating-linear-gradient(
      -45deg,
      rgba(239, 68, 68, 0.12),
      rgba(239, 68, 68, 0.12) 12px,
      rgba(239, 68, 68, 0.04) 12px,
      rgba(239, 68, 68, 0.04) 24px
    );
    border-top: 2px dashed rgba(239, 68, 68, 0.65);
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding-top: 14px;
    font-family: "JetBrains Mono", monospace;
    font-size: 16px;
    color: #fca5a5;
    letter-spacing: 0.1em;
  }
  .safe-shorts-right {
    position: absolute;
    top: 420px;
    right: 0;
    bottom: 440px;
    width: 84px;
    border-left: 2px dashed rgba(239, 68, 68, 0.5);
    background: rgba(239, 68, 68, 0.07);
  }

  /* SVG Element Hover Tooltip */
  .svg-hover-tooltip {
    position: fixed;
    pointer-events: none;
    z-index: 100;
    background: rgba(11, 14, 21, 0.96);
    border: 1px solid var(--gold);
    border-radius: 8px;
    padding: 6px 12px;
    font-family: "JetBrains Mono", monospace;
    font-size: 12.5px;
    color: var(--text-main);
    box-shadow: 0 10px 25px rgba(0, 0, 0, 0.7);
    display: none;
    gap: 8px;
    align-items: center;
  }
  .svg-hover-tooltip .tt-ref {
    color: var(--gold);
    font-weight: 700;
  }
  .svg-hover-tooltip .tt-id {
    color: var(--text-muted);
    font-size: 11px;
  }

  /* Right Director Sidebar */
  .director-sidebar {
    width: 410px;
    flex-shrink: 0;
    background: var(--bg-panel);
    border-left: 1px solid var(--border-gold);
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  .director-sidebar.collapsed {
    display: none;
  }
  .sidebar-tabs {
    display: flex;
    border-bottom: 1px solid var(--border-subtle);
    background: #090c13;
  }
  .sb-tab {
    flex: 1;
    padding: 10px 12px;
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--text-muted);
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    cursor: pointer;
    text-transform: uppercase;
  }
  .sb-tab.active {
    color: var(--gold);
    border-bottom-color: var(--gold);
    background: rgba(232, 184, 90, 0.06);
  }
  .sidebar-body {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  /* Step List Cards */
  .section-divider {
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--blue);
    padding: 6px 4px 2px 4px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid rgba(56, 189, 248, 0.22);
  }
  .step-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
    padding: 10px 12px;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    gap: 6px;
    transition: all 0.12s ease;
  }
  .step-card:hover {
    border-color: rgba(232, 184, 90, 0.5);
  }
  .step-card.active {
    background: rgba(232, 184, 90, 0.13);
    border-color: var(--gold);
    box-shadow: inset 3px 0 0 var(--gold);
  }
  .sc-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    font-family: "JetBrains Mono", monospace;
    font-size: 11.5px;
  }
  .sc-badge {
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
  }
  .sc-sfx {
    font-size: 10.5px;
    color: var(--green);
    background: rgba(94, 194, 126, 0.12);
    border: 1px solid rgba(94, 194, 126, 0.35);
    padding: 1px 6px;
    border-radius: 4px;
  }
  .sc-time {
    color: var(--text-muted);
    font-size: 11px;
  }
  .sc-symbolic {
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    color: var(--gold);
    font-weight: 600;
  }
  .sc-say {
    font-family: Georgia, serif;
    font-size: 13px;
    line-height: 1.35;
    color: #e2e8f0;
  }

  /* Inspector Panel */
  .insp-box {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .insp-title {
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text-muted);
  }
  .scr-code {
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    line-height: 1.5;
    color: #e2e8f0;
    background: #080b11;
    border: 1px solid rgba(148, 163, 184, 0.18);
    border-radius: 8px;
    padding: 10px 12px;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .vocab-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .vocab-pill {
    font-family: "JetBrains Mono", monospace;
    font-size: 11.5px;
    padding: 4px 9px;
    border-radius: 6px;
    border: 1px solid var(--border-subtle);
    background: #080b11;
    color: var(--text-muted);
    cursor: pointer;
    user-select: none;
  }
  .vocab-pill.st-active {
    color: var(--gold);
    border-color: var(--gold);
    background: rgba(232, 184, 90, 0.16);
    font-weight: 700;
  }
  .vocab-pill.st-equal {
    color: var(--green);
    border-color: var(--green);
    background: rgba(94, 194, 126, 0.16);
    font-weight: 700;
  }
  .vocab-pill.st-visible {
    color: #e2e8f0;
    border-color: rgba(148, 163, 184, 0.45);
  }
  .vocab-pill.st-hidden {
    opacity: 0.35;
    text-decoration: line-through;
  }

  /* Bottom Transport & Timeline Bar */
  .director-transport {
    height: 58px;
    flex-shrink: 0;
    background: var(--bg-panel);
    border-top: 1px solid var(--border-gold);
    display: flex;
    align-items: center;
    padding: 0 18px;
    gap: 14px;
    z-index: 20;
  }
  .trans-btn {
    font-family: "JetBrains Mono", monospace;
    font-size: 13px;
    font-weight: 700;
    color: var(--text-main);
    background: var(--bg-card);
    border: 1px solid var(--border-gold);
    border-radius: 8px;
    padding: 6px 13px;
    cursor: pointer;
  }
  .trans-btn:hover {
    background: rgba(232, 184, 90, 0.2);
  }
  .trans-btn.play-active {
    background: var(--gold);
    color: #090c13;
  }
  .timeline-segments {
    flex: 1;
    height: 26px;
    display: flex;
    gap: 3px;
    align-items: center;
    background: #080b11;
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    padding: 3px;
    overflow: hidden;
  }
  .tl-seg {
    height: 100%;
    border-radius: 4px;
    background: rgba(148, 163, 184, 0.18);
    cursor: pointer;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: "JetBrains Mono", monospace;
    font-size: 10px;
    color: rgba(255, 255, 255, 0.65);
    overflow: hidden;
    transition: background 0.12s ease;
  }
  .tl-seg:hover {
    background: rgba(232, 184, 90, 0.35);
  }
  .tl-seg.done {
    background: rgba(232, 184, 90, 0.42);
  }
  .tl-seg.current {
    background: var(--gold);
    color: #090c13;
    font-weight: 700;
  }
  .time-code {
    font-family: "JetBrains Mono", monospace;
    font-size: 12.5px;
    color: var(--text-muted);
    min-width: 115px;
    text-align: right;
  }

  /* ============================================================================
     CANONICAL BROADCAST STAGE SURFACE (#stage-canvas)
     Exact 1920x1080 (16:9 -> 2560x1440 QHD) and 1080x1920 (9:16 Short)
     ============================================================================ */
  #stage-canvas {
    position: relative;
    overflow: hidden;
    background: radial-gradient(circle at 35% 48%, #151b28 0%, #0b0e15 70%, #07090e 100%);
    color: #f4f1ea;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    user-select: none;
  }
  #stage-canvas.fmt-16x9,
  #stage-canvas.fmt-thumb_16x9 {
    width: 1920px;
    height: 1080px;
  }
  #stage-canvas.fmt-9x16,
  #stage-canvas.fmt-thumb_9x16 {
    width: 1080px;
    height: 1920px;
  }

  .stage-grid {
    position: absolute;
    inset: 0;
    background-image:
      linear-gradient(rgba(232, 184, 90, 0.035) 1px, transparent 1px),
      linear-gradient(90deg, rgba(232, 184, 90, 0.035) 1px, transparent 1px);
    background-size: 56px 56px;
    pointer-events: none;
  }

  /* Smooth SVG transitions when stepping interactively */
  body.smooth-transitions #svg-holder svg * {
    transition: opacity 0.24s ease, stroke 0.24s ease, fill 0.24s ease, stroke-width 0.24s ease;
  }

  /* Shared Stage Typography & Token Pills */
  .header-canon-badge {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 38px;
    font-weight: 700;
    line-height: 1;
    letter-spacing: 0.03em;
    color: #e8b85a;
    background: rgba(232, 184, 90, 0.14);
    border: 1.5px solid rgba(232, 184, 90, 0.62);
    padding: 10px 22px;
    border-radius: 12px;
    white-space: nowrap;
    box-shadow: 0 8px 22px rgba(0, 0, 0, 0.45);
  }
  .header-title-text {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 34px;
    font-weight: 700;
    line-height: 1.2;
    color: #f8f6f0;
    text-transform: capitalize;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .step-counter-pill {
    font-family: "JetBrains Mono", monospace;
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 0.12em;
    color: #cbd5e1;
    background: rgba(30, 41, 59, 0.85);
    border: 1px solid rgba(148, 163, 184, 0.32);
    padding: 8px 18px;
    border-radius: 999px;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .progress-track {
    display: flex;
    gap: 8px;
    width: 100%;
  }
  .p-seg {
    flex: 1;
    height: 6px;
    border-radius: 999px;
    background: rgba(148, 163, 184, 0.2);
  }
  .p-seg.done { background: rgba(232, 184, 90, 0.65); }

  /* 16:9 Widescreen Layout (1920x1080 -> 2560x1440 QHD) */
  .wide-frame {
    position: relative;
    width: 1920px;
    height: 1080px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 36px 52px 64px 52px;
    gap: 24px;
  }
  .wide-header {
    display: flex;
    flex-direction: column;
    gap: 14px;
    background: rgba(16, 20, 29, 0.92);
    border: 1px solid rgba(232, 184, 90, 0.34);
    border-radius: 16px;
    padding: 18px 28px 16px 28px;
    box-shadow: 0 14px 35px rgba(0, 0, 0, 0.5);
  }
  .header-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 24px;
  }
  .header-left {
    display: flex;
    align-items: center;
    gap: 20px;
    min-width: 0;
    flex: 1;
  }
  .wide-main {
    flex: 1;
    display: grid;
    grid-template-columns: 1060px 1fr;
    gap: 28px;
    min-height: 0;
  }
  .wide-main .geom-stage-card {
    position: relative;
    height: 100%;
    background: radial-gradient(circle at 50% 50%, #141926 0%, #0d111a 100%);
    border: 1.5px solid rgba(232, 184, 90, 0.36);
    border-radius: 20px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 24px;
    box-shadow: inset 0 0 60px rgba(0, 0, 0, 0.65), 0 22px 55px rgba(0, 0, 0, 0.65);
    overflow: hidden;
  }
  .wide-main .svg-canvas-holder {
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .wide-main .svg-canvas-holder svg {
    width: 97% !important;
    height: 97% !important;
    max-height: 760px !important;
  }
  .wide-main .step-panel {
    height: 100%;
    min-width: 0;
    overflow: hidden;
    background: rgba(16, 20, 29, 0.95);
    border: 1.5px solid rgba(232, 184, 90, 0.36);
    border-radius: 20px;
    padding: 34px 38px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 24px;
    box-shadow: 0 18px 45px rgba(0, 0, 0, 0.65);
  }

  /* 9:16 Vertical Short Layout (1080x1920) */
  .short-frame {
    position: relative;
    width: 1080px;
    height: 1920px;
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
    padding: 44px 48px 48px 48px;
    gap: 22px;
  }
  .short-header {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 16px;
    background: rgba(16, 20, 29, 0.92);
    border: 1px solid rgba(232, 184, 90, 0.34);
    border-radius: 16px;
    padding: 22px 26px 18px 26px;
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.5);
  }
  .short-header .header-canon-badge {
    font-size: 36px;
    padding: 8px 18px;
  }
  .short-header .header-title-text {
    font-size: 30px;
    white-space: normal;
  }
  .short-frame .geom-stage-card {
    position: relative;
    flex: 0 0 800px;
    height: 800px;
    background: radial-gradient(circle at 50% 50%, #141926 0%, #0d111a 100%);
    border: 1.5px solid rgba(232, 184, 90, 0.36);
    border-radius: 20px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 24px 28px;
    box-shadow: inset 0 0 60px rgba(0, 0, 0, 0.65), 0 22px 55px rgba(0, 0, 0, 0.65);
    overflow: hidden;
  }
  .short-frame .svg-canvas-holder {
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .short-frame .svg-canvas-holder svg {
    width: 97% !important;
    height: 97% !important;
    max-height: 730px !important;
  }
  .short-frame .step-panel {
    background: rgba(16, 20, 29, 0.95);
    border: 1.5px solid rgba(232, 184, 90, 0.36);
    border-radius: 18px;
    padding: 32px 36px;
    display: flex;
    flex-direction: column;
    gap: 24px;
    box-shadow: 0 18px 45px rgba(0, 0, 0, 0.65);
  }

  /* Thumbnail Layouts (16:9 & 9:16) */
  .thumb-frame-16x9 {
    position: relative;
    width: 1920px;
    height: 1080px;
    padding: 36px;
    display: flex;
  }
  .thumb-frame-9x16 {
    position: relative;
    width: 1080px;
    height: 1920px;
    padding: 40px 40px 80px 40px;
    display: flex;
  }
  .thumb-card {
    position: relative;
    flex: 1;
    height: 100%;
    background: radial-gradient(circle at 54% 52%, #151b28 0%, #0c1018 100%);
    border: 2.5px solid rgba(232, 184, 90, 0.48);
    border-radius: 26px;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: inset 0 0 80px rgba(0, 0, 0, 0.7), 0 24px 60px rgba(0, 0, 0, 0.75);
    overflow: hidden;
  }
  .thumb-frame-16x9 .thumb-card {
    padding: 44px 120px 44px 200px;
  }
  .thumb-frame-9x16 .thumb-card {
    padding: 230px 56px 140px 56px;
  }
  .canon-title {
    position: absolute;
    top: 38px;
    left: 48px;
    font-family: Georgia, "Times New Roman", serif;
    font-size: 124px;
    font-weight: 700;
    letter-spacing: 0.04em;
    line-height: 1;
    color: #e8b85a;
    background: rgba(11, 14, 21, 0.92);
    border: 2px solid rgba(232, 184, 90, 0.55);
    padding: 20px 44px;
    border-radius: 18px;
    box-shadow: 0 18px 45px rgba(0, 0, 0, 0.75);
    z-index: 5;
  }
  .thumb-frame-16x9 .svg-canvas-holder,
  .thumb-frame-9x16 .svg-canvas-holder {
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .thumb-frame-16x9 .svg-canvas-holder svg {
    width: 100% !important;
    height: 100% !important;
    max-width: 1520px !important;
    max-height: 860px !important;
  }
  .thumb-frame-9x16 .svg-canvas-holder svg {
    width: 100% !important;
    height: 100% !important;
    max-width: 900px !important;
    max-height: 1380px !important;
  }

  /* Right Step Panel: Clean Single-Equation Typesetting */
  .step-panel-body {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    height: 100%;
    gap: 28px;
  }
  .step-panel-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }
  .step-kind-badge {
    font-family: "JetBrains Mono", monospace;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.14em;
    padding: 8px 20px;
    border-radius: 8px;
  }
  .just-ref-pill {
    font-family: "JetBrains Mono", monospace;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.06em;
    color: #e8b85a;
    background: rgba(232, 184, 90, 0.12);
    border: 1px solid rgba(232, 184, 90, 0.45);
    padding: 7px 16px;
    border-radius: 8px;
    white-space: nowrap;
  }
  .step-clause-wrap {
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
  }
  .step-clause {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 40px;
    line-height: 1.5;
    color: #f8f6f0;
    background: rgba(11, 14, 21, 0.72);
    border: 1px solid rgba(148, 163, 184, 0.24);
    border-radius: 16px;
    padding: 32px 34px;
  }
  .clause-line {
    display: block;
  }
  .clause-line + .clause-line {
    margin-top: 16px;
    padding-top: 16px;
    border-top: 1px solid rgba(148, 163, 184, 0.16);
  }
  .fn-tok {
    font-family: "JetBrains Mono", monospace;
    font-size: 32px;
    font-weight: 600;
    color: #cbd5e1;
  }
  .op-tok {
    display: inline-block;
    font-family: "JetBrains Mono", monospace;
    font-size: 34px;
    font-weight: 700;
    color: #94a3b8;
    margin: 0 8px;
  }
  .g-tok, .tok-gold, .eq-tok, .tok-green, .coincide-tok, .tok-blue {
    display: inline-block;
    font-family: "JetBrains Mono", monospace;
    font-size: 33px;
    font-weight: 700;
    padding: 2px 13px;
    border-radius: 8px;
    margin: 2px 3px;
  }
  .g-tok, .tok-gold {
    color: #e8b85a;
    background: rgba(232, 184, 90, 0.15);
    border: 1.5px solid rgba(232, 184, 90, 0.58);
  }
  .eq-tok, .tok-green {
    color: #5ec27e;
    background: rgba(94, 194, 126, 0.15);
    border: 1.5px solid rgba(94, 194, 126, 0.62);
  }
  .coincide-tok, .tok-blue {
    color: #38bdf8;
    background: rgba(56, 189, 248, 0.15);
    border: 1.5px solid rgba(56, 189, 248, 0.65);
  }

  /* ============================================================================
     INTRO (HEATH QUOTE ONLY), HARMONIC REVIEW LEDGER & OUTRO STAGE CARDS
     ============================================================================ */
  .intro-hero-wrap {
    display: flex;
    flex-direction: column;
    justify-content: center;
    height: 100%;
    gap: 24px;
  }
  .intro-enunciation-card {
    background: rgba(11, 14, 21, 0.85);
    border: 1.5px solid rgba(232, 184, 90, 0.42);
    border-left: 6px solid #e8b85a;
    border-radius: 18px;
    padding: 36px 38px;
    display: flex;
    flex-direction: column;
    gap: 22px;
  }
  .intro-enun-quote {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 38px;
    line-height: 1.44;
    color: #f8f6f0;
    font-style: italic;
  }
  .intro-enun-cite {
    font-family: "JetBrains Mono", monospace;
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: #e8b85a;
  }

  /* Harmonic Review: Clean Symbolic Proof Ledger */
  .review-panel-wrap {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-width: 0;
    justify-content: space-between;
    gap: 14px;
  }
  .review-ledger-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    flex: 1;
    min-width: 0;
    justify-content: center;
  }
  .review-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    min-width: 0;
    padding: 9px 16px;
    border-radius: 10px;
    background: rgba(11, 14, 21, 0.72);
    border: 1px solid rgba(148, 163, 184, 0.2);
    font-family: "JetBrains Mono", monospace;
    transition: all 0.15s ease;
  }
  .review-row.done {
    background: rgba(15, 23, 38, 0.88);
    border-color: rgba(232, 184, 90, 0.32);
    opacity: 0.88;
  }
  .review-row.current {
    background: rgba(232, 184, 90, 0.15);
    border: 1.5px solid #e8b85a;
    box-shadow: 0 0 22px rgba(232, 184, 90, 0.25);
  }
  .review-row.future {
    opacity: 0.28;
  }
  .rr-left {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    flex: 1;
  }
  .rr-status {
    width: 20px;
    text-align: center;
    font-size: 16px;
    font-weight: 700;
    flex-shrink: 0;
  }
  .rr-num {
    font-size: 14px;
    font-weight: 700;
    color: #94a3b8;
    flex-shrink: 0;
  }
  .rr-sym {
    font-size: 17px;
    font-weight: 600;
    color: #f8f6f0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
    flex: 1;
  }

  /* Outro: Pleas for Attention & Next Proposition */
  .outro-panel-wrap {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    height: 100%;
    gap: 16px;
  }
  .outro-headline {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 34px;
    font-weight: 700;
    color: #f8f6f0;
    line-height: 1.22;
  }
  .outro-cta-stack {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .outro-cta-card {
    display: flex;
    align-items: center;
    gap: 18px;
    background: rgba(11, 14, 21, 0.92);
    border: 1.5px solid rgba(232, 184, 90, 0.4);
    border-radius: 14px;
    padding: 15px 20px;
    box-shadow: 0 10px 26px rgba(0, 0, 0, 0.45);
  }
  .outro-cta-card.cta-primary {
    background: linear-gradient(90deg, rgba(232, 184, 90, 0.2) 0%, rgba(16, 20, 29, 0.95) 100%);
    border-color: #e8b85a;
  }
  .outro-cta-icon {
    width: 52px;
    height: 52px;
    border-radius: 12px;
    background: rgba(232, 184, 90, 0.16);
    border: 1.5px solid rgba(232, 184, 90, 0.55);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 26px;
    flex-shrink: 0;
  }
  .outro-cta-body {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .outro-cta-title {
    font-family: "JetBrains Mono", monospace;
    font-size: 18px;
    font-weight: 700;
    color: #e8b85a;
    letter-spacing: 0.04em;
  }
  .outro-cta-sub {
    font-size: 17.5px;
    color: #cbd5e1;
    line-height: 1.3;
  }
  .outro-next-banner {
    background: rgba(56, 189, 248, 0.11);
    border: 1.5px solid rgba(56, 189, 248, 0.45);
    border-radius: 12px;
    padding: 14px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    font-family: "JetBrains Mono", monospace;
  }
  .outro-next-label {
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.12em;
    color: #38bdf8;
  }
  .outro-next-val {
    font-size: 16.5px;
    font-weight: 700;
    color: #f8f6f0;
  }

  /* ============================================================================
     ETC TABLE, COORDINATE GRID & SEMANTIC TOKEN STYLING
     ============================================================================ */
  .tok-x1 { color: #ff5252; font-weight: 700; }
  .tok-x2 { color: #4caf50; font-weight: 700; }
  .tok-x3 { color: #ba68c8; font-weight: 700; }
  .tok-x4 { color: #29b6f6; font-weight: 700; }
  .tok-x5 { color: #ff9800; font-weight: 700; }
  .tok-ref { color: #d4a84b; font-weight: 700; }
  .tok-conic { color: #f06292; font-weight: 700; }
  .tok-line { color: #ffd54f; font-weight: 700; }
  .tok-tri { color: #4dd0e1; font-weight: 700; }
  .tok-circle { color: #26a69a; font-weight: 700; }

  .etc-table-block {
    display: flex;
    flex-direction: column;
    gap: 10px;
    width: 100%;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  .etc-sec-head {
    font-family: "JetBrains Mono", monospace;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.12em;
    color: #e8b85a;
    text-transform: uppercase;
    border-bottom: 1px solid rgba(232, 184, 90, 0.35);
    padding-bottom: 4px;
    margin-top: 4px;
  }
  .etc-data-row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 14px;
    background: rgba(11, 14, 21, 0.85);
    border: 1px solid rgba(148, 163, 184, 0.22);
    border-radius: 10px;
    padding: 9px 14px;
  }
  .etc-row-lbl {
    font-family: "JetBrains Mono", monospace;
    font-size: 14px;
    font-weight: 700;
    color: #94a3b8;
    flex-shrink: 0;
  }
  .etc-row-val {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 20px;
    font-weight: 600;
    color: #f8f6f0;
    text-align: right;
  }
  .etc-prose-row {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 22px;
    line-height: 1.38;
    color: #e2e8f0;
    padding: 4px 2px;
  }
  .etc-coord-grid {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .etc-coord-card {
    background: rgba(11, 14, 21, 0.9);
    border: 1px solid rgba(232, 184, 90, 0.35);
    border-radius: 10px;
    padding: 10px 14px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }
  .etc-coord-lbl {
    font-family: "JetBrains Mono", monospace;
    font-size: 13px;
    font-weight: 700;
    color: #e8b85a;
    text-transform: uppercase;
  }
  .etc-coord-expr {
    font-family: "JetBrains Mono", monospace;
    font-size: 18px;
    font-weight: 700;
    color: #f8f6f0;
  }
  .etc-Active {
    filter: drop-shadow(0 0 8px rgba(232, 184, 90, 0.65));
  }

  /* ============================================================================
     LIVE STATUS PILLS, LIVE SCRIPT EDITOR & STATUS MATRIX MODAL
     ============================================================================ */
  .status-pill-group {
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .st-pill {
    font-family: "JetBrains Mono", monospace;
    font-size: 10.5px;
    font-weight: 700;
    padding: 4px 8px;
    border-radius: 5px;
    letter-spacing: 0.04em;
    white-space: nowrap;
    border: 1px solid transparent;
    cursor: pointer;
  }
  .st-pill.st-ready {
    color: #5ec27e;
    background: rgba(94, 194, 126, 0.14);
    border-color: rgba(94, 194, 126, 0.45);
  }
  .st-pill.st-stale, .st-pill.st-partial {
    color: #f59e0b;
    background: rgba(245, 158, 11, 0.15);
    border-color: rgba(245, 158, 11, 0.5);
  }
  .st-pill.st-missing, .st-pill.st-local {
    color: #94a3b8;
    background: rgba(148, 163, 184, 0.12);
    border-color: rgba(148, 163, 184, 0.3);
  }
  .st-pill.st-unlisted, .st-pill.st-public {
    color: #38bdf8;
    background: rgba(56, 189, 248, 0.14);
    border-color: rgba(56, 189, 248, 0.45);
  }

  /* Live Editor Sidebar Tab */
  .editor-toolbar {
    display: flex;
    flex-direction: column;
    gap: 8px;
    background: #0a0d14;
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    padding: 10px;
  }
  .editor-variant-tabs {
    display: flex;
    gap: 4px;
    background: #06080d;
    padding: 3px;
    border-radius: 6px;
    border: 1px solid rgba(148, 163, 184, 0.18);
  }
  .ed-var-btn {
    flex: 1;
    font-family: "JetBrains Mono", monospace;
    font-size: 10.5px;
    font-weight: 700;
    color: var(--text-muted);
    background: transparent;
    border: none;
    padding: 5px 6px;
    border-radius: 4px;
    cursor: pointer;
    text-align: center;
  }
  .ed-var-btn.active {
    color: #0b0e15;
    background: var(--gold);
  }
  .editor-actions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .act-btn {
    flex: 1;
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    font-weight: 700;
    padding: 7px 10px;
    border-radius: 6px;
    cursor: pointer;
    border: 1px solid transparent;
    transition: all 0.15s ease;
    white-space: nowrap;
    text-align: center;
  }
  .act-btn.btn-save {
    color: #0b0e15;
    background: var(--gold);
    border-color: var(--gold);
  }
  .act-btn.btn-save:hover { filter: brightness(1.08); }
  .act-btn.btn-speak {
    color: #38bdf8;
    background: rgba(56, 189, 248, 0.14);
    border-color: rgba(56, 189, 248, 0.45);
  }
  .act-btn.btn-speak:hover { background: rgba(56, 189, 248, 0.24); }
  .act-btn.btn-render {
    color: #5ec27e;
    background: rgba(94, 194, 126, 0.14);
    border-color: rgba(94, 194, 126, 0.45);
  }
  .act-btn.btn-render:hover { background: rgba(94, 194, 126, 0.24); }
  .act-btn:disabled {
    opacity: 0.45;
    cursor: wait;
  }

  .quick-step-box {
    background: #0a0d14;
    border: 1px solid rgba(232, 184, 90, 0.35);
    border-radius: 8px;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 7px;
  }
  .quick-field {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .quick-field label {
    font-family: "JetBrains Mono", monospace;
    font-size: 10px;
    font-weight: 700;
    color: var(--text-muted);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .quick-input {
    width: 100%;
    font-family: "JetBrains Mono", monospace;
    font-size: 11.5px;
    color: #f8f6f0;
    background: #111723;
    border: 1px solid rgba(148, 163, 184, 0.28);
    border-radius: 5px;
    padding: 5px 8px;
    outline: none;
  }
  .quick-input:focus {
    border-color: var(--gold);
  }

  .scr-textarea {
    width: 100%;
    min-height: 310px;
    flex: 1;
    font-family: "JetBrains Mono", monospace;
    font-size: 11.5px;
    line-height: 1.5;
    color: #f4f1ea;
    background: #070a0f;
    border: 1px solid rgba(148, 163, 184, 0.28);
    border-radius: 8px;
    padding: 10px;
    resize: vertical;
    outline: none;
    tab-size: 2;
  }
  .scr-textarea:focus {
    border-color: var(--gold);
  }

  .toast-banner {
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    padding: 7px 10px;
    border-radius: 6px;
    background: rgba(56, 189, 248, 0.14);
    border: 1px solid rgba(56, 189, 248, 0.45);
    color: #e2e8f0;
    display: none;
    line-height: 1.35;
  }
  .toast-banner.visible {
    display: block;
  }

  /* Status Matrix Modal */
  .matrix-modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(4, 6, 10, 0.86);
    backdrop-filter: blur(6px);
    z-index: 150;
    display: none;
    align-items: center;
    justify-content: center;
    padding: 28px;
  }
  .matrix-modal-backdrop.open {
    display: flex;
  }
  .matrix-modal {
    width: min(1240px, 96vw);
    height: min(840px, 90vh);
    background: #0d111a;
    border: 1.5px solid rgba(232, 184, 90, 0.45);
    border-radius: 14px;
    box-shadow: 0 28px 80px rgba(0, 0, 0, 0.85);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .matrix-header {
    padding: 14px 20px;
    background: #121826;
    border-bottom: 1px solid var(--border-subtle);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .matrix-title {
    font-family: "JetBrains Mono", monospace;
    font-size: 14px;
    font-weight: 700;
    color: var(--gold);
    letter-spacing: 0.08em;
  }
  .matrix-summary-bar {
    padding: 10px 20px;
    background: #090d14;
    border-bottom: 1px solid var(--border-subtle);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .matrix-kpis {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
  }
  .kpi-chip {
    font-family: "JetBrains Mono", monospace;
    font-size: 11.5px;
    padding: 4px 10px;
    border-radius: 6px;
    background: #131926;
    border: 1px solid var(--border-subtle);
    color: #cbd5e1;
  }
  .kpi-chip strong {
    color: var(--gold);
  }
  .matrix-search {
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    color: #f8f6f0;
    background: #131926;
    border: 1px solid var(--border-subtle);
    border-radius: 6px;
    padding: 6px 12px;
    width: 240px;
    outline: none;
  }
  .matrix-search:focus {
    border-color: var(--gold);
  }
  .matrix-table-wrap {
    flex: 1;
    overflow: auto;
    padding: 0;
  }
  .matrix-table {
    width: 100%;
    border-collapse: collapse;
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
  }
  .matrix-table th {
    position: sticky;
    top: 0;
    background: #111723;
    color: var(--text-muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid var(--border-subtle);
    z-index: 2;
  }
  .matrix-table td {
    padding: 9px 14px;
    border-bottom: 1px solid rgba(148, 163, 184, 0.12);
    color: #e2e8f0;
  }
  .matrix-table tr:hover td {
    background: rgba(232, 184, 90, 0.07);
    cursor: pointer;
  }
  .matrix-table tr.active-pkg td {
    background: rgba(232, 184, 90, 0.14);
  }
</style>
</head>
<body class="smooth-transitions">
  <div class="director-app">
    <!-- Top Director Toolbar -->
    <header class="director-topbar">
      <div class="topbar-left">
        <span class="studio-brand">GEOMETOR STAGE</span>
        <select id="project-select" class="prop-select" style="max-width:145px;" title="Switch Project Repository (Elements / ETC)">
          <option value="current">PROJECT</option>
        </select>
        <button class="tb-btn" id="btn-prev-pkg" onclick="stepPackage(-1)" title="Previous Video Package ([)">◀</button>
        <select id="prop-select" class="prop-select" title="Switch Video Package">
          <option value="__PREFIX__">__SHORT_CODE__ • __SHORT_TITLE__</option>
        </select>
        <button class="tb-btn" id="btn-next-pkg" onclick="stepPackage(1)" title="Next Video Package (])">▶</button>
        <button class="tb-btn" id="btn-matrix" onclick="toggleMatrixModal()" title="Open Project Status Matrix & Navigator (M)">📊 Matrix</button>
      </div>

      <div class="topbar-center">
        <div class="btn-group" id="format-tabs">
          <button class="tb-btn active" data-fmt="16x9" onclick="setFormat('16x9')">16:9 QHD</button>
          <button class="tb-btn" data-fmt="9x16" onclick="setFormat('9x16')">9:16 Short</button>
          <button class="tb-btn" data-fmt="thumb_16x9" onclick="setFormat('thumb_16x9')">16:9 Thumb</button>
          <button class="tb-btn" data-fmt="thumb_9x16" onclick="setFormat('thumb_9x16')">9:16 Thumb</button>
        </div>

        <div class="btn-group" id="scale-tabs">
          <button class="tb-btn active" data-scale="fit" onclick="setScaleMode('fit')">Fit</button>
          <button class="tb-btn" data-scale="0.5" onclick="setScaleMode(0.5)">50%</button>
          <button class="tb-btn" data-scale="0.75" onclick="setScaleMode(0.75)">75%</button>
          <button class="tb-btn" data-scale="1.0" onclick="setScaleMode(1.0)">100%</button>
          <button class="tb-btn" data-scale="1.3333" onclick="setScaleMode(1.3333)">1440p</button>
        </div>
      </div>

      <div class="topbar-right">
        <div class="status-pill-group" id="status-pills"></div>
        <span class="res-readout" id="res-readout">2560×1440 QHD • Fit</span>
        <button class="tb-btn" id="btn-guides" onclick="toggleGuides()" title="Toggle YouTube Safe Zones & Golden Guides (G)">Guides (G)</button>
        <button class="tb-btn toggle-on" id="btn-inspector" onclick="toggleSidebar()" title="Toggle Sidebar (I)">Studio (I)</button>
      </div>
    </header>

    <!-- Main Center Stage + Right Sidebar -->
    <div class="director-workspace">
      <div class="stage-viewport" id="stage-viewport">
        <div class="stage-bezel-label" id="bezel-label">STAGE • 16:9 BROADCAST MASTER (1920×1080 CSS → 2560×1440 RASTER)</div>
        <div id="stage-scaler">
          <div id="stage-mount">
            <div id="stage-canvas" class="fmt-16x9"></div>
            <div class="safe-zone-overlay" id="safe-zone-overlay"></div>
          </div>
        </div>
      </div>

      <aside class="director-sidebar" id="director-sidebar" style="width:450px;">
        <div class="sidebar-tabs">
          <button class="sb-tab active" id="tab-steps" onclick="setSidebarTab('steps')">Steps (__STEP_TOTAL__)</button>
          <button class="sb-tab" id="tab-editor" onclick="setSidebarTab('editor')">✏️ Live Editor</button>
          <button class="sb-tab" id="tab-inspector" onclick="setSidebarTab('inspector')">SVG & Vocab</button>
        </div>
        <div class="sidebar-body" id="sidebar-steps"></div>
        <div class="sidebar-body" id="sidebar-editor" style="display:none;">
          <div class="editor-toolbar">
            <div class="insp-title" style="display:flex;justify-content:space-between;align-items:center;">
              <span>SCRIPT VARIANT TARGET</span>
              <span id="editor-file-label" style="color:var(--gold);">script.scr</span>
            </div>
            <div class="editor-variant-tabs">
              <button class="ed-var-btn active" data-var="shared" onclick="setEditorVariant('shared')">Shared (script.scr)</button>
              <button class="ed-var-btn" data-var="wide" onclick="setEditorVariant('wide')">16:9 Wide Override</button>
              <button class="ed-var-btn" data-var="short" onclick="setEditorVariant('short')">9:16 Short Override</button>
            </div>
            <div class="editor-actions">
              <button class="act-btn btn-save" id="btn-save-scr" onclick="saveLiveScript()" title="Save script & hot-reload preview (Ctrl+S)">💾 Save & Preview</button>
              <button class="act-btn btn-speak" id="btn-regen-tts" onclick="runLiveSpeak()" title="Synthesize TTS audio & harmonic cues for modified lines">🎙️ Regen Audio</button>
              <button class="act-btn btn-render" id="btn-render-mp4" onclick="runLiveRender()" title="Incrementally build dirty segments & assemble 1440p + Short MP4s">🎬 Build & Assemble</button>
            </div>
            <div class="toast-banner" id="editor-toast"></div>
          </div>

          <div class="quick-step-box" id="editor-segments-box">
            <div class="insp-title" style="display:flex;justify-content:space-between;align-items:center;">
              <span>SEGMENT FOLDERS (segments/&lt;NN&gt;_&lt;section&gt;/)</span>
              <span id="segments-summary-label" style="color:var(--green);"></span>
            </div>
            <div id="editor-segments-list" style="display:flex;flex-direction:column;gap:5px;"></div>
          </div>

          <div class="quick-step-box" id="quick-step-box">
            <div class="insp-title" style="display:flex;justify-content:space-between;align-items:center;">
              <span id="quick-step-title">ACTIVE STEP QUICK-EDIT</span>
              <button class="tb-btn" style="padding:2px 7px;font-size:10px;border:1px solid var(--border-gold);color:var(--gold);" onclick="applyQuickStepToTextarea()">Apply to Script ↓</button>
            </div>
            <div class="quick-field">
              <label>Spoken Line (say: — keep concise, e.g. "Construct circle A B.")</label>
              <input type="text" class="quick-input" id="qe-say" placeholder="Construct circle A B. [sfx:circle]" />
            </div>
            <div style="display:flex;gap:6px;">
              <div class="quick-field" style="flex:1;">
                <label>16:9 Spoken Override (say_wide:)</label>
                <input type="text" class="quick-input" id="qe-say-wide" placeholder="Optional wider detail..." />
              </div>
              <div class="quick-field" style="flex:1;">
                <label>9:16 Spoken Override (say_short:)</label>
                <input type="text" class="quick-input" id="qe-say-short" placeholder="Optional short version..." />
              </div>
            </div>
            <div class="quick-field">
              <label>Display Clause (clause:) & Symbolic Header (symbolic:)</label>
              <div style="display:flex;gap:6px;">
                <input type="text" class="quick-input" id="qe-clause" style="flex:2;" placeholder="Construct circle $A B$." />
                <input type="text" class="quick-input" id="qe-symbolic" style="flex:1;" placeholder="◯ A(B)" />
              </div>
            </div>
            <div style="display:flex;gap:6px;">
              <div class="quick-field" style="flex:1;">
                <label>Active (active:)</label>
                <input type="text" class="quick-input" id="qe-active" placeholder="A, B, (AB)" />
              </div>
              <div class="quick-field" style="flex:1;">
                <label>Equal (equal:)</label>
                <input type="text" class="quick-input" id="qe-equal" placeholder="/AB/, /AC/" />
              </div>
            </div>
          </div>

          <div style="display:flex;flex-direction:column;flex:1;gap:4px;min-height:260px;">
            <div class="insp-title" style="display:flex;justify-content:space-between;align-items:center;">
              <span>FULL SCRIPT SOURCE (SUPPORTS say_wide: / say_short: / only: wide|short)</span>
              <button class="tb-btn" id="btn-seed-variant" style="display:none;padding:2px 6px;font-size:10px;color:var(--blue);" onclick="seedVariantFromShared()">Copy from Shared</button>
            </div>
            <textarea class="scr-textarea" id="scr-editor-textarea" spellcheck="false"></textarea>
          </div>
        </div>
        <div class="sidebar-body" id="sidebar-inspector" style="display:none;"></div>
      </aside>
    </div>

    <!-- Bottom Transport & Audio Scrub Bar -->
    <footer class="director-transport">
      <button class="trans-btn" onclick="stepTo(0)" title="First Step (Home)">⏮</button>
      <button class="trans-btn" onclick="stepDelta(-1)" title="Previous Step (←)">◀ Prev</button>
      <button class="trans-btn" id="btn-play" onclick="togglePlay()" title="Play / Pause Audio & Sequence (Space)">▶ Play</button>
      <button class="trans-btn" onclick="stepDelta(1)" title="Next Step (→)">Next ▶</button>
      <button class="trans-btn" onclick="stepTo(STAGE.slides.length - 1)" title="Final Step (End)">⏭</button>

      <div class="timeline-segments" id="timeline-segments"></div>
      <div class="time-code" id="time-code">00.0s / 00.0s</div>
      <audio id="stage-audio" preload="auto"></audio>
    </footer>
  </div>

  <!-- Segment Clip Preview Modal -->
  <div class="matrix-modal-backdrop" id="clip-preview-modal" onclick="if(event.target===this)closeClipModal()">
    <div class="matrix-modal" style="max-width:960px;height:auto;max-height:90vh;">
      <div class="matrix-header">
        <span class="matrix-title" id="clip-modal-title">SEGMENT CLIP PREVIEW</span>
        <button class="tb-btn" onclick="closeClipModal()">✕ Close (Esc)</button>
      </div>
      <div style="padding:16px;display:flex;flex-direction:column;align-items:center;gap:10px;background:#05070b;">
        <video id="clip-modal-video" controls style="max-width:100%;max-height:70vh;border:1px solid var(--border-gold);border-radius:8px;background:#000;"></video>
        <div id="clip-modal-meta" style="font-family:'JetBrains Mono',monospace;font-size:11.5px;color:var(--text-muted);"></div>
      </div>
    </div>
  </div>

  <!-- Project Status Matrix Modal -->
  <div class="matrix-modal-backdrop" id="matrix-modal" onclick="if(event.target===this)toggleMatrixModal()">
    <div class="matrix-modal">
      <div class="matrix-header">
        <span class="matrix-title" id="matrix-modal-title">GEOMETOR STUDIO • PROJECT STATUS MATRIX</span>
        <div style="display:flex;gap:8px;align-items:center;">
          <div class="btn-group" id="matrix-filter-tabs">
            <button class="tb-btn active" data-flt="all" onclick="setMatrixFilter('all')">All</button>
            <button class="tb-btn" data-flt="ready" onclick="setMatrixFilter('ready')">Rendered</button>
            <button class="tb-btn" data-flt="stale" onclick="setMatrixFilter('stale')">Stale</button>
            <button class="tb-btn" data-flt="missing" onclick="setMatrixFilter('missing')">Unrendered</button>
            <button class="tb-btn" data-flt="youtube" onclick="setMatrixFilter('youtube')">On YouTube</button>
          </div>
          <button class="tb-btn" onclick="toggleMatrixModal()">✕ Close (Esc)</button>
        </div>
      </div>
      <div class="matrix-summary-bar">
        <div class="matrix-kpis" id="matrix-kpis"></div>
        <input type="text" class="matrix-search" id="matrix-search" placeholder="Filter by code or title..." oninput="renderMatrixTable()" />
      </div>
      <div class="matrix-table-wrap">
        <table class="matrix-table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Title</th>
              <th>Script</th>
              <th>16:9 / 9:16</th>
              <th>Audio (TTS)</th>
              <th>Render (1440p + Short)</th>
              <th>YouTube</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody id="matrix-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="svg-hover-tooltip" id="svg-tooltip"></div>

  <script>
    let STAGE = __STAGE_JSON__;
    let currentStep = 0;
    let currentFormat = '16x9'; // '16x9' | '9x16' | 'thumb_16x9' | 'thumb_9x16'
    let scaleMode = 'fit';      // 'fit' | number
    let showGuides = false;
    let isPlaying = false;
    let syntheticTimer = null;
    let webAudioCtx = null;
    let editorVariant = 'shared'; // 'shared' | 'wide' | 'short'
    let editorDirty = false;
    let catalogData = null;
    let matrixFilter = 'all';

    function activeSlidesDeck(fmt) {
      const f = fmt || currentFormat;
      if ((f === '9x16' || f === 'thumb_9x16') && STAGE.slides_9x16 && STAGE.slides_9x16.length) {
        return STAGE.slides_9x16;
      }
      return STAGE.slides_16x9 || STAGE.slides || [];
    }

    // Exact Harmonic Chime Specifications matching voice_profile.json & speak.py
    const HARMONIC_SPECS = {
      '[bell:context]':      { freqs: [440.0, 660.0], dur: 0.45, amp: 0.14 },
      '[bell:proposition]':  { freqs: [440.0, 880.0], dur: 0.45, amp: 0.15 },
      '[bell:construction]': { freqs: [528.0, 792.0], dur: 0.45, amp: 0.15 },
      '[bell:insight]':      { freqs: [440.0, 711.935, 880.0], dur: 0.68, amp: 0.16 },
      '[bell:punchline]':    { freqs: [587.33, 880.0, 1174.66], dur: 0.40, amp: 0.14 },
      '[sfx:point]':         { freqs: [1046.5, 1567.98], dur: 0.14, amp: 0.10 },
      '[sfx:segment]':       { freqs: [440.0, 659.25, 880.0], dur: 0.22, amp: 0.11 },
      '[sfx:line]':          { freqs: [440.0, 659.25, 880.0], dur: 0.22, amp: 0.11 },
      '[sfx:circle]':        { freqs: [392.0, 587.33, 783.99, 1174.66], dur: 0.30, amp: 0.11 },
      '[sfx:intersect]':     { freqs: [880.0, 1318.51], dur: 0.18, amp: 0.12 },
      '[sfx:concur]':        { freqs: [523.25, 659.25, 783.99, 1046.5], dur: 0.26, amp: 0.12 },
      '[sfx:equal]':         { freqs: [523.25, 783.99], dur: 0.24, amp: 0.12 },
      '[sfx:ratio]':         { freqs: [493.88, 739.99, 987.77], dur: 0.24, amp: 0.11 },
      '[sfx:conjugate]':     { freqs: [659.25, 523.25, 783.99], dur: 0.25, amp: 0.11 },
      '[sfx:invariant]':     { freqs: [349.23, 523.25, 698.46, 1046.5], dur: 0.32, amp: 0.12 },
      '[sfx:qed]':           { freqs: [523.25, 659.25, 783.99, 1046.5], dur: 0.38, amp: 0.14 },
    };

    function playHarmonicCueList(cues) {
      if (isHeadless || !cues || !cues.length) return;
      try {
        if (!webAudioCtx) {
          const AudioContextClass = window.AudioContext || window.webkitAudioContext;
          if (!AudioContextClass) return;
          webAudioCtx = new AudioContextClass();
        }
        if (webAudioCtx.state === 'suspended') {
          webAudioCtx.resume();
        }
        let offset = 0;
        cues.forEach((rawCue) => {
          const key = String(rawCue).toLowerCase();
          const spec = HARMONIC_SPECS[key];
          if (!spec) return;
          const startT = webAudioCtx.currentTime + offset;
          const weights = spec.freqs.map((_, i) => 1.0 / (i + 1));
          const wSum = weights.reduce((a, b) => a + b, 0);
          spec.freqs.forEach((f, idx) => {
            const osc = webAudioCtx.createOscillator();
            const gain = webAudioCtx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(f, startT);
            const peak = (weights[idx] / wSum) * spec.amp * 1.4;
            gain.gain.setValueAtTime(0.0001, startT);
            gain.gain.linearRampToValueAtTime(peak, startT + 0.008);
            gain.gain.exponentialRampToValueAtTime(0.0001, startT + spec.dur);
            osc.connect(gain);
            gain.connect(webAudioCtx.destination);
            osc.start(startT);
            osc.stop(startT + spec.dur + 0.02);
          });
          offset += key.startsWith('[bell:') ? (spec.dur * 0.65) : 0.12;
        });
      } catch (e) {}
    }

    const urlParams = new URLSearchParams(window.location.search);
    const isHeadless = urlParams.get('headless') === '1';
    if (isHeadless) {
      document.body.classList.add('headless-mode');
      document.body.classList.remove('smooth-transitions');
    }

    const viewportEl = document.getElementById('stage-viewport');
    const scalerEl = document.getElementById('stage-scaler');
    const mountEl = document.getElementById('stage-mount');
    const canvasEl = document.getElementById('stage-canvas');
    const overlayEl = document.getElementById('safe-zone-overlay');
    const audioEl = document.getElementById('stage-audio');
    const tooltipEl = document.getElementById('svg-tooltip');

    function escapeHtml(str) {
      const div = document.createElement('div');
      div.textContent = str == null ? '' : String(str);
      return div.innerHTML;
    }

    function applySvgStepState(slide, isThumb) {
      const svg = canvasEl.querySelector('#svg-holder svg');
      if (!svg) return;
      const svgNs = 'http://www.w3.org/2000/svg';
      const isEtcDrawing = svg.classList.contains('geometor-etc-drawing');

      if (!isEtcDrawing) {
        // 1. Ensure label clearance below points
        Array.from(svg.querySelectorAll('#labels text')).forEach((txtEl) => {
          const id = txtEl.getAttribute('id') || '';
          if (!id.startsWith('lbl-')) return;
          const ptEl = svg.querySelector('#el-pt-' + CSS.escape(id.slice(4)));
          if (!ptEl) return;
          const tx = parseFloat(txtEl.getAttribute('x') || '0');
          const ty = parseFloat(txtEl.getAttribute('y') || '0');
          const cx = parseFloat(ptEl.getAttribute('cx') || '0');
          const cy = parseFloat(ptEl.getAttribute('cy') || '0');
          const dy = ty - cy;
          if (Math.abs(tx - cx) < 10 && dy > 0 && dy < 17) {
            txtEl.setAttribute('y', String(ty + 3.5));
          }
        });

        // 2. Ensure equality tick markers exist in <defs>
        let defs = svg.querySelector('defs');
        if (!defs) {
          defs = document.createElementNS(svgNs, 'defs');
          svg.insertBefore(defs, svg.firstChild);
        }
        if (!defs.querySelector('#geom-tick-1')) {
          const markerSpecs = [
            { id: 'geom-tick-1', xs: [0] },
            { id: 'geom-tick-2', xs: [-1.8, 1.8] },
            { id: 'geom-tick-3', xs: [-3.2, 0, 3.2] },
          ];
          markerSpecs.forEach((spec) => {
            const m = document.createElementNS(svgNs, 'marker');
            m.setAttribute('id', spec.id);
            m.setAttribute('viewBox', '-6 -6 12 12');
            m.setAttribute('refX', '0');
            m.setAttribute('refY', '0');
            m.setAttribute('markerWidth', '12');
            m.setAttribute('markerHeight', '12');
            m.setAttribute('markerUnits', 'userSpaceOnUse');
            m.setAttribute('orient', 'auto');
            spec.xs.forEach((xVal) => {
              const ln = document.createElementNS(svgNs, 'line');
              ln.setAttribute('x1', String(xVal));
              ln.setAttribute('y1', '-4.5');
              ln.setAttribute('x2', String(xVal));
              ln.setAttribute('y2', '4.5');
              ln.setAttribute('class', 'geom-tick-mark');
              m.appendChild(ln);
            });
            defs.appendChild(m);
          });
        }

        // 3. Convert <line> to 3-point <path> so marker-mid renders equality ticks
        Array.from(svg.querySelectorAll('line')).forEach((lineEl) => {
          if (lineEl.closest('defs')) return;
          const x1 = parseFloat(lineEl.getAttribute('x1') || '0');
          const y1 = parseFloat(lineEl.getAttribute('y1') || '0');
          const x2 = parseFloat(lineEl.getAttribute('x2') || '0');
          const y2 = parseFloat(lineEl.getAttribute('y2') || '0');
          const mx = (x1 + x2) / 2;
          const my = (y1 + y2) / 2;
          const p = document.createElementNS(svgNs, 'path');
          Array.from(lineEl.attributes).forEach((attr) => {
            if (!['x1', 'y1', 'x2', 'y2'].includes(attr.name)) {
              p.setAttribute(attr.name, attr.value);
            }
          });
          p.setAttribute('d', `M ${x1} ${y1} L ${mx} ${my} L ${x2} ${y2}`);
          lineEl.parentNode.replaceChild(p, lineEl);
        });
      }

      const ALL_COLOR_CLASSES = [
        'geom-highlight-active', 'geom-coincide-active', 'geom-assert-active',
        'geom-color-gold', 'geom-color-orange', 'geom-color-teal', 'geom-color-blue',
        'geom-color-purple', 'geom-color-orchid', 'geom-color-pink', 'geom-color-green', 'geom-color-lime',
      ];

      // 4. Reset classes on all controllable SVG elements
      svg.classList.remove('has-step-highlights');
      const controllable = svg.querySelectorAll('#elements > *, #graphics > *, #points > *, #labels > *');
      controllable.forEach((el) => {
        el.classList.remove(
          'geom-step-hidden', 'geom-step-ghost', 'geom-active-any',
          'geom-tick-1', 'geom-tick-2', 'geom-tick-3',
          ...ALL_COLOR_CLASSES
        );
      });

      // 5. Apply visibility
      const visSet = new Set(slide.visible_ids || ['*']);
      const showAll = visSet.has('*');
      if (!showAll) {
        controllable.forEach((el) => {
          const id = el.getAttribute('id') || '';
          if (id && !visSet.has(id)) {
            el.classList.add('geom-step-hidden');
          }
        });
      }

      // 6. Hide suppressed IDs (e.g. hide: D, E)
      (slide.hidden_ids || []).forEach((id) => {
        const el = svg.querySelector('#' + CSS.escape(id));
        if (el) el.classList.add('geom-step-hidden');
      });

      let anyHighlighted = false;
      const vbParts = (svg.getAttribute('viewBox') || '0 0 800 600').trim().split(/[\s,]+/).map(parseFloat);
      const vbWidth = (vbParts.length >= 4 && vbParts[2] > 0) ? vbParts[2] : 800;
      const colTol = vbWidth * 0.0003;

      const markElement = (el, cls, tickCls) => {
        if (!el) return;
        const effCls = (isEtcDrawing && el.classList.contains('target')) ? 'geom-color-gold' : cls;
        el.classList.remove('geom-step-hidden', 'geom-step-ghost');
        const keepGold = tickCls && !isEtcDrawing && el.classList.contains('geom-highlight-active');
        if (!keepGold) {
          el.classList.remove(...ALL_COLOR_CLASSES);
          el.classList.add('geom-active-any', effCls);
        } else {
          el.classList.add('geom-assert-active');
        }
        if (tickCls) el.classList.add(tickCls);
        anyHighlighted = true;
        const elId = el.getAttribute('id') || el.getAttribute('data-id') || '';
        if (elId) {
          const hlEl = svg.querySelector('#hl-' + CSS.escape(elId)) ||
                       svg.querySelector('[data-highlight-for="' + CSS.escape(elId) + '"]');
          if (hlEl) {
            hlEl.classList.remove('geom-step-hidden', 'geom-step-ghost', ...ALL_COLOR_CLASSES);
            hlEl.classList.add('geom-active-any', effCls);
          }
          const lblEl = svg.querySelector('#lbl-' + CSS.escape(elId)) ||
                        svg.querySelector('[data-point-id="' + CSS.escape(elId) + '"]');
          if (lblEl) {
            lblEl.classList.remove('geom-step-hidden', 'geom-step-ghost', ...ALL_COLOR_CLASSES);
            lblEl.classList.add('geom-active-any', effCls);
          }
        }
        if (elId.startsWith('el-seg-')) {
          const hlSeg = svg.querySelector('#hl-seg-' + CSS.escape(elId.slice(7)));
          if (hlSeg) {
            hlSeg.classList.remove('geom-step-hidden');
            hlSeg.classList.add('geom-active-any', effCls);
          }
        }
      };

      const highlightLineThroughCenters = (nums, cls, tickCls) => {
        const ptSet = new Set(nums.map((n) => 'X_' + n));
        const coords = [];
        nums.forEach((n) => {
          const ptId = 'X_' + n;
          const ptEl = svg.querySelector('#' + CSS.escape(ptId)) ||
                       svg.querySelector('[data-id="' + CSS.escape(ptId) + '"]');
          if (ptEl) {
            markElement(ptEl, cls, null);
            coords.push({
              x: parseFloat(ptEl.getAttribute('cx') || '0'),
              y: parseFloat(ptEl.getAttribute('cy') || '0'),
            });
          }
        });
        let matchedByPtAttrs = false;
        Array.from(svg.querySelectorAll('#elements line, #elements path.line')).forEach((lnEl) => {
          const p1 = lnEl.getAttribute('data-pt1') || '';
          const p2 = lnEl.getAttribute('data-pt2') || '';
          if (p1 && p2 && ptSet.has(p1) && ptSet.has(p2)) {
            markElement(lnEl, cls, tickCls);
            matchedByPtAttrs = true;
          }
        });
        if (!matchedByPtAttrs && coords.length >= 2) {
          Array.from(svg.querySelectorAll('#elements line, #elements path.line')).forEach((lnEl) => {
            if (!lnEl.hasAttribute('x1')) return;
            const x1 = parseFloat(lnEl.getAttribute('x1') || '0');
            const y1 = parseFloat(lnEl.getAttribute('y1') || '0');
            const x2 = parseFloat(lnEl.getAttribute('x2') || '0');
            const y2 = parseFloat(lnEl.getAttribute('y2') || '0');
            const len = Math.hypot(x2 - x1, y2 - y1);
            if (len < 1e-6) return;
            let hits = 0;
            coords.forEach((pt) => {
              const dist = Math.abs((y2 - y1) * pt.x - (x2 - x1) * pt.y + x2 * y1 - y2 * x1) / len;
              if (dist < colTol) hits += 1;
            });
            if (hits >= 2) markElement(lnEl, cls, tickCls);
          });
        }
      };

      const applyTokenHighlight = (rawToken, defaultCls, tickCls) => {
        let token = String(rawToken || '').trim().replace(/^[△Δ]\s*/, '');
        let explicitColor = '';
        const colMatch = token.match(/^(.*):(gold|orange|teal|blue|purple|orchid|pink|green|lime|muted)$/i);
        if (colMatch) {
          token = colMatch[1].trim();
          explicitColor = colMatch[2].toLowerCase();
        }
        if (!token || token === 'ABC' || explicitColor === 'muted') return;
        const resolveCls = (el) => {
          if (explicitColor) return 'geom-color-' + explicitColor;
          if (el) {
            const dc = el.getAttribute('data-color-token');
            if (dc) return 'geom-color-' + dc;
          }
          return defaultCls;
        };
        const directEl = svg.querySelector('#' + CSS.escape(token)) ||
                         svg.querySelector('[data-id="' + CSS.escape(token) + '"]');
        if (directEl) {
          const chosenCls = resolveCls(directEl);
          markElement(directEl, chosenCls, tickCls);
          if ((directEl.tagName || '').toLowerCase() === 'line') {
            ['data-pt1', 'data-pt2'].forEach((attr) => {
              const pid = directEl.getAttribute(attr) || '';
              if (pid && !['A', 'B', 'C'].includes(pid)) {
                const pEl = svg.querySelector('#' + CSS.escape(pid));
                if (pEl) markElement(pEl, chosenCls, null);
              }
            });
          }
          return;
        }
        if (token.startsWith('{') && token.endsWith('}')) {
          const nums = (token.match(/\d+/g) || []).map((n) => parseInt(n, 10));
          const chosenCls = explicitColor ? ('geom-color-' + explicitColor) : defaultCls;
          highlightLineThroughCenters(nums, chosenCls, tickCls);
          return;
        }
        const xm = token.match(/^(X_(\d+))\b/);
        if (xm) {
          const ptEl = svg.querySelector('#' + CSS.escape(xm[1])) ||
                       svg.querySelector('[data-id="' + CSS.escape(xm[1]) + '"]');
          if (ptEl) markElement(ptEl, resolveCls(ptEl), tickCls);
          return;
        }
        const cleanBr = token.replace(/^\[|\s*\]$/g, '').trim();
        const pairMatch = cleanBr.match(/^([A-Z])(?:\s+|[-–]\s*)?([A-Za-z0-9'_]+)$/) ||
                          cleanBr.match(/^([A-Za-z0-9'_]+)\s*(?:[-–]|\s+)\s*([A-Za-z0-9'_]+)$/);
        if (pairMatch) {
          const p1 = pairMatch[1];
          const p2 = pairMatch[2];
          const cand1 = `[ ${p1} ${p2} ]`;
          const cand2 = `[ ${p2} ${p1} ]`;
          const lnEl = svg.querySelector('[data-id="' + CSS.escape(cand1) + '"]') ||
                       svg.querySelector('[data-id="' + CSS.escape(cand2) + '"]') ||
                       svg.querySelector(`[data-pt1="${CSS.escape(p1)}"][data-pt2="${CSS.escape(p2)}"]`) ||
                       svg.querySelector(`[data-pt1="${CSS.escape(p2)}"][data-pt2="${CSS.escape(p1)}"]`);
          if (lnEl) {
            const chosenCls = resolveCls(lnEl);
            markElement(lnEl, chosenCls, tickCls);
            const pt2El = svg.querySelector('#' + CSS.escape(p2));
            if (pt2El) markElement(pt2El, chosenCls, null);
            return;
          }
        }
        if (token.length >= 3) {
          const lowerTok = token.toLowerCase();
          Array.from(svg.querySelectorAll('[data-name]')).forEach((namedEl) => {
            const dName = (namedEl.getAttribute('data-name') || '').toLowerCase();
            if (dName && (dName === lowerTok || dName.includes(lowerTok) || lowerTok.includes(dName))) {
              markElement(namedEl, resolveCls(namedEl), tickCls);
            }
          });
        }
      };

      const activeCls = isEtcDrawing ? 'geom-color-gold' : 'geom-highlight-active';
      const equalCls = isEtcDrawing ? 'geom-color-green' : 'geom-assert-active';
      (slide.active_ids || []).forEach((id) => applyTokenHighlight(id, activeCls, null));
      (slide.equal_ids || []).forEach((id) => applyTokenHighlight(id, equalCls, null));
      (slide.tick_ids || []).forEach((id) => applyTokenHighlight(id, equalCls, 'geom-tick-1'));

      if (anyHighlighted && isEtcDrawing) {
        svg.classList.add('has-step-highlights');
      }

      if (isEtcDrawing) {
        const initVb = svg.getAttribute('data-initial-viewbox');
        if (initVb) svg.setAttribute('viewBox', initVb);
        const rawZoom = isThumb ? (slide.thumb_zoom || slide.zoom || []) : (slide.zoom || []);
        const zoomPtSet = new Set();
        let hasCircleInZoom = false;
        if (Array.isArray(rawZoom) && rawZoom.length > 0 && rawZoom[0] !== 'all' && rawZoom[0] !== '*') {
          const bboxPts = [];
          const addPt = (x, y) => {
            if (Number.isFinite(x) && Number.isFinite(y)) bboxPts.push({ x, y });
          };
          const addElementBounds = (el) => {
            if (!el) return;
            const tag = (el.tagName || '').toLowerCase();
            if (tag === 'circle') {
              const cx = parseFloat(el.getAttribute('cx') || '0');
              const cy = parseFloat(el.getAttribute('cy') || '0');
              if (el.closest('#points') !== null) {
                addPt(cx, cy);
                const pid = el.getAttribute('id') || el.getAttribute('data-id') || '';
                if (pid) zoomPtSet.add(pid);
              } else {
                const r = parseFloat(el.getAttribute('r') || '0');
                addPt(cx - r, cy - r);
                addPt(cx + r, cy + r);
                hasCircleInZoom = true;
              }
            } else if (tag === 'polygon' || tag === 'polyline') {
              const ptsStr = (el.getAttribute('points') || '').trim();
              ptsStr.split(/\s+/).forEach((pair) => {
                const parts = pair.split(',').map(parseFloat);
                if (parts.length >= 2) addPt(parts[0], parts[1]);
              });
            }
          };
          rawZoom.forEach((rawTok) => {
            const tok = String(rawTok || '').trim().replace(/:(gold|orange|teal|blue|purple|orchid|pink|green|lime|muted)$/i, '').trim();
            if (!tok) return;
            const direct = svg.querySelector('#' + CSS.escape(tok)) ||
                           svg.querySelector('[data-id="' + CSS.escape(tok) + '"]');
            if (direct) {
              addElementBounds(direct);
              return;
            }
            const lowerTok = tok.toLowerCase();
            Array.from(svg.querySelectorAll('[data-name]')).forEach((namedEl) => {
              const dName = (namedEl.getAttribute('data-name') || '').toLowerCase();
              if (dName && (dName === lowerTok || dName.includes(lowerTok))) {
                addElementBounds(namedEl);
              }
            });
          });
          if (bboxPts.length > 0) {
            const xs = bboxPts.map((p) => p.x);
            const ys = bboxPts.map((p) => p.y);
            const minX = Math.min(...xs), maxX = Math.max(...xs);
            const minY = Math.min(...ys), maxY = Math.max(...ys);
            const spanX = maxX - minX, spanY = maxY - minY;
            const aspect = 4.0 / 3.0;
            const padFactor = hasCircleInZoom ? 1.24 : 1.48;
            let vbW = Math.max(spanX * padFactor, 0.21);
            let vbH = Math.max(spanY * padFactor, 0.16);
            if (vbW / vbH < aspect) vbW = vbH * aspect;
            else vbH = vbW / aspect;
            const cx = 0.5 * (minX + maxX);
            const cy = 0.5 * (minY + maxY);
            svg.setAttribute('viewBox', [cx - 0.5 * vbW, cy - 0.5 * vbH, vbW, vbH].map((v) => v.toFixed(6)).join(' '));
          }
        }
        const curVb = (svg.getAttribute('viewBox') || '0 0 1 1').trim().split(/[\s,]+/).map(parseFloat);
        const unitsPerPx = Math.max(curVb[2] / 920.0, curVb[3] / 690.0);
        const targetRPx = parseFloat(svg.getAttribute('data-target-r-px') || '10.5');
        Array.from(svg.querySelectorAll('#points circle')).forEach((c) => {
          const pid = c.getAttribute('id') || c.getAttribute('data-id') || '';
          const isTarget = c.classList.contains('target') || c.classList.contains('selected');
          const isHl = c.classList.contains('geom-active-any');
          const isZoomPt = zoomPtSet.has(pid);
          const isGuide = c.classList.contains('guide') && !isHl && !isZoomPt;
          const rPx = isTarget ? targetRPx : (isHl ? 8.5 : (isGuide ? 5.0 : 7.0));
          c.setAttribute('r', (rPx * unitsPerPx).toFixed(6));
        });
      }

      // 8. Wire interactive hover/click inspector on SVG elements with data-ref
      if (!isHeadless) {
        svg.querySelectorAll('[data-ref], [id^="el-"], [id^="lbl-"]').forEach((el) => {
          el.style.cursor = 'pointer';
          el.onmouseenter = (ev) => {
            const ref = el.getAttribute('data-ref') || el.getAttribute('data-aliases') || '';
            const id = el.getAttribute('id') || '';
            if (!ref && !id) return;
            tooltipEl.innerHTML = `<span class="tt-ref">${escapeHtml(ref || id)}</span><span class="tt-id">#${escapeHtml(id)} (click to copy)</span>`;
            tooltipEl.style.display = 'flex';
            tooltipEl.style.left = (ev.clientX + 14) + 'px';
            tooltipEl.style.top = (ev.clientY + 14) + 'px';
          };
          el.onmousemove = (ev) => {
            tooltipEl.style.left = (ev.clientX + 14) + 'px';
            tooltipEl.style.top = (ev.clientY + 14) + 'px';
          };
          el.onmouseleave = () => {
            tooltipEl.style.display = 'none';
          };
          el.onclick = (ev) => {
            ev.stopPropagation();
            const ref = el.getAttribute('data-ref') || el.getAttribute('id') || '';
            if (ref && navigator.clipboard) {
              navigator.clipboard.writeText(ref);
              tooltipEl.innerHTML = `<span class="tt-ref">Copied: ${escapeHtml(ref)}</span>`;
            }
          };
        });
      }
    }

    function cleanJustificationRef(slide) {
      if (!slide.just_id) return '';
      let ref = String(slide.just_id)
        .replace(/^G\.\d+\s*•\s*/i, '')
        .replace(/^∩\s*•\s*INTERSECT$/i, '')
        .replace(/^INTERSECT$/i, '')
        .trim();
      if (!ref || ref === STAGE.short_code) return '';
      return ref;
    }

    function formatSubscriptsHtml(raw, isHtml = false) {
      const src = isHtml ? String(raw || '') : escapeHtml(raw || '');
      return src.replace(/(<[^>]+>)|([^<]+)/g, (m, tag, txt) => {
        if (tag) return tag;
        return txt
          .replace(/\bX_(\d+|[a-z])\b/g, 'X<sub>$1</sub>')
          .replace(/\b([A-Za-z])_([abc]|[1-9])\b/g, '$1<sub>$2</sub>');
      });
    }

    function buildRightPanelHtml(slide) {
      const isEtc = STAGE.project_kind === 'etc';
      if (slide.is_intro) {
        return `
          <div class="intro-hero-wrap">
            <div class="intro-enunciation-card">
              <div class="intro-enun-quote">“${formatSubscriptsHtml(STAGE.enunciation)}”</div>
              <div class="intro-enun-cite">— ${formatSubscriptsHtml(STAGE.folio_display)}</div>
            </div>
          </div>
        `;
      }

      if (slide.is_review) {
        const rowsHtml = (slide.review_ledger || []).map((row) => {
          const isDone = row.step_num < slide.step_num;
          const isCur = row.step_num === slide.step_num;
          const stCls = isDone ? 'done' : (isCur ? 'current' : 'future');
          const icon = isDone ? '✓' : (isCur ? '▶' : '·');
          const iconColor = isDone ? '#5ec27e' : (isCur ? row.kind_fg : '#64748b');
          const rowBorder = isCur ? `border-color:${row.kind_fg};border-left:5px solid ${row.kind_fg};` : '';
          return `
            <div class="review-row ${stCls}" style="${rowBorder}">
              <div class="rr-left">
                <span class="rr-status" style="color:${iconColor};">${icon}</span>
                <span class="rr-num">${String(row.step_num).padStart(2, '0')}</span>
                <span class="rr-sym">${formatSubscriptsHtml(row.symbolic)}</span>
              </div>
            </div>
          `;
        }).join('');

        return `
          <div class="review-panel-wrap">
            <div class="step-panel-top">
              <span class="step-kind-badge" style="color:${slide.kind_fg};background:${slide.kind_bg};border:1.5px solid ${slide.kind_fg};">
                REVIEW
              </span>
              <span class="just-ref-pill">${String(slide.step_num).padStart(2, '0')} / ${String(slide.step_total).padStart(2, '0')}</span>
            </div>
            <div class="review-ledger-list">${rowsHtml}</div>
          </div>
        `;
      }

      if (slide.is_outro) {
        const codexUrl = isEtc ? 'geometor.github.io/etc' : 'geometor.github.io/elements';
        const nextLabel = isEtc ? 'NEXT TRIANGLE CENTER →' : 'NEXT PROPOSITION →';
        return `
          <div class="outro-panel-wrap">
            <div class="step-panel-top">
              <span class="step-kind-badge" style="color:#5ec27e;background:rgba(94,194,126,0.16);border:1.5px solid #5ec27e;">
                Q.E.F.
              </span>
              <span class="just-ref-pill">@GEOMETOR</span>
            </div>
            <div class="outro-headline">Continue the Exploration of Constructive Geometry</div>
            <div class="outro-cta-stack">
              <div class="outro-cta-card cta-primary">
                <div class="outro-cta-icon">🔔</div>
                <div class="outro-cta-body">
                  <div class="outro-cta-title">SUBSCRIBE TO @GEOMETOR</div>
                  <div class="outro-cta-sub">Subscribe & ring the bell for new constructive geometry & triangle center explorations.</div>
                </div>
              </div>
              <div class="outro-cta-card">
                <div class="outro-cta-icon">👍</div>
                <div class="outro-cta-body">
                  <div class="outro-cta-title">LIKE, COMMENT & SHARE</div>
                  <div class="outro-cta-sub">Leave a comment on the proof & share this construction with fellow geometers.</div>
                </div>
              </div>
              <div class="outro-cta-card">
                <div class="outro-cta-icon">📐</div>
                <div class="outro-cta-body">
                  <div class="outro-cta-title">EXPLORE THE INTERACTIVE CODEX</div>
                  <div class="outro-cta-sub">Step through exact symbolic models at ${escapeHtml(codexUrl)}</div>
                </div>
              </div>
            </div>
            <div class="outro-next-banner">
              <span class="outro-next-label">${nextLabel}</span>
              <span class="outro-next-val">${formatSubscriptsHtml(STAGE.next_prop || 'Next')}</span>
            </div>
          </div>
        `;
      }

      const cleanRef = cleanJustificationRef(slide);
      const refHtml = cleanRef
        ? `<span class="just-ref-pill">∵ ${formatSubscriptsHtml(cleanRef)}</span>`
        : '';

      return `
        <div class="step-panel-body">
          <div class="step-panel-top">
            <span class="step-kind-badge" style="color:${slide.kind_fg};background:${slide.kind_bg};border:1.5px solid ${slide.kind_fg};">
              ${escapeHtml(slide.step_kind)}
            </span>
            ${refHtml}
          </div>
          <div class="step-clause-wrap">
            <div class="step-clause">${slide.clause_html ? formatSubscriptsHtml(slide.clause_html, true) : formatSubscriptsHtml(slide.symbolic || '')}</div>
          </div>
        </div>
      `;
    }

    function renderStageStep(stepIdx, fmt) {
      currentFormat = fmt || currentFormat;
      STAGE.slides = activeSlidesDeck(currentFormat);
      if (!STAGE.slides || STAGE.slides.length === 0) return;
      const idx = Math.max(0, Math.min(STAGE.slides.length - 1, stepIdx));
      currentStep = idx;

      const isThumb = currentFormat.startsWith('thumb_');
      const pIdx = (currentFormat === '9x16' || currentFormat === 'thumb_9x16')
        ? (STAGE.punchline_index_9x16 || (STAGE.slides.length - 1))
        : (STAGE.punchline_index || (STAGE.slides.length - 1));
      const slide = isThumb ? STAGE.slides[pIdx] : STAGE.slides[idx];

      // Multi-SVG lookup per slide (supports ETC packages with 01_primary.svg .. 05_four_triangles.svg)
      const slideSvgMarkup = (slide.svg_name && STAGE.svgs_map && STAGE.svgs_map[slide.svg_name])
        ? STAGE.svgs_map[slide.svg_name]
        : STAGE.svg_markup;

      canvasEl.className = 'fmt-' + currentFormat;

      if (currentFormat === 'thumb_16x9' || currentFormat === 'thumb_9x16') {
        const frameCls = currentFormat === 'thumb_16x9' ? 'thumb-frame-16x9' : 'thumb-frame-9x16';
        canvasEl.innerHTML = `
          <div class="stage-grid"></div>
          <div class="${frameCls}">
            <div class="thumb-card">
              <div class="canon-title">${formatSubscriptsHtml(STAGE.short_code)}</div>
              <div class="svg-canvas-holder" id="svg-holder">${slideSvgMarkup}</div>
            </div>
          </div>
        `;
        applySvgStepState(slide, true);
      } else {
        const pBars = [];
        for (let i = 1; i <= slide.step_total; i++) {
          let cls = 'p-seg future';
          let style = '';
          if (slide.is_intro || slide.is_outro) {
            cls = 'p-seg done';
          } else if (i < slide.step_num) {
            cls = 'p-seg done';
          } else if (i === slide.step_num) {
            cls = 'p-seg current';
            style = `background:${slide.kind_fg};box-shadow:0 0 12px ${slide.kind_fg};`;
          }
          pBars.push(`<div class="${cls}" style="${style}"></div>`);
        }

        let headerCounterLabel = `${String(slide.step_num).padStart(2, '0')} / ${String(slide.step_total).padStart(2, '0')}`;
        if (slide.is_intro) {
          headerCounterLabel = 'INTRO';
        } else if (slide.is_review) {
          headerCounterLabel = `REVIEW ${String(slide.step_num).padStart(2, '0')} / ${String(slide.step_total).padStart(2, '0')}`;
        } else if (slide.is_outro) {
          headerCounterLabel = 'OUTRO';
        }

        const rightPanelContent = buildRightPanelHtml(slide);
        const topBannerTitle = STAGE.short_title || STAGE.prop_id || 'Constructive Geometry';

        if (currentFormat === '16x9') {
          canvasEl.innerHTML = `
            <div class="stage-grid"></div>
            <div class="wide-frame">
              <header class="wide-header">
                <div class="header-top">
                  <div class="header-left">
                    <span class="header-canon-badge">${formatSubscriptsHtml(STAGE.short_code)}</span>
                    <span class="header-title-text">${formatSubscriptsHtml(topBannerTitle)}</span>
                  </div>
                  <span class="step-counter-pill">${escapeHtml(headerCounterLabel)}</span>
                </div>
                <div class="progress-track">${pBars.join('')}</div>
              </header>

              <main class="wide-main">
                <div class="geom-stage-card">
                  <div class="svg-canvas-holder" id="svg-holder">${slideSvgMarkup}</div>
                </div>

                <section class="step-panel" style="border-left: 8px solid ${slide.kind_fg};">
                  ${rightPanelContent}
                </section>
              </main>
            </div>
          `;
        } else {
          // 9:16 Vertical Short
          canvasEl.innerHTML = `
            <div class="stage-grid"></div>
            <div class="short-frame">
              <header class="short-header">
                <div class="header-top">
                  <div class="header-left">
                    <span class="header-canon-badge">${formatSubscriptsHtml(STAGE.short_code)}</span>
                    <span class="header-title-text">${formatSubscriptsHtml(topBannerTitle)}</span>
                  </div>
                  <span class="step-counter-pill">${escapeHtml(headerCounterLabel)}</span>
                </div>
                <div class="progress-track">${pBars.join('')}</div>
              </header>

              <main class="geom-stage-card">
                <div class="svg-canvas-holder" id="svg-holder">${slideSvgMarkup}</div>
              </main>

              <section class="step-panel" style="border-left: 8px solid ${slide.kind_fg};">
                ${rightPanelContent}
              </section>
            </div>
          `;
        }
        applySvgStepState(slide, false);
      }

      updateSafeZoneOverlay();
      updateViewportScale();
      updateDirectorUI();
    }

    function updateSafeZoneOverlay() {
      const is16x9 = currentFormat === '16x9' || currentFormat === 'thumb_16x9';
      overlayEl.classList.toggle('visible', showGuides);
      if (is16x9) {
        overlayEl.innerHTML = `
          <div class="guide-line-v" style="left: 38.2%;"></div>
          <div class="guide-line-v" style="left: 61.8%;"></div>
          <div class="guide-line-h" style="top: 38.2%;"></div>
          <div class="guide-line-h" style="top: 61.8%;"></div>
          <div class="safe-scrub-16x9">YOUTUBE 16:9 SCRUB BAR SAFE MARGIN (64PX)</div>
        `;
      } else {
        overlayEl.innerHTML = `
          <div class="guide-line-v" style="left: 50%;"></div>
          <div class="guide-line-h" style="top: 38.2%;"></div>
          <div class="safe-shorts-right"></div>
          <div class="safe-shorts-bottom">YOUTUBE SHORTS MOBILE UI OVERLAY ZONE (440PX)</div>
        `;
      }
    }

    function updateViewportScale() {
      if (isHeadless) return;
      const is16x9 = currentFormat === '16x9' || currentFormat === 'thumb_16x9';
      const natW = is16x9 ? 1920 : 1080;
      const natH = is16x9 ? 1080 : 1920;

      const rect = viewportEl.getBoundingClientRect();
      const availW = Math.max(200, rect.width - 56);
      const availH = Math.max(200, rect.height - 56);

      let s = 1.0;
      if (scaleMode === 'fit') {
        s = Math.min(availW / natW, availH / natH);
      } else {
        s = parseFloat(scaleMode) || 1.0;
      }

      scalerEl.style.width = Math.round(natW * s) + 'px';
      scalerEl.style.height = Math.round(natH * s) + 'px';
      mountEl.style.width = natW + 'px';
      mountEl.style.height = natH + 'px';
      mountEl.style.transform = `scale(${s.toFixed(4)})`;

      const pct = Math.round(s * 100);
      const outRes = is16x9 ? '2560×1440 QHD' : '1080×1920 Short';
      const domRes = is16x9 ? '1920×1080' : '1080×1920';
      document.getElementById('res-readout').textContent = `${outRes} • ${pct}%`;
      document.getElementById('bezel-label').textContent =
        `STAGE • ${currentFormat.toUpperCase()} (DOM ${domRes} → MASTER ${outRes} • SCALE ${pct}%)`;
    }

    function renderStatusPills() {
      const wrap = document.getElementById('status-pills');
      if (!wrap || !STAGE.status) return;
      const st = STAGE.status;
      const aCls = st.audio_status === 'ready' ? 'st-ready' : (st.audio_status === 'stale' ? 'st-stale' : 'st-missing');
      const rCls = st.render_status === 'ready' ? 'st-ready' : (st.render_status === 'stale' || st.render_status === 'partial' ? 'st-stale' : 'st-missing');
      const yCls = (st.youtube_status === 'public' || st.youtube_status === 'unlisted') ? 'st-unlisted' : 'st-local';
      const aLbl = st.audio_status === 'ready' ? `AUDIO: ${st.audio_duration_sec || 0}s` : `AUDIO: ${st.audio_status.toUpperCase()}`;
      const rLbl = st.render_status === 'ready' ? `MP4: READY (${st.mp4_16x9_mb}M)` : `MP4: ${st.render_status.toUpperCase()}`;
      const yLbl = `YT: ${st.youtube_status.toUpperCase()}`;
      wrap.innerHTML = `
        <span class="st-pill ${aCls}" onclick="setSidebarTab('editor')" title="Click to open Live Editor & Regenerate Audio">${escapeHtml(aLbl)}</span>
        <span class="st-pill ${rCls}" onclick="setSidebarTab('editor')" title="Click to open Live Editor & Render MP4s">${escapeHtml(rLbl)}</span>
        <span class="st-pill ${yCls}" onclick="toggleMatrixModal()" title="Click to open Status Matrix">${escapeHtml(yLbl)}</span>
      `;
    }

    function getSectionFolderMeta(secId) {
      return (STAGE.section_folders || []).find((sf) => sf.section_id === secId) || null;
    }

    function renderEditorSegmentsBox() {
      const listEl = document.getElementById('editor-segments-list');
      const sumEl = document.getElementById('segments-summary-label');
      if (!listEl) return;
      const secs = STAGE.section_folders || [];
      const readyCount = secs.filter((sf) => sf.status === 'ready').length;
      if (sumEl) {
        sumEl.textContent = `${readyCount}/${secs.length} CLIPS READY`;
      }
      listEl.innerHTML = secs.map((sf) => {
        const stCls = sf.status === 'ready' ? 'st-ready' : (sf.status === 'dirty' ? 'st-stale' : 'st-missing');
        const stTxt = sf.status === 'ready' ? 'CLIP READY' : (sf.status === 'dirty' ? 'DIRTY' : 'NEW');
        const hasClip = (currentFormat === '9x16' || currentFormat === 'thumb_9x16') ? sf.has_clip_9x16 : sf.has_clip_16x9;
        return `
          <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;padding:5px 8px;background:#080b11;border:1px solid rgba(148,163,184,0.18);border-radius:6px;font-family:'JetBrains Mono',monospace;font-size:11px;">
            <span style="color:var(--blue);font-weight:700;">📁 ${escapeHtml(sf.folder_name)}/</span>
            <span style="color:var(--text-muted);">${sf.step_count_16x9}st • ${sf.duration_sec.toFixed(1)}s</span>
            <span class="st-pill ${stCls}" style="font-size:9.5px;padding:1px 6px;">${stTxt}</span>
            <div style="display:flex;gap:4px;">
              ${hasClip ? `<button class="tb-btn" style="padding:2px 6px;font-size:10px;color:var(--green);border:1px solid rgba(94,194,126,0.4);" onclick="previewSectionClip('${escapeHtml(sf.section_id)}', event)" title="Preview rendered MP4 clip for ${escapeHtml(sf.folder_name)}">▶ Clip</button>` : ''}
              <button class="tb-btn" style="padding:2px 6px;font-size:10px;color:var(--gold);border:1px solid var(--border-gold);" onclick="runSectionRender('${escapeHtml(sf.section_id)}', event)" title="Regenerate only ${escapeHtml(sf.folder_name)} and re-assemble MP4s">🔄 Regen</button>
            </div>
          </div>
        `;
      }).join('');
    }

    function buildSidebarSteps() {
      const container = document.getElementById('sidebar-steps');
      container.innerHTML = '';
      STAGE.slides = activeSlidesDeck(currentFormat);
      document.getElementById('tab-steps').textContent = `Steps (${STAGE.slides.length})`;
      let lastSec = null;
      STAGE.slides.forEach((s, i) => {
        if (s.section_id && s.section_id !== lastSec) {
          lastSec = s.section_id;
          const sf = getSectionFolderMeta(s.section_id);
          const folderLabel = sf ? sf.folder_name : s.section_id;
          const st = sf ? sf.status : 'missing';
          const dot = st === 'ready' ? '🟢' : (st === 'dirty' ? '🟡' : '⚪');
          const hasClip = sf ? ((currentFormat === '9x16' || currentFormat === 'thumb_9x16') ? sf.has_clip_9x16 : sf.has_clip_16x9) : false;
          const secDiv = document.createElement('div');
          secDiv.className = 'section-divider';
          secDiv.innerHTML = `
            <span title="Segment folder: segments/${escapeHtml(folderLabel)}/">${dot} 📁 ${escapeHtml(folderLabel)}/</span>
            <span style="display:flex;align-items:center;gap:5px;">
              <span style="font-size:10px;opacity:0.75;">${escapeHtml(s.bell)}</span>
              ${hasClip ? `<button class="tb-btn" style="padding:1px 6px;font-size:9.5px;color:var(--green);border:1px solid rgba(94,194,126,0.38);" onclick="previewSectionClip('${escapeHtml(s.section_id)}', event)" title="Play segment MP4 clip">▶ Clip</button>` : ''}
              <button class="tb-btn" style="padding:1px 6px;font-size:9.5px;color:var(--gold);border:1px solid var(--border-gold);" onclick="runSectionRender('${escapeHtml(s.section_id)}', event)" title="Regenerate only this section & re-assemble MP4s">🔄 Regen</button>
            </span>
          `;
          container.appendChild(secDiv);
        }
        const card = document.createElement('div');
        card.className = 'step-card' + (i === currentStep ? ' active' : '');
        card.dataset.idx = i;
        const sfxBadges = (s.sfx_cues || []).map((c) => `<span class="sc-sfx">${escapeHtml(c)}</span>`).join(' ');
        let badgeLabel = `${String(s.step_num).padStart(2, '0')} • ${s.step_kind}`;
        if (s.is_intro) badgeLabel = 'INTRO';
        else if (s.is_review) badgeLabel = `REV ${String(s.step_num).padStart(2, '0')} • ${s.orig_step_kind}`;
        else if (s.is_outro) badgeLabel = 'OUTRO';

        card.innerHTML = `
          <div class="sc-top">
            <span class="sc-badge" style="color:${s.kind_fg};background:${s.kind_bg};border:1px solid ${s.kind_fg};">
              ${escapeHtml(badgeLabel)}
            </span>
            <div>${sfxBadges}</div>
            <span class="sc-time">${s.start_sec.toFixed(1)}s (${s.duration.toFixed(1)}s)</span>
          </div>
          ${s.symbolic ? `<div class="sc-symbolic">${escapeHtml(s.symbolic)}</div>` : ''}
          <div class="sc-say">${escapeHtml(s.say_clean)}</div>
        `;
        card.onclick = () => {
          stepTo(i, true);
        };
        container.appendChild(card);
      });
      renderEditorSegmentsBox();
    }

    function buildTimelineSegments() {
      const tl = document.getElementById('timeline-segments');
      tl.innerHTML = '';
      STAGE.slides = activeSlidesDeck(currentFormat);
      const total = Math.max(0.1, STAGE.total_duration || 1.0);
      STAGE.slides.forEach((s, i) => {
        const seg = document.createElement('div');
        seg.className = 'tl-seg';
        seg.style.flex = String(Math.max(0.35, s.duration) / total);
        let segLbl = String(s.step_num);
        if (s.is_intro) segLbl = 'IN';
        else if (s.is_review) segLbl = 'R' + s.step_num;
        else if (s.is_outro) segLbl = 'OUT';
        seg.title = `${segLbl} (${s.step_kind}): ${s.say_clean}`;
        seg.textContent = segLbl;
        seg.onclick = () => stepTo(i, true);
        tl.appendChild(seg);
      });
    }

    function populateQuickEditorFromStep() {
      const s = STAGE.slides[currentStep];
      if (!s) return;
      const titleEl = document.getElementById('quick-step-title');
      if (titleEl) {
        titleEl.textContent = `STEP ${String(s.step_num).padStart(2, '0')} (${s.step_kind}) QUICK-EDIT`;
      }
      const qSay = document.getElementById('qe-say');
      const qSayWide = document.getElementById('qe-say-wide');
      const qSayShort = document.getElementById('qe-say-short');
      const qClause = document.getElementById('qe-clause');
      const qSym = document.getElementById('qe-symbolic');
      const qAct = document.getElementById('qe-active');
      const qEq = document.getElementById('qe-equal');
      if (qSay) qSay.value = s.say_shared_raw || s.say_raw || '';
      if (qSayWide) qSayWide.value = s.say_wide_raw || '';
      if (qSayShort) qSayShort.value = s.say_short_raw || '';
      if (qClause) qClause.value = s.raw_clause || '';
      if (qSym) qSym.value = s.symbolic || '';
      if (qAct) qAct.value = (s.raw_active || []).join(', ');
      if (qEq) qEq.value = (s.raw_equal || []).join(', ');
    }

    function syncEditorTextarea() {
      const ta = document.getElementById('scr-editor-textarea');
      const lbl = document.getElementById('editor-file-label');
      const seedBtn = document.getElementById('btn-seed-variant');
      if (!ta) return;
      document.querySelectorAll('.ed-var-btn').forEach((b) => {
        b.classList.toggle('active', b.dataset.var === editorVariant);
      });
      if (editorVariant === 'wide') {
        ta.value = STAGE.script_wide_text || '';
        if (lbl) lbl.textContent = 'script.wide.scr';
        if (seedBtn) seedBtn.style.display = STAGE.script_wide_text ? 'none' : 'inline-block';
      } else if (editorVariant === 'short') {
        ta.value = STAGE.script_short_text || '';
        if (lbl) lbl.textContent = 'script.short.scr';
        if (seedBtn) seedBtn.style.display = STAGE.script_short_text ? 'none' : 'inline-block';
      } else {
        ta.value = STAGE.script_text || '';
        if (lbl) lbl.textContent = STAGE.source_file || 'script.scr';
        if (seedBtn) seedBtn.style.display = 'none';
      }
      editorDirty = false;
    }

    function setEditorVariant(v) {
      editorVariant = v;
      syncEditorTextarea();
    }

    function seedVariantFromShared() {
      const ta = document.getElementById('scr-editor-textarea');
      if (!ta) return;
      ta.value = STAGE.script_text || '';
      editorDirty = true;
    }

    function applyQuickStepToTextarea() {
      const s = STAGE.slides[currentStep];
      if (!s || s.is_intro || s.is_review || s.is_outro) {
        showEditorToast('Select a core proposition step to quick-edit.');
        return;
      }
      const ta = document.getElementById('scr-editor-textarea');
      if (!ta) return;
      const lines = ta.value.split('\n');
      let screenIdx = -1;
      let targetScreenCount = s.step_num;
      let seenScreens = 0;
      for (let i = 0; i < lines.length; i++) {
        if (/^\s*\[screen\s*:\s*(?!INTRO|OUTRO|REVIEW)/i.test(lines[i])) {
          seenScreens++;
          if (seenScreens === targetScreenCount) {
            screenIdx = i;
            break;
          }
        }
      }
      if (screenIdx === -1) {
        showEditorToast('Could not locate matching [screen: ...] block in script.');
        return;
      }
      let blockEnd = lines.length;
      for (let i = screenIdx + 1; i < lines.length; i++) {
        if (/^\s*\[(?:screen|review|intro|outro):/i.test(lines[i]) || /^\s*(?:#\s*)?===/i.test(lines[i])) {
          blockEnd = i;
          break;
        }
      }

      const newSay = document.getElementById('qe-say').value.trim();
      const newSayW = document.getElementById('qe-say-wide').value.trim();
      const newSayS = document.getElementById('qe-say-short').value.trim();
      const newClause = document.getElementById('qe-clause').value.trim();
      const newSym = document.getElementById('qe-symbolic').value.trim();
      const newAct = document.getElementById('qe-active').value.trim();
      const newEq = document.getElementById('qe-equal').value.trim();

      const blockLines = lines.slice(screenIdx, blockEnd);
      const updatedBlock = [];
      let hadSay = false, hadSayW = false, hadSayS = false, hadClause = false, hadSym = false, hadAct = false, hadEq = false;

      blockLines.forEach((ln) => {
        if (/^\s*say\s*:/i.test(ln)) {
          if (newSay) updatedBlock.push(`say: ${newSay}`);
          hadSay = true;
        } else if (/^\s*say_wide\s*:/i.test(ln)) {
          if (newSayW) updatedBlock.push(`say_wide: ${newSayW}`);
          hadSayW = true;
        } else if (/^\s*say_short\s*:/i.test(ln)) {
          if (newSayS) updatedBlock.push(`say_short: ${newSayS}`);
          hadSayS = true;
        } else if (/^\s*clause\s*:/i.test(ln)) {
          if (newClause) updatedBlock.push(`clause: ${newClause}`);
          hadClause = true;
        } else if (/^\s*symbolic\s*:/i.test(ln)) {
          if (newSym) updatedBlock.push(`symbolic: ${newSym}`);
          hadSym = true;
        } else if (/^\s*active\s*:/i.test(ln)) {
          if (newAct) updatedBlock.push(`active: ${newAct}`);
          hadAct = true;
        } else if (/^\s*equal\s*:/i.test(ln)) {
          if (newEq) updatedBlock.push(`equal: ${newEq}`);
          hadEq = true;
        } else {
          updatedBlock.push(ln);
        }
      });

      if (!hadAct && newAct) updatedBlock.splice(1, 0, `active: ${newAct}`);
      if (!hadEq && newEq) updatedBlock.splice(1, 0, `equal: ${newEq}`);
      if (!hadSym && newSym) updatedBlock.push(`symbolic: ${newSym}`);
      if (!hadClause && newClause) updatedBlock.push(`clause: ${newClause}`);
      if (!hadSay && newSay) updatedBlock.push(`say: ${newSay}`);
      if (!hadSayW && newSayW) updatedBlock.push(`say_wide: ${newSayW}`);
      if (!hadSayS && newSayS) updatedBlock.push(`say_short: ${newSayS}`);

      const nextLines = [
        ...lines.slice(0, screenIdx),
        ...updatedBlock,
        ...lines.slice(blockEnd),
      ];
      ta.value = nextLines.join('\n');
      editorDirty = true;
      saveLiveScript();
    }

    function showEditorToast(msg) {
      const el = document.getElementById('editor-toast');
      if (!el) return;
      el.textContent = msg;
      el.classList.add('visible');
    }

    async function saveLiveScript() {
      const ta = document.getElementById('scr-editor-textarea');
      const btn = document.getElementById('btn-save-scr');
      if (!ta) return;
      if (btn) btn.disabled = true;
      showEditorToast('Compiling & updating live stage preview...');
      try {
        const propParam = urlParams.get('prop') || '';
        const projParam = urlParams.get('project') || '';
        const res = await fetch('/api/save-script', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prop: propParam,
            project: projParam,
            variant: editorVariant,
            content: ta.value,
          }),
        });
        const data = await res.json();
        if (data.status === 'ok' && data.stage) {
          STAGE = data.stage;
          editorDirty = false;
          renderStatusPills();
          buildSidebarSteps();
          buildTimelineSegments();
          renderStageStep(currentStep, currentFormat);
          showEditorToast(`✓ Saved ${data.saved_file} • Hot-reloaded ${STAGE.slides_16x9.length} (16:9) / ${STAGE.slides_9x16.length} (9:16) slides.`);
        } else {
          showEditorToast(`⚠ Error: ${data.error || 'Failed to save script'}`);
        }
      } catch (e) {
        showEditorToast(`⚠ Save failed: ${e.message}`);
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function runLiveSpeak() {
      const btn = document.getElementById('btn-regen-tts');
      if (editorDirty) {
        await saveLiveScript();
      }
      if (btn) btn.disabled = true;
      showEditorToast('🎙️ Synthesizing spoken audio & harmonic bells via TTS cache...');
      try {
        const propParam = urlParams.get('prop') || '';
        const projParam = urlParams.get('project') || '';
        const res = await fetch('/api/speak', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prop: propParam,
            project: projParam,
            variant: currentFormat === '9x16' ? '9x16' : '16x9',
          }),
        });
        const data = await res.json();
        if (data.status === 'ok' && data.stage) {
          STAGE = data.stage;
          if (STAGE.has_audio && STAGE.audio_filename) {
            audioEl.src = STAGE.audio_filename + '?t=' + Date.now();
          }
          renderStatusPills();
          buildSidebarSteps();
          buildTimelineSegments();
          renderStageStep(currentStep, currentFormat);
          showEditorToast(`✓ Audio ready (${STAGE.total_duration.toFixed(1)}s) in ${data.elapsed_sec}s.`);
        } else {
          showEditorToast(`⚠ TTS Error: ${data.error || 'Failed'}`);
        }
      } catch (e) {
        showEditorToast(`⚠ TTS request failed: ${e.message}`);
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function runLiveRender(targetSec = null) {
      const btn = document.getElementById('btn-render-mp4');
      if (editorDirty) {
        await saveLiveScript();
      }
      if (btn) btn.disabled = true;
      const lbl = targetSec ? `section '${targetSec}'` : 'dirty segments';
      showEditorToast(`🎬 Regenerating ${lbl} & assembling 1440p + Short MP4s...`);
      try {
        const propParam = urlParams.get('prop') || '';
        const projParam = urlParams.get('project') || '';
        const res = await fetch('/api/render', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prop: propParam,
            project: projParam,
            section: targetSec || '',
          }),
        });
        const data = await res.json();
        if (data.status === 'ok' && data.stage) {
          STAGE = data.stage;
          if (STAGE.has_audio && STAGE.audio_filename) {
            audioEl.src = STAGE.audio_filename + '?t=' + Date.now();
          }
          renderStatusPills();
          buildSidebarSteps();
          buildTimelineSegments();
          const rebuilt = (data.render.rebuilt_sections || []).join(', ') || 'none (all cached)';
          showEditorToast(`✓ Built [${rebuilt}] & assembled MP4s in ${data.render.total_render_sec}s (cap ${data.render.capture_sec}s, enc ${data.render.encode_sec}s).`);
        } else {
          showEditorToast(`⚠ Render Error: ${data.error || 'Failed'}`);
        }
      } catch (e) {
        showEditorToast(`⚠ Render request failed: ${e.message}`);
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    function runSectionRender(secId, ev) {
      if (ev) ev.stopPropagation();
      setSidebarTab('editor');
      runLiveRender(secId);
    }

    function previewSectionClip(secId, ev) {
      if (ev) ev.stopPropagation();
      const sf = getSectionFolderMeta(secId);
      if (!sf) return;
      const isShort = currentFormat === '9x16' || currentFormat === 'thumb_9x16';
      const url = isShort
        ? (sf.clip_9x16_http_url || sf.clip_9x16_url)
        : (sf.clip_16x9_http_url || sf.clip_16x9_url);
      const modal = document.getElementById('clip-preview-modal');
      const vid = document.getElementById('clip-modal-video');
      const title = document.getElementById('clip-modal-title');
      const meta = document.getElementById('clip-modal-meta');
      if (!modal || !vid) return;
      stopPlayback();
      title.textContent = `SEGMENT CLIP • ${sf.folder_name} (${isShort ? '9:16 Short 1080×1920' : '16:9 QHD 2560×1440'})`;
      meta.textContent = `Folder: ${sf.rel_dir}/ • Steps: ${sf.step_count_16x9} • Duration: ${sf.duration_sec.toFixed(2)}s • Hash: ${sf.hashes ? sf.hashes.clip_hash : ''}`;
      vid.src = url + '?t=' + Date.now();
      modal.classList.add('open');
      vid.play().catch(() => {});
    }

    function closeClipModal() {
      const modal = document.getElementById('clip-preview-modal');
      const vid = document.getElementById('clip-modal-video');
      if (vid) {
        vid.pause();
        vid.removeAttribute('src');
      }
      if (modal) modal.classList.remove('open');
    }

    function updateInspectorPanel() {
      const insp = document.getElementById('sidebar-inspector');
      const s = STAGE.slides[currentStep];
      if (!s) return;

      const scrLines = [
        `# === ${s.section_id} ===`,
      ];
      if (s.is_review) {
        scrLines.push(`[review: auto | pace: ${s.duration}]`);
        scrLines.push(`# Replay of Step ${s.step_num} (${s.orig_step_kind}) — No words, just sounds`);
      } else {
        scrLines.push(`[screen: ${s.step_kind}${s.is_punchline ? ' | punchline: true' : ''}${s.only ? ' | only: ' + s.only : ''}]`);
      }
      scrLines.push(`show: ${(s.raw_show || ['*']).join(', ')}`);
      if (s.raw_active && s.raw_active.length) scrLines.push(`active: ${s.raw_active.join(', ')}`);
      if (s.raw_equal && s.raw_equal.length) scrLines.push(`equal: ${s.raw_equal.join(', ')}`);
      if (s.raw_ticks && s.raw_ticks.length) scrLines.push(`ticks: ${s.raw_ticks.join(', ')}`);
      if (s.symbolic) scrLines.push(`symbolic: ${s.symbolic}`);
      if (s.raw_clause) scrLines.push(`clause: ${s.raw_clause}`);
      if (s.just_id) scrLines.push(`just: ${s.just_id}${s.just_title ? ' | ' + s.just_title : ''}`);
      if (s.say_raw) scrLines.push(`say: ${s.say_raw}`);
      if (s.say_wide_raw) scrLines.push(`say_wide: ${s.say_wide_raw}`);
      if (s.say_short_raw) scrLines.push(`say_short: ${s.say_short_raw}`);

      const actSet = new Set(s.active_ids || []);
      const eqSet = new Set(s.equal_ids || []);
      const visSet = new Set(s.visible_ids || []);
      const hidSet = new Set(s.hidden_ids || []);
      const showAll = visSet.has('*');

      const vocabHtml = (STAGE.svg_vocab || []).map((v) => {
        let stCls = 'st-hidden';
        if (hidSet.has(v.id)) {
          stCls = 'st-hidden';
        } else if (actSet.has(v.id)) {
          stCls = 'st-active';
        } else if (eqSet.has(v.id)) {
          stCls = 'st-equal';
        } else if (showAll || visSet.has(v.id)) {
          stCls = 'st-visible';
        }
        return `<span class="vocab-pill ${stCls}" title="#${escapeHtml(v.id)} (${escapeHtml(v.kind)}) — click to copy" onclick="navigator.clipboard && navigator.clipboard.writeText('${escapeHtml(v.ref)}')">${escapeHtml(v.ref)}</span>`;
      }).join('');

      insp.innerHTML = `
        <div class="insp-box">
          <div class="insp-title">Active Slide (${currentStep + 1}/${STAGE.slides.length}) • ${escapeHtml(STAGE.source_file)}</div>
          <div class="scr-code">${escapeHtml(scrLines.join('\n'))}</div>
        </div>
        <div class="insp-box">
          <div class="insp-title">SVG Vocabulary (${escapeHtml(s.svg_name || STAGE.svg_name)}) • Click to Copy</div>
          <div class="vocab-grid">${vocabHtml || '<span style="font-size:11px;color:#94a3b8;">Multi-SVG / Custom diagram</span>'}</div>
        </div>
      `;
    }

    function updateDirectorUI() {
      document.querySelectorAll('#format-tabs .tb-btn').forEach((b) => {
        b.classList.toggle('active', b.dataset.fmt === currentFormat);
      });
      document.querySelectorAll('#scale-tabs .tb-btn').forEach((b) => {
        b.classList.toggle('active', String(b.dataset.scale) === String(scaleMode));
      });
      document.querySelectorAll('.step-card').forEach((c, i) => {
        const isAct = i === currentStep;
        c.classList.toggle('active', isAct);
        if (isAct) c.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      });
      document.querySelectorAll('.tl-seg').forEach((seg, i) => {
        seg.classList.toggle('done', i < currentStep);
        seg.classList.toggle('current', i === currentStep);
      });
      const s = STAGE.slides[currentStep];
      if (s) {
        const curT = (!audioEl.paused && STAGE.has_audio && !s.use_synth_audio) ? audioEl.currentTime : s.start_sec;
        document.getElementById('time-code').textContent =
          `${curT.toFixed(1)}s / ${(STAGE.total_duration || 0).toFixed(1)}s`;
      }
      renderStatusPills();
      populateQuickEditorFromStep();
      updateInspectorPanel();
    }

    function setFormat(fmt) {
      const prevDeck = STAGE.slides;
      const nextDeck = activeSlidesDeck(fmt);
      if (prevDeck !== nextDeck) {
        STAGE.slides = nextDeck;
        buildSidebarSteps();
        buildTimelineSegments();
      }
      renderStageStep(currentStep, fmt);
    }

    function setScaleMode(mode) {
      scaleMode = mode;
      updateViewportScale();
      updateDirectorUI();
    }

    function toggleGuides() {
      showGuides = !showGuides;
      document.getElementById('btn-guides').classList.toggle('toggle-on', showGuides);
      updateSafeZoneOverlay();
    }

    function toggleSidebar() {
      const sb = document.getElementById('director-sidebar');
      const collapsed = sb.classList.toggle('collapsed');
      document.getElementById('btn-inspector').classList.toggle('toggle-on', !collapsed);
      updateViewportScale();
    }

    function setSidebarTab(tab) {
      document.getElementById('tab-steps').classList.toggle('active', tab === 'steps');
      document.getElementById('tab-editor').classList.toggle('active', tab === 'editor');
      document.getElementById('tab-inspector').classList.toggle('active', tab === 'inspector');
      document.getElementById('sidebar-steps').style.display = tab === 'steps' ? 'flex' : 'none';
      document.getElementById('sidebar-editor').style.display = tab === 'editor' ? 'flex' : 'none';
      document.getElementById('sidebar-inspector').style.display = tab === 'inspector' ? 'flex' : 'none';
      if (tab === 'editor' && !editorDirty) {
        syncEditorTextarea();
      }
    }

    function stepTo(idx, seekAudio = true) {
      STAGE.slides = activeSlidesDeck(currentFormat);
      const clamped = Math.max(0, Math.min(STAGE.slides.length - 1, idx));
      if (currentFormat.startsWith('thumb_')) {
        currentFormat = currentFormat === 'thumb_9x16' ? '9x16' : '16x9';
      }
      renderStageStep(clamped, currentFormat);
      const s = STAGE.slides[clamped];
      if (!s) return;

      if (seekAudio) {
        if (s.use_synth_audio || !STAGE.has_audio || audioEl.paused) {
          if (!audioEl.paused && s.use_synth_audio) {
            audioEl.pause();
          }
          playHarmonicCueList(s.sfx_cues);
        } else if (STAGE.has_audio) {
          audioEl.currentTime = s.start_sec + 0.02;
        }
      }
    }

    function stepDelta(delta) {
      stepTo(currentStep + delta, true);
    }

    function stepPackage(delta) {
      const sel = document.getElementById('prop-select');
      if (!sel || sel.options.length < 2) return;
      const nextIdx = Math.max(0, Math.min(sel.options.length - 1, sel.selectedIndex + delta));
      if (nextIdx !== sel.selectedIndex) {
        sel.selectedIndex = nextIdx;
        sel.onchange();
      }
    }

    function toggleMatrixModal() {
      const m = document.getElementById('matrix-modal');
      if (!m) return;
      const open = m.classList.toggle('open');
      if (open && catalogData) {
        renderMatrixTable();
      }
    }

    function setMatrixFilter(flt) {
      matrixFilter = flt;
      document.querySelectorAll('#matrix-filter-tabs .tb-btn').forEach((b) => {
        b.classList.toggle('active', b.dataset.flt === flt);
      });
      renderMatrixTable();
    }

    function renderMatrixTable() {
      if (!catalogData || !catalogData.packages) return;
      const pkgs = catalogData.packages;
      const sum = catalogData.summary || {};
      const kpisEl = document.getElementById('matrix-kpis');
      if (kpisEl) {
        kpisEl.innerHTML = `
          <span class="kpi-chip">Total: <strong>${sum.total || pkgs.length}</strong></span>
          <span class="kpi-chip">script.scr: <strong>${sum.scr_count || 0}</strong></span>
          <span class="kpi-chip">16:9/9:16 Split: <strong>${sum.split_form_factor || 0}</strong></span>
          <span class="kpi-chip">Audio Ready: <strong>${sum.audio_ready || 0}</strong></span>
          <span class="kpi-chip">MP4 Rendered: <strong>${sum.render_ready || 0}</strong></span>
          <span class="kpi-chip">YouTube: <strong>${sum.youtube_uploaded || 0}</strong></span>
        `;
      }
      const q = (document.getElementById('matrix-search')?.value || '').trim().toLowerCase();
      const tbody = document.getElementById('matrix-tbody');
      if (!tbody) return;
      const projParam = catalogData.active_project || '';
      const rows = pkgs.filter((p) => {
        const st = p.status || {};
        if (matrixFilter === 'ready' && st.render_status !== 'ready') return false;
        if (matrixFilter === 'stale' && st.render_status !== 'stale' && st.audio_status !== 'stale') return false;
        if (matrixFilter === 'missing' && st.render_status !== 'missing') return false;
        if (matrixFilter === 'youtube' && st.youtube_status === 'local') return false;
        if (q) {
          const hay = `${p.short_code} ${p.prefix} ${p.title} ${p.rel_path}`.toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      }).map((p) => {
        const st = p.status || {};
        const isCur = p.prefix === STAGE.prefix;
        const aCls = st.audio_status === 'ready' ? 'st-ready' : (st.audio_status === 'stale' ? 'st-stale' : 'st-missing');
        const rCls = st.render_status === 'ready' ? 'st-ready' : (st.render_status === 'stale' || st.render_status === 'partial' ? 'st-stale' : 'st-missing');
        const yCls = st.youtube_status !== 'local' ? 'st-unlisted' : 'st-local';
        const ffBadge = (st.has_wide_override || st.has_short_override)
          ? '<span class="st-pill st-unlisted">16:9 + 9:16 CUSTOM</span>'
          : '<span class="st-pill st-local">SHARED</span>';
        const url = `/?project=${encodeURIComponent(projParam)}&prop=${encodeURIComponent(p.rel_path)}`;
        return `
          <tr class="${isCur ? 'active-pkg' : ''}" onclick="window.location.href='${url}'">
            <td><strong style="color:var(--gold);">${escapeHtml(p.short_code)}</strong></td>
            <td>${escapeHtml(p.title || p.prefix)}</td>
            <td>${escapeHtml(st.script_file || 'script.scr')}</td>
            <td>${ffBadge}</td>
            <td><span class="st-pill ${aCls}">${escapeHtml((st.audio_status || 'missing').toUpperCase())}${st.audio_duration_sec ? ' (' + st.audio_duration_sec + 's)' : ''}</span></td>
            <td><span class="st-pill ${rCls}">${escapeHtml((st.render_status || 'missing').toUpperCase())}${st.mp4_16x9_mb ? ' (' + st.mp4_16x9_mb + ' MB)' : ''}</span></td>
            <td><span class="st-pill ${yCls}">${escapeHtml((st.youtube_status || 'local').toUpperCase())}</span></td>
            <td><a href="${url}" style="color:var(--gold);text-decoration:none;font-weight:700;">Open Stage →</a></td>
          </tr>
        `;
      }).join('');
      tbody.innerHTML = rows || '<tr><td colspan="8" style="text-align:center;color:#94a3b8;padding:24px;">No matching packages</td></tr>';
    }

    function stopPlayback() {
      isPlaying = false;
      if (syntheticTimer) {
        clearTimeout(syntheticTimer);
        syntheticTimer = null;
      }
      audioEl.pause();
      const btn = document.getElementById('btn-play');
      btn.textContent = '▶ Play';
      btn.classList.remove('play-active');
    }

    function togglePlay() {
      if (isPlaying) {
        stopPlayback();
        return;
      }
      isPlaying = true;
      const btn = document.getElementById('btn-play');
      btn.textContent = '⏸ Pause';
      btn.classList.add('play-active');

      const s = STAGE.slides[currentStep];
      if (STAGE.has_audio && s && !s.use_synth_audio) {
        if (audioEl.currentTime < s.start_sec || audioEl.currentTime >= (STAGE.audio_file_duration || STAGE.total_duration) - 0.1) {
          audioEl.currentTime = s.start_sec;
        }
        audioEl.play().catch(() => {
          playHarmonicCueList(s.sfx_cues);
          runSyntheticStepLoop();
        });
      } else {
        if (s) playHarmonicCueList(s.sfx_cues);
        runSyntheticStepLoop();
      }
    }

    function runSyntheticStepLoop() {
      if (!isPlaying) return;
      if (syntheticTimer) clearTimeout(syntheticTimer);
      const s = STAGE.slides[currentStep];
      const waitMs = Math.max(400, (s ? s.duration : 1.0) * 1000);
      syntheticTimer = setTimeout(() => {
        if (!isPlaying) return;
        if (currentStep + 1 < STAGE.slides.length) {
          const nextIdx = currentStep + 1;
          renderStageStep(nextIdx, currentFormat);
          const nextSlide = STAGE.slides[nextIdx];
          if (nextSlide) {
            playHarmonicCueList(nextSlide.sfx_cues);
          }
          runSyntheticStepLoop();
        } else {
          stopPlayback();
        }
      }, waitMs);
    }

    audioEl.addEventListener('timeupdate', () => {
      if (audioEl.paused) return;
      const t = audioEl.currentTime;
      for (let i = STAGE.slides.length - 1; i >= 0; i--) {
        const sl = STAGE.slides[i];
        if (!sl.use_synth_audio && t >= sl.start_sec) {
          if (i !== currentStep) {
            renderStageStep(i, currentFormat);
          } else {
            document.getElementById('time-code').textContent =
              `${t.toFixed(1)}s / ${(STAGE.total_duration || 0).toFixed(1)}s`;
          }
          break;
        }
      }
    });

    audioEl.addEventListener('ended', () => {
      if (!isPlaying) return;
      const nextSynthIdx = STAGE.slides.findIndex((sl, idx) => idx > currentStep && sl.use_synth_audio);
      if (nextSynthIdx !== -1) {
        renderStageStep(nextSynthIdx, currentFormat);
        playHarmonicCueList(STAGE.slides[nextSynthIdx].sfx_cues);
        runSyntheticStepLoop();
      } else {
        stopPlayback();
      }
    });

    const scrTaEl = document.getElementById('scr-editor-textarea');
    if (scrTaEl) {
      scrTaEl.addEventListener('input', () => {
        editorDirty = true;
      });
      scrTaEl.addEventListener('keydown', (ev) => {
        if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 's') {
          ev.preventDefault();
          saveLiveScript();
        }
      });
    }

    // Keyboard shortcuts
    window.addEventListener('keydown', (ev) => {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 's') {
        ev.preventDefault();
        saveLiveScript();
        return;
      }
      if (ev.key === 'Escape') {
        const m = document.getElementById('matrix-modal');
        if (m && m.classList.contains('open')) {
          m.classList.remove('open');
          return;
        }
      }
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes((ev.target.tagName || '').toUpperCase())) return;
      if (ev.key === 'ArrowRight' || ev.key === 'j') {
        ev.preventDefault();
        stepDelta(1);
      } else if (ev.key === 'ArrowLeft' || ev.key === 'k') {
        ev.preventDefault();
        stepDelta(-1);
      } else if (ev.key === '[') {
        ev.preventDefault();
        stepPackage(-1);
      } else if (ev.key === ']') {
        ev.preventDefault();
        stepPackage(1);
      } else if (ev.key === ' ') {
        ev.preventDefault();
        togglePlay();
      } else if (ev.key === '1') {
        setFormat('16x9');
      } else if (ev.key === '2') {
        setFormat('9x16');
      } else if (ev.key === '3') {
        setFormat('thumb_16x9');
      } else if (ev.key === '4') {
        setFormat('thumb_9x16');
      } else if (ev.key === 'g' || ev.key === 'G') {
        toggleGuides();
      } else if (ev.key === 'i' || ev.key === 'I') {
        toggleSidebar();
      } else if (ev.key === 'e' || ev.key === 'E') {
        setSidebarTab('editor');
      } else if (ev.key === 'm' || ev.key === 'M') {
        toggleMatrixModal();
      } else if (ev.key === 'f' || ev.key === 'F') {
        setScaleMode('fit');
      }
    });

    window.addEventListener('resize', updateViewportScale);

    function initStage() {
      const initFmt = urlParams.get('format') || '16x9';
      currentFormat = initFmt;
      STAGE.slides = activeSlidesDeck(initFmt);

      if (!isHeadless) {
        if (STAGE.has_audio && STAGE.audio_filename) {
          audioEl.src = STAGE.audio_filename;
        }
        renderStatusPills();
        syncEditorTextarea();
        buildSidebarSteps();
        buildTimelineSegments();
      }

      if (urlParams.get('guides') === '1') {
        showGuides = true;
        document.getElementById('btn-guides').classList.add('toggle-on');
      }
      if (urlParams.get('tab') === 'inspector') {
        setSidebarTab('inspector');
      } else if (urlParams.get('tab') === 'editor') {
        setSidebarTab('editor');
      }
      if (urlParams.get('matrix') === '1') {
        setTimeout(() => toggleMatrixModal(), 120);
      }
      if (urlParams.get('scale')) {
        const sc = urlParams.get('scale');
        scaleMode = sc === 'fit' ? 'fit' : (parseFloat(sc) || 'fit');
      }

      const initStep = parseInt(urlParams.get('step') || '0', 10);
      renderStageStep(Number.isFinite(initStep) ? initStep : 0, initFmt);
    }

    // Live server catalog, project switcher, package switcher & hot-reload
    if (window.location.protocol.startsWith('http') && !isHeadless) {
      const projParam = urlParams.get('project') || '';
      fetch(`/api/packages?project=${encodeURIComponent(projParam)}`)
        .then((r) => r.ok ? r.json() : null)
        .then((data) => {
          if (!data) return;
          catalogData = data;
          const projSel = document.getElementById('project-select');
          if (projSel && data.projects && data.projects.length) {
            projSel.innerHTML = '';
            data.projects.forEach((pr) => {
              const opt = document.createElement('option');
              opt.value = pr.id;
              opt.textContent = `${pr.label} (${pr.count})`;
              if (pr.id === data.active_project) opt.selected = true;
              projSel.appendChild(opt);
            });
            projSel.onchange = () => {
              window.location.href = `/?project=${encodeURIComponent(projSel.value)}`;
            };
          }

          const sel = document.getElementById('prop-select');
          if (sel && data.packages) {
            sel.innerHTML = '';
            data.packages.forEach((p) => {
              const opt = document.createElement('option');
              opt.value = p.rel_path;
              const st = p.status || {};
              const dot = st.render_status === 'ready' ? '🟢' : (st.audio_status === 'ready' ? '🟡' : '⚪');
              opt.textContent = `${dot} ${p.short_code} • ${p.title || p.prefix}`;
              if (p.prefix === STAGE.prefix) opt.selected = true;
              sel.appendChild(opt);
            });
            sel.onchange = () => {
              window.location.href = `/?project=${encodeURIComponent(data.active_project || '')}&prop=${encodeURIComponent(sel.value)}`;
            };
          }
          renderMatrixTable();
        })
        .catch(() => {});

      setInterval(() => {
        if (editorDirty) return;
        const propParam = urlParams.get('prop') || '';
        const prjParam = urlParams.get('project') || '';
        fetch(`/api/stage?project=${encodeURIComponent(prjParam)}&prop=${encodeURIComponent(propParam)}&mtime=${STAGE.mtime || 0}`)
          .then((r) => r.ok ? r.json() : null)
          .then((res) => {
            if (res && res.updated && res.stage) {
              STAGE = res.stage;
              renderStatusPills();
              syncEditorTextarea();
              buildSidebarSteps();
              buildTimelineSegments();
              renderStageStep(currentStep, currentFormat);
            }
          })
          .catch(() => {});
      }, 1200);
    }

    initStage();
  </script>
</body>
</html>
"""


def build_stage_html(
    package_dir: Path,
    out_html: Path | None = None,
    prefix: str | None = None,
) -> Path:
    """Build the interactive `stage.html` inside `package_dir` and return its path."""
    package_dir = Path(package_dir).resolve()
    stage_data = build_stage_data(package_dir, prefix=prefix)
    pfx = stage_data["prefix"]
    target_html = Path(out_html).resolve() if out_html else (package_dir / "stage.html")

    doc = (
        STAGE_HTML_TEMPLATE.replace("__SHORT_CODE__", html.escape(stage_data["short_code"]))
        .replace("__PREFIX__", html.escape(pfx))
        .replace("__SHORT_TITLE__", html.escape(stage_data["short_title"]))
        .replace("__ENUNCIATION__", html.escape(stage_data["enunciation"]))
        .replace("__STEP_TOTAL__", str(len(stage_data["slides"])))
        .replace("__STAGE_JSON__", json.dumps(stage_data).replace("</", "<\\/"))
    )
    target_html.write_text(doc, encoding="utf-8")
    return target_html


def _extract_pkg_meta(pkg_dir: Path, videos_root: Path) -> dict[str, Any]:
    rel_path = str(pkg_dir.relative_to(videos_root))
    pfx = infer_package_prefix(pkg_dir)
    scr = pkg_dir / "script.scr"
    txt = pkg_dir / "script.txt"
    scenes_f = pkg_dir / "scenes.json"

    short_code = pfx.replace("_0", ".").replace("_", ".")
    m_x = re.match(r"^X_0*(\d+)$", pfx, re.I)
    if m_x:
        short_code = f"X({m_x.group(1)})"

    title = ""
    src_f = scr if scr.exists() else (txt if txt.exists() else None)
    if src_f and src_f.exists():
        for line in src_f.read_text(encoding="utf-8").splitlines()[:20]:
            s = line.strip()
            if s.lower().startswith("title:"):
                title = s.split(":", 1)[1].strip()
                break
            if s.startswith("#") and "•" in s and not title:
                title = s.lstrip("#").split("•", 1)[1].strip()
    if not title and scenes_f.exists():
        try:
            sc_list = json.loads(scenes_f.read_text(encoding="utf-8"))
            if sc_list and isinstance(sc_list, list):
                first = (sc_list[0].get("subslides") or [sc_list[0]])[0]
                title = first.get("short_title") or first.get("enunciation", "")[:48]
        except Exception:
            pass

    status = get_package_status(pkg_dir, prefix=pfx)
    return {
        "rel_path": rel_path,
        "prefix": pfx,
        "short_code": short_code,
        "title": title,
        "pkg_dir": str(pkg_dir),
        "status": status,
    }


def discover_propositions(videos_root: Path) -> list[dict[str, Any]]:
    """Scan a project's `videos/` root (`elements/videos`, `etc/videos`, etc.) for all video packages."""
    videos_root = Path(videos_root).resolve()
    seen_dirs: set[Path] = set()
    pkgs: list[dict[str, Any]] = []

    candidates: list[Path] = []
    for pat in ("book_*/*", "x-*", "intro-*", "topics/*"):
        for d in sorted(videos_root.glob(pat)):
            if d.is_dir():
                candidates.append(d)
    if not candidates:
        for f in sorted(videos_root.rglob("script.scr")):
            candidates.append(f.parent)

    for pkg_dir in candidates:
        pkg_dir = pkg_dir.resolve()
        if pkg_dir in seen_dirs:
            continue
        if not any((pkg_dir / name).exists() for name in ("script.scr", "script.txt", "scenes.json")):
            continue
        seen_dirs.add(pkg_dir)
        pkgs.append(_extract_pkg_meta(pkg_dir, videos_root))

    return pkgs


def discover_workspace_projects(active_videos_root: Path) -> dict[str, Path]:
    """Discover sibling GEOMETOR/PHOTON projects that have a `videos/` directory (`elements`, `etc`, etc.)."""
    active_videos_root = Path(active_videos_root).resolve()
    projects: dict[str, Path] = {}
    repo_dir = active_videos_root.parent
    workspace_dir = repo_dir.parent
    for cand_repo in sorted(workspace_dir.iterdir()) if workspace_dir.exists() else [repo_dir]:
        if not cand_repo.is_dir():
            continue
        v_dir = cand_repo / "videos"
        if v_dir.is_dir() and any(v_dir.rglob("scenes.json")) or any(v_dir.rglob("script.scr")):
            projects[cand_repo.name] = v_dir.resolve()
    if repo_dir.name not in projects:
        projects[repo_dir.name] = active_videos_root
    return projects


def summarize_packages(pkgs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(pkgs),
        "scr_count": sum(1 for p in pkgs if p.get("status", {}).get("has_scr")),
        "split_form_factor": sum(
            1
            for p in pkgs
            if p.get("status", {}).get("has_wide_override") or p.get("status", {}).get("has_short_override")
        ),
        "audio_ready": sum(1 for p in pkgs if p.get("status", {}).get("audio_status") == "ready"),
        "render_ready": sum(1 for p in pkgs if p.get("status", {}).get("render_status") == "ready"),
        "youtube_uploaded": sum(1 for p in pkgs if p.get("status", {}).get("youtube_status") not in ("local", "", None)),
    }


def serve_stage(
    target_path: Path,
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Start the Live Studio Stage Workstation HTTP server with live `script.scr` editing,
    TTS audio regeneration, 1440p + Short MP4 rendering, and multi-project navigation."""
    import time
    from .speak import synthesize_script

    target_path = Path(target_path).resolve()
    if any((target_path / name).exists() for name in ("script.scr", "script.txt", "scenes.json")):
        default_pkg = target_path
        if target_path.parent.name in ("videos",):
            videos_root = target_path.parent
        elif target_path.parent.parent.name in ("videos",):
            videos_root = target_path.parent.parent
        else:
            videos_root = target_path.parent
    else:
        videos_root = target_path if target_path.name == "videos" else (target_path / "videos" if (target_path / "videos").exists() else target_path)
        all_props = discover_propositions(videos_root)
        if not all_props:
            raise FileNotFoundError(f"No video packages found under {target_path}")
        default_pkg = Path(all_props[0]["pkg_dir"])

    projects_map = discover_workspace_projects(videos_root)
    default_project_id = videos_root.parent.name

    class StageHTTPHandler(SimpleHTTPRequestHandler):
        def _resolve_project_and_pkg(self, qs: dict[str, list[str]], body_data: dict[str, Any] | None = None) -> tuple[str, Path, Path]:
            b = body_data or {}
            proj_id = (b.get("project") or (qs.get("project") or [""])[0]).strip()
            if not proj_id or proj_id not in projects_map:
                proj_id = default_project_id
            v_root = projects_map.get(proj_id, videos_root)

            prop_rel = (b.get("prop") or (qs.get("prop") or [""])[0]).strip()
            if prop_rel:
                cand = (v_root / prop_rel).resolve()
                if cand.exists():
                    return proj_id, v_root, cand
            if proj_id != default_project_id:
                pkgs = discover_propositions(v_root)
                if pkgs:
                    return proj_id, v_root, Path(pkgs[0]["pkg_dir"])
            return proj_id, v_root, default_pkg

        def _send_json(self, obj: dict[str, Any], status_code: int = 200) -> None:
            payload = json.dumps(obj).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _attach_media_urls(self, st: dict[str, Any], proj_id: str, v_root: Path, pkg: Path) -> dict[str, Any]:
            rel_pkg = pkg.relative_to(v_root) if pkg.is_relative_to(v_root) else Path(".")
            if st.get("has_audio") and st.get("audio_filename") and not str(st["audio_filename"]).startswith("/media/"):
                st["audio_filename"] = f"/media/{proj_id}/{rel_pkg}/{st['audio_filename']}"
            for sf in st.get("section_folders", []):
                sf["audio_http_url"] = f"/media/{proj_id}/{rel_pkg}/{sf['audio_url']}"
                sf["clip_16x9_http_url"] = f"/media/{proj_id}/{rel_pkg}/{sf['clip_16x9_url']}"
                sf["clip_9x16_http_url"] = f"/media/{proj_id}/{rel_pkg}/{sf['clip_9x16_url']}"
            return st

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)

            if parsed.path in ("/", "/stage.html"):
                proj_id, v_root, pkg = self._resolve_project_and_pkg(qs)
                stage_data = self._attach_media_urls(build_stage_data(pkg), proj_id, v_root, pkg)
                doc = (
                    STAGE_HTML_TEMPLATE.replace("__SHORT_CODE__", html.escape(stage_data["short_code"]))
                    .replace("__PREFIX__", html.escape(stage_data["prefix"]))
                    .replace("__SHORT_TITLE__", html.escape(stage_data["short_title"]))
                    .replace("__ENUNCIATION__", html.escape(stage_data["enunciation"]))
                    .replace("__STEP_TOTAL__", str(len(stage_data["slides"])))
                    .replace("__STAGE_JSON__", json.dumps(stage_data).replace("</", "<\\/"))
                )
                body = doc.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed.path in ("/api/packages", "/api/propositions"):
                proj_id, v_root, _ = self._resolve_project_and_pkg(qs)
                pkgs = discover_propositions(v_root)
                proj_list = []
                for pid, p_root in projects_map.items():
                    p_pkgs = pkgs if pid == proj_id else discover_propositions(p_root)
                    proj_list.append({
                        "id": pid,
                        "label": pid.upper(),
                        "count": len(p_pkgs),
                    })
                self._send_json({
                    "active_project": proj_id,
                    "projects": proj_list,
                    "packages": pkgs,
                    "propositions": pkgs,
                    "summary": summarize_packages(pkgs),
                })
                return

            if parsed.path == "/api/stage":
                proj_id, v_root, pkg = self._resolve_project_and_pkg(qs)
                client_mtime = float((qs.get("mtime") or ["0"])[0] or 0.0)
                mtimes = [
                    p.stat().st_mtime
                    for p in (pkg / "script.scr", pkg / "script.wide.scr", pkg / "script.short.scr", pkg / "script.txt", pkg / "scenes.json")
                    if p.exists()
                ]
                cur_mtime = max(mtimes) if mtimes else 0.0
                if cur_mtime > client_mtime + 1e-4:
                    st = self._attach_media_urls(build_stage_data(pkg), proj_id, v_root, pkg)
                    self._send_json({"updated": True, "stage": st})
                else:
                    self._send_json({"updated": False, "mtime": cur_mtime})
                return

            if parsed.path.startswith("/media/"):
                rel = parsed.path[len("/media/") :]
                parts = rel.split("/", 1)
                if len(parts) == 2 and parts[0] in projects_map:
                    f_path = (projects_map[parts[0]] / parts[1]).resolve()
                else:
                    f_path = (videos_root / rel).resolve()
                if f_path.exists() and f_path.is_file():
                    data = f_path.read_bytes()
                    ext = f_path.suffix.lower()
                    ctype = "application/octet-stream"
                    if ext == ".wav":
                        ctype = "audio/wav"
                    elif ext == ".mp4":
                        ctype = "video/mp4"
                    elif ext == ".png":
                        ctype = "image/png"
                    elif ext == ".json":
                        ctype = "application/json"
                    elif ext in (".scr", ".txt", ".srt", ".vtt"):
                        ctype = "text/plain; charset=utf-8"
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return

            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                body_data = json.loads(raw_body)
            except Exception:
                body_data = {}

            proj_id, v_root, pkg = self._resolve_project_and_pkg(qs, body_data)

            if parsed.path == "/api/save-script":
                try:
                    variant = str(body_data.get("variant", "shared")).lower()
                    content = str(body_data.get("content", ""))
                    if variant == "wide":
                        target_f = pkg / "script.wide.scr"
                    elif variant == "short":
                        target_f = pkg / "script.short.scr"
                    else:
                        target_f = pkg / "script.scr" if (pkg / "script.scr").exists() or not (pkg / "script.txt").exists() else (pkg / "script.txt")

                    if content.strip() or variant == "shared":
                        target_f.write_text(content, encoding="utf-8")
                    elif target_f.exists() and variant in ("wide", "short"):
                        target_f.unlink()

                    build_stage_html(pkg)
                    st = self._attach_media_urls(build_stage_data(pkg), proj_id, v_root, pkg)
                    self._send_json({"status": "ok", "saved_file": target_f.name, "stage": st})
                except Exception as exc:
                    self._send_json({"status": "error", "error": str(exc)}, status_code=400)
                return

            if parsed.path == "/api/speak":
                try:
                    t0 = time.monotonic()
                    variant = str(body_data.get("variant", "16x9"))
                    src_script = resolve_script_source_for_variant(pkg, variant)
                    pfx = infer_package_prefix(pkg)
                    synthesize_script(src_script, out_dir=pkg, prefix=pfx, variant=variant)
                    st = build_stage_data(pkg, prefix=pfx)
                    _ensure_stage_master_wav(pkg, st)
                    build_stage_html(pkg, prefix=pfx)
                    st = self._attach_media_urls(build_stage_data(pkg, prefix=pfx), proj_id, v_root, pkg)
                    self._send_json({
                        "status": "ok",
                        "elapsed_sec": round(time.monotonic() - t0, 2),
                        "stage": st,
                    })
                except Exception as exc:
                    self._send_json({"status": "error", "error": str(exc)}, status_code=500)
                return

            if parsed.path in ("/api/render", "/api/render-section"):
                try:
                    target_sec = str(body_data.get("section") or "").strip() or None
                    force_flag = bool(body_data.get("force", False))
                    res = render_stage_package(
                        pkg,
                        target_section=target_sec,
                        force=force_flag,
                    )
                    st = self._attach_media_urls(build_stage_data(pkg), proj_id, v_root, pkg)
                    self._send_json({"status": "ok", "render": res, "stage": st})
                except Exception as exc:
                    self._send_json({"status": "error", "error": str(exc)}, status_code=500)
                return

            self.send_error(404)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), StageHTTPHandler)
    url = f"http://127.0.0.1:{port}/"
    print(f"GEOMETOR Studio Stage Workstation running at {url}")
    print(f"Active project: {default_project_id} ({len(projects_map)} workspace projects discovered)")
    print(f"Target package: {default_pkg}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _format_srt_time(seconds: float) -> str:
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds)
    hrs = s // 3600
    mins = (s % 3600) // 60
    secs = s % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"


def _format_vtt_time(seconds: float) -> str:
    return _format_srt_time(seconds).replace(",", ".")


def _ensure_stage_master_wav(package_dir: Path, stage_data: dict[str, Any]) -> Path:
    """Ensure `<prefix>.wav` includes any sound-only post-speech sections (`review` and `outro`),
    preserving a backup of the raw spoken track in `<prefix>_spoken.wav` for idempotent re-builds."""
    import shutil
    import wave
    from .speak import (
        SAMPLE_RATE,
        load_profile,
        make_harmonic_bell_pcm,
        make_sfx_pcm,
        silence_pcm,
    )

    pfx = stage_data["prefix"]
    wav_path = package_dir / f"{pfx}.wav"
    spoken_backup = package_dir / f"{pfx}_spoken.wav"

    slides = stage_data.get("slides_16x9") or stage_data.get("slides", [])
    synth_slides = [s for s in slides if s.get("use_synth_audio")]
    if not synth_slides:
        return wav_path

    first_synth_start = float(synth_slides[0]["start_sec"])
    profile = load_profile()
    bells_cfg = profile.get("bells", {})

    base_pcm = b""
    sample_rate = SAMPLE_RATE
    if wav_path.exists():
        if not spoken_backup.exists():
            shutil.copy2(wav_path, spoken_backup)
        src_wav = spoken_backup if spoken_backup.exists() else wav_path
        with wave.open(str(src_wav), "rb") as wf:
            sample_rate = wf.getframerate()
            n_spoken_frames = min(wf.getnframes(), int(round(first_synth_start * sample_rate)))
            base_pcm = wf.readframes(n_spoken_frames)
    else:
        base_pcm = silence_pcm(first_synth_start, sample_rate=sample_rate)

    extra_chunks: list[bytes] = []
    for s in synth_slides:
        target_dur = float(s.get("duration", 0.85))
        target_bytes = int(round(target_dur * sample_rate)) * 2
        slide_chunks: list[bytes] = []
        for raw_cue in s.get("sfx_cues", []):
            cue_low = str(raw_cue).strip("[]").lower()
            if cue_low.startswith("bell:"):
                b = bells_cfg.get(cue_low)
                if b:
                    slide_chunks.append(
                        make_harmonic_bell_pcm(
                            b["freqs"],
                            duration=b.get("duration", 0.45),
                            amp=b.get("amp", 0.15),
                            sample_rate=sample_rate,
                        )
                    )
                    slide_chunks.append(silence_pcm(0.06, sample_rate=sample_rate))
            elif cue_low.startswith("sfx:"):
                sfx_name = cue_low.split(":", 1)[1]
                sfx_b = make_sfx_pcm(sfx_name)
                if sfx_b:
                    slide_chunks.append(sfx_b)
        joined = b"".join(slide_chunks)
        if len(joined) < target_bytes:
            rem_samples = (target_bytes - len(joined)) // 2
            joined += b"\x00\x00" * rem_samples
        else:
            joined = joined[:target_bytes]
        extra_chunks.append(joined)

    full_pcm = base_pcm + b"".join(extra_chunks)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(full_pcm)

    return wav_path


def _ensure_segment_audio(
    package_dir: Path,
    sec_idx: int,
    sec_id: str,
    sec_dir: Path,
    bell: str,
    slides_16_sec: list[dict[str, Any]],
    audio_hash: str,
    master_wav: Path,
    force: bool = False,
) -> tuple[Path, list[dict[str, Any]], bool]:
    """Ensure `segments/<NN>_<sec_id>/audio.wav` and `steps/step_<KK>.wav` exist and match `audio_hash`.
    Uses `.tts_cache/` (or Gemini TTS if a phrase is new and API key is set, or slices `master_wav` if offline).
    Returns `(sec_audio_wav, subsegments_list, was_regenerated)`."""
    import hashlib
    import os
    import wave
    from .speak import (
        SAMPLE_RATE,
        SINGLE_PAUSE_RE,
        apply_pronunciations,
        extract_sfx_cues,
        load_profile,
        make_harmonic_bell_pcm,
        make_sfx_pcm,
        silence_pcm,
        synth_pcm_cached,
    )

    steps_dir = sec_dir / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)
    sec_wav = sec_dir / "audio.wav"
    sec_json = sec_dir / "section.json"

    if not force and sec_wav.exists() and sec_json.exists():
        try:
            saved = json.loads(sec_json.read_text(encoding="utf-8"))
            if saved.get("audio_hash") == audio_hash and all(
                (steps_dir / f"step_{k:02d}.wav").exists() for k in range(len(slides_16_sec))
            ):
                subsegs: list[dict[str, Any]] = []
                cur_s = 0.0
                for k, sl in enumerate(slides_16_sec):
                    sw = steps_dir / f"step_{k:02d}.wav"
                    with wave.open(str(sw), "rb") as wf:
                        dur = wf.getnframes() / float(wf.getframerate() or SAMPLE_RATE)
                    subsegs.append({
                        "index": k,
                        "text": sl.get("say_clean", ""),
                        "rel_start_sec": round(cur_s, 3),
                        "rel_end_sec": round(cur_s + dur, 3),
                        "duration_sec": round(dur, 3),
                    })
                    cur_s += dur
                return sec_wav, subsegs, False
        except Exception:
            pass

    profile = load_profile()
    voice_id = profile["voice_id"]
    model = profile.get("model", "gemini-3.8-flash-tts")
    pronunciations = profile.get("pronunciations", {})
    bells_cfg = profile.get("bells", {})
    styles = profile["styles"]
    default_style_key = profile.get("default_style", "brisk")
    bell_cfg = bells_cfg.get(bell or "", {})
    style_key = bell_cfg.get("auto_style", default_style_key)
    if style_key not in styles:
        style_key = default_style_key
    style_str = styles[style_key]

    cache_dir = package_dir / ".tts_cache"
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

    master_pcm_all = b""
    master_sr = SAMPLE_RATE
    if master_wav.exists():
        try:
            with wave.open(str(master_wav), "rb") as mwf:
                master_sr = mwf.getframerate()
                master_pcm_all = mwf.readframes(mwf.getnframes())
        except Exception:
            master_pcm_all = b""

    sec_pcm_chunks: list[bytes] = []
    subsegs_out: list[dict[str, Any]] = []
    rel_cursor_samples = 0

    for k, sl in enumerate(slides_16_sec):
        is_sound_only = bool(sl.get("sound_only", False))
        hold_target_sec = float(sl.get("duration", 0.85))
        say_raw = str(sl.get("say_raw", ""))
        sfx_cues, phrase_no_sfx = extract_sfx_cues(say_raw)
        clean_text = "" if is_sound_only else apply_pronunciations(phrase_no_sfx, pronunciations)

        cache_key = (
            hashlib.sha256(f"{model}|{voice_id}|{style_str}|{clean_text}".encode("utf-8")).hexdigest()[:24]
            if clean_text
            else ""
        )
        cache_file = (cache_dir / f"{cache_key}.pcm") if cache_key else None
        can_synth_step = bool(is_sound_only or not clean_text or (cache_file and cache_file.exists()) or api_key)

        step_pcm = b""
        if can_synth_step:
            p_chunks: list[bytes] = []
            if k == 0 and sec_idx > 0:
                p_chunks.append(silence_pcm(0.14))
            if k == 0 and bell and bell in bells_cfg:
                b = bells_cfg[bell]
                p_chunks.append(
                    make_harmonic_bell_pcm(
                        b["freqs"],
                        duration=b.get("duration", 0.45),
                        amp=b.get("amp", 0.15),
                    )
                )
                p_chunks.append(silence_pcm(0.08))
            elif k > 0 and not is_sound_only:
                p_chunks.append(silence_pcm(0.42))

            for cue in sfx_cues:
                sfx_b = make_sfx_pcm(cue)
                if sfx_b:
                    p_chunks.append(sfx_b)

            if clean_text and not is_sound_only:
                v_pcm = synth_pcm_cached(voice_id, clean_text, style_str, model, api_key, cache_dir=cache_dir)
                p_chunks.append(v_pcm)
            else:
                elapsed_s = (sum(len(c) // 2 for c in p_chunks)) / float(SAMPLE_RATE)
                rem_s = max(0.15, hold_target_sec - elapsed_s)
                p_chunks.append(silence_pcm(rem_s))

            step_pcm = b"".join(p_chunks)
        elif master_pcm_all:
            st_frame = max(0, int(round(float(sl.get("start_sec", 0.0)) * master_sr)))
            en_frame = max(st_frame + int(0.2 * master_sr), int(round(float(sl.get("end_sec", 1.0)) * master_sr)))
            step_pcm = master_pcm_all[st_frame * 2 : en_frame * 2]
        else:
            step_pcm = silence_pcm(max(0.5, hold_target_sec))

        sw_path = steps_dir / f"step_{k:02d}.wav"
        with wave.open(str(sw_path), "wb") as swf:
            swf.setnchannels(1)
            swf.setsampwidth(2)
            swf.setframerate(SAMPLE_RATE)
            swf.writeframes(step_pcm)

        step_samples = len(step_pcm) // 2
        sub_clean_cap = SINGLE_PAUSE_RE.sub(" ", phrase_no_sfx)
        sub_clean_cap = re.sub(r"\s+", " ", sub_clean_cap).strip()
        subsegs_out.append({
            "index": k,
            "text": sub_clean_cap or " ".join(f"[sfx:{c}]" for c in sfx_cues),
            "rel_start_sec": round(rel_cursor_samples / float(SAMPLE_RATE), 3),
            "rel_end_sec": round((rel_cursor_samples + step_samples) / float(SAMPLE_RATE), 3),
            "duration_sec": round(step_samples / float(SAMPLE_RATE), 3),
        })
        rel_cursor_samples += step_samples
        sec_pcm_chunks.append(step_pcm)

    with wave.open(str(sec_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(sec_pcm_chunks))

    return sec_wav, subsegs_out, True


def _sync_master_wav_and_manifest_from_segments(
    package_dir: Path,
    prefix: str,
    sec_records: list[dict[str, Any]],
) -> tuple[Path, Path]:
    """Assemble `<prefix>.wav` and `<prefix>.json` by concatenating `segments/<NN>_<sec_id>/audio.wav` across all sections."""
    import wave
    from .speak import SAMPLE_RATE, load_profile

    profile = load_profile()
    voice_id = profile.get("voice_id", "voice_xdhacg1pn1cq")
    master_wav = package_dir / f"{prefix}.wav"
    manifest_json = package_dir / f"{prefix}.json"

    all_pcm: list[bytes] = []
    timing_segments: list[dict[str, Any]] = []
    cursor_samples = 0

    for idx, rec in enumerate(sec_records):
        sec_wav: Path = rec["sec_wav"]
        with wave.open(str(sec_wav), "rb") as wf:
            pcm = wf.readframes(wf.getnframes())
        seg_start_samples = cursor_samples
        seg_samples = len(pcm) // 2
        all_pcm.append(pcm)

        subsegments: list[dict[str, Any]] = []
        texts: list[str] = []
        for sub in rec["subsegments"]:
            st_s = round(seg_start_samples / float(SAMPLE_RATE) + float(sub["rel_start_sec"]), 3)
            en_s = round(seg_start_samples / float(SAMPLE_RATE) + float(sub["rel_end_sec"]), 3)
            dur_s = round(float(sub["duration_sec"]), 3)
            subsegments.append({
                "index": sub["index"],
                "text": sub["text"],
                "start_sec": st_s,
                "end_sec": en_s,
                "duration_sec": dur_s,
            })
            if sub["text"] and not str(sub["text"]).startswith("[sfx:"):
                texts.append(sub["text"])

        cursor_samples += seg_samples
        timing_segments.append({
            "index": idx,
            "section_id": rec["section_id"],
            "folder_name": rec["folder_name"],
            "bell": rec["bell"] or None,
            "style": "harmonic_review" if rec["section_id"] == "review" else "speaking briskly, nimble, crisp, and composed",
            "text": " ".join(texts),
            "start_sec": round(seg_start_samples / float(SAMPLE_RATE), 3),
            "end_sec": round(cursor_samples / float(SAMPLE_RATE), 3),
            "duration_sec": round(seg_samples / float(SAMPLE_RATE), 3),
            "subsegments": subsegments,
        })

    with wave.open(str(master_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(all_pcm))

    manifest_json.write_text(
        json.dumps(
            {
                "voice_id": voice_id,
                "audio_wav": str(master_wav),
                "total_duration_sec": round(cursor_samples / float(SAMPLE_RATE), 3),
                "segments": timing_segments,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return master_wav, manifest_json


def _cdp_capture_stage_slides(
    chrome_bin: str,
    stage_uri: str,
    jobs_16x9: list[tuple[int, list[Path]]],
    jobs_9x16: list[tuple[int, list[Path]]],
    thumb_16x9_png: Path | None = None,
    thumb_9x16_png: Path | None = None,
) -> bool:
    """Launch ONE headless Chromium instance, load `stage.html?headless=1` ONCE,
    and capture only the requested 16:9 QHD (2560x1440), 9:16 Short (1080x1920), and thumbnail PNGs
    in a single tab via Chrome DevTools Protocol over `--remote-debugging-pipe`."""
    import base64
    import os
    import shutil
    import subprocess
    import time
    import uuid

    if not jobs_16x9 and not jobs_9x16 and thumb_16x9_png is None and thumb_9x16_png is None:
        return True

    uid = uuid.uuid4().hex[:10]
    tmp_root = Path(f"/tmp/studio_cdp_{uid}")
    tmp_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(tmp_root / "config")
    env["XDG_CACHE_HOME"] = str(tmp_root / "cache")
    env["HOME"] = str(tmp_root / "home")
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)

    r1, w1 = os.pipe()
    r2, w2 = os.pipe()
    holders: list[int] = []
    while True:
        fd = os.open("/dev/null", os.O_RDONLY)
        holders.append(fd)
        if fd > 10:
            break
    p2c_r = os.dup(r1)
    p2c_w = os.dup(w1)
    c2p_r = os.dup(r2)
    c2p_w = os.dup(w2)
    for fd in (r1, w1, r2, w2, *holders):
        try:
            os.close(fd)
        except OSError:
            pass

    os.dup2(p2c_r, 3)
    os.dup2(c2p_w, 4)
    os.close(p2c_r)
    os.close(c2p_w)

    proc = subprocess.Popen(
        [
            chrome_bin,
            "--headless=new",
            "--remote-debugging-pipe",
            f"--user-data-dir={tmp_root / 'profile'}",
            "--disk-cache-size=1",
            "--media-cache-size=1",
            "--disable-application-cache",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--window-size=1920,1080",
            f"{stage_uri}?headless=1&step=0&format=16x9",
        ],
        pass_fds=(3, 4),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(3)
    os.close(4)

    buf = b""
    msg_id = 0
    try:
        def _cdp_call(
            method: str,
            params: dict[str, Any] | None = None,
            session_id: str | None = None,
        ) -> dict[str, Any]:
            nonlocal buf, msg_id
            msg_id += 1
            req_id = msg_id
            payload: dict[str, Any] = {"id": req_id, "method": method, "params": params or {}}
            if session_id:
                payload["sessionId"] = session_id
            raw = json.dumps(payload).encode("utf-8") + b"\x00"
            os.write(p2c_w, raw)
            while True:
                while b"\x00" not in buf:
                    chunk = os.read(c2p_r, 65536)
                    if not chunk:
                        raise EOFError("CDP pipe closed")
                    buf += chunk
                line, buf = buf.split(b"\x00", 1)
                if not line:
                    continue
                msg = json.loads(line.decode("utf-8"))
                if msg.get("id") == req_id:
                    if "error" in msg:
                        raise RuntimeError(str(msg["error"]))
                    return msg.get("result", {})

        target_id = ""
        for _ in range(50):
            res = _cdp_call("Target.getTargets")
            for t in res.get("targetInfos", []):
                if t.get("type") == "page":
                    target_id = t.get("targetId", "")
                    break
            if target_id:
                break
            time.sleep(0.05)
        if not target_id:
            return False

        attach = _cdp_call("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        sid = attach["sessionId"]
        _cdp_call("Page.enable", {}, session_id=sid)
        _cdp_call("Runtime.enable", {}, session_id=sid)

        for _ in range(60):
            r = _cdp_call(
                "Runtime.evaluate",
                {
                    "expression": "Boolean(window.STAGE && typeof renderStageStep === 'function' && document.getElementById('stage-canvas') && document.getElementById('stage-canvas').children.length > 0)",
                    "returnByValue": True,
                },
                session_id=sid,
            )
            if r.get("result", {}).get("value") is True:
                break
            time.sleep(0.04)

        # 1. Capture requested 16:9 QHD slides (1920x1080 @ 4/3 = 2560x1440) + 16:9 thumbnail
        if jobs_16x9 or thumb_16x9_png is not None:
            _cdp_call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1920, "height": 1080, "deviceScaleFactor": 4.0 / 3.0, "mobile": False},
                session_id=sid,
            )
            for idx, dest_paths in jobs_16x9:
                _cdp_call(
                    "Runtime.evaluate",
                    {"expression": f"renderStageStep({idx}, '16x9'); true;", "returnByValue": True},
                    session_id=sid,
                )
                shot = _cdp_call(
                    "Page.captureScreenshot",
                    {"format": "png", "optimizeForSpeed": True},
                    session_id=sid,
                )
                png_bytes = base64.b64decode(shot["data"])
                for dp in dest_paths:
                    dp.parent.mkdir(parents=True, exist_ok=True)
                    dp.write_bytes(png_bytes)

            if thumb_16x9_png is not None:
                _cdp_call(
                    "Runtime.evaluate",
                    {"expression": "renderStageStep(0, 'thumb_16x9'); true;", "returnByValue": True},
                    session_id=sid,
                )
                shot_t16 = _cdp_call(
                    "Page.captureScreenshot",
                    {"format": "png", "optimizeForSpeed": True},
                    session_id=sid,
                )
                thumb_16x9_png.write_bytes(base64.b64decode(shot_t16["data"]))

        # 2. Capture requested 9:16 Short slides (1080x1920 @ 1.0 = 1080x1920) + 9:16 thumbnail
        if jobs_9x16 or thumb_9x16_png is not None:
            _cdp_call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1080, "height": 1920, "deviceScaleFactor": 1.0, "mobile": False},
                session_id=sid,
            )
            for idx, dest_paths in jobs_9x16:
                _cdp_call(
                    "Runtime.evaluate",
                    {"expression": f"renderStageStep({idx}, '9x16'); true;", "returnByValue": True},
                    session_id=sid,
                )
                shot = _cdp_call(
                    "Page.captureScreenshot",
                    {"format": "png", "optimizeForSpeed": True},
                    session_id=sid,
                )
                png_bytes = base64.b64decode(shot["data"])
                for dp in dest_paths:
                    dp.parent.mkdir(parents=True, exist_ok=True)
                    dp.write_bytes(png_bytes)

            if thumb_9x16_png is not None:
                _cdp_call(
                    "Runtime.evaluate",
                    {"expression": "renderStageStep(0, 'thumb_9x16'); true;", "returnByValue": True},
                    session_id=sid,
                )
                shot_t9 = _cdp_call(
                    "Page.captureScreenshot",
                    {"format": "png", "optimizeForSpeed": True},
                    session_id=sid,
                )
                thumb_9x16_png.write_bytes(base64.b64decode(shot_t9["data"]))

        return True
    except Exception:
        return False
    finally:
        for fd in (p2c_w, c2p_r):
            try:
                os.close(fd)
            except OSError:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp_root, ignore_errors=True)


def _write_segment_concat_manifest(png_paths: list[Path], durations: list[float], out_txt: Path) -> None:
    """Write an FFmpeg concat demuxer manifest for a segment folder."""
    if not png_paths:
        return
    lines: list[str] = []
    for p, dur in zip(png_paths, durations):
        rp = p.resolve()
        d = max(0.08, float(dur))
        lines.append(f"file '{rp}'")
        lines.append(f"duration {d:.4f}")
    lines.append(f"file '{png_paths[-1].resolve()}'")
    out_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _encode_segment_clip(concat_txt: Path, sec_wav: Path, out_mp4: Path) -> None:
    import subprocess

    cmd = [
        "/usr/bin/ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_txt),
        "-i",
        str(sec_wav),
        "-vsync",
        "vfr",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out_mp4),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def render_stage_package(
    package_dir: Path,
    prefix: str | None = None,
    target_section: str | None = None,
    force: bool = False,
    max_workers: int = 6,
) -> dict[str, Any]:
    """Render a video package (`elements` or `etc`) using the **Per-Segment Folder Pipeline**:
    1. Every script segment (`=== <section_id> ===`) is stored in `segments/<NN>_<section_id>/` with:
       - `section.scr`, `section.json`, `concat_16x9.txt`, `concat_9x16.txt`
       - `audio.wav` + per-step `steps/step_<KK>.wav`
       - `steps/step_<KK>_16x9.png` (`2560x1440` QHD) + `steps/step_<KK>_9x16.png` (`1080x1920` Short)
       - `clip_16x9.mp4` (`2560x1440` QHD) + `clip_9x16.mp4` (`1080x1920` Short)
    2. Only modified/missing sections (or a targeted `--section <id>`) are re-synthesized, re-captured, and re-encoded.
    3. All segment clips (`segments/*/clip_16x9.mp4` and `clip_9x16.mp4`) are assembled via fast `-c:v copy` stream copy into `<prefix>_1080p.mp4` and `<prefix>_short.mp4`.
    """
    from concurrent.futures import ThreadPoolExecutor
    import shutil
    import subprocess
    import time
    from .render import ensure_compressed_thumbnail

    t0 = time.monotonic()
    package_dir = Path(package_dir).resolve()
    stage_data = build_stage_data(package_dir, prefix=prefix)
    pfx = stage_data["prefix"]
    master_wav = _ensure_stage_master_wav(package_dir, stage_data)

    slides_16x9 = stage_data.get("slides_16x9") or stage_data["slides"]
    slides_9x16 = stage_data.get("slides_9x16") or stage_data["slides"]
    meta_header = {
        "prop_id": stage_data["prop_id"],
        "short_code": stage_data["short_code"],
        "short_title": stage_data["short_title"],
        "next_prop": stage_data["next_prop"],
        "g_id": stage_data["g_id"],
        "folio_display": stage_data["folio_display"],
        "enunciation": stage_data["enunciation"],
    }
    section_folders = inspect_package_segments(
        package_dir=package_dir,
        slides_16x9=slides_16x9,
        slides_9x16=slides_9x16,
        script_text=stage_data.get("script_text", ""),
        meta_header=meta_header,
    )

    target_norm = _sanitize_section_id(target_section) if target_section else ""

    def _matches_target(sf: dict[str, Any]) -> bool:
        if not target_norm:
            return False
        return (
            _sanitize_section_id(sf["section_id"]) == target_norm
            or _sanitize_section_id(sf["folder_name"]) == target_norm
            or target_norm in _sanitize_section_id(sf["folder_name"])
        )

    # Phase 1: Ensure per-segment audio (`audio.wav` + `steps/step_KK.wav`) & assemble `<prefix>.wav` + `<prefix>.json`
    segments_root = package_dir / "segments"
    segments_root.mkdir(parents=True, exist_ok=True)
    sec_audio_records: list[dict[str, Any]] = []
    any_audio_regen = False

    for sf in section_folders:
        sec_idx = sf["index"]
        sec_id = sf["section_id"]
        folder_name = sf["folder_name"]
        sec_dir = segments_root / folder_name
        sec_dir.mkdir(parents=True, exist_ok=True)
        s16 = [s for s in slides_16x9 if s.get("section_id") == sec_id]
        force_this = force or _matches_target(sf)

        sec_wav, subsegs, was_regen = _ensure_segment_audio(
            package_dir=package_dir,
            sec_idx=sec_idx,
            sec_id=sec_id,
            sec_dir=sec_dir,
            bell=sf["bell"],
            slides_16_sec=s16,
            audio_hash=sf["hashes"]["audio_hash"],
            master_wav=master_wav,
            force=force_this,
        )
        if was_regen:
            any_audio_regen = True
        sec_audio_records.append({
            "index": sec_idx,
            "section_id": sec_id,
            "folder_name": folder_name,
            "bell": sf["bell"],
            "sec_wav": sec_wav,
            "subsegments": subsegs,
        })

    master_wav, _ = _sync_master_wav_and_manifest_from_segments(package_dir, pfx, sec_audio_records)

    # Rebuild stage_data & stage.html now that exact segment audio timings are synchronized
    stage_data = build_stage_data(package_dir, prefix=pfx)
    stage_html = build_stage_html(package_dir, prefix=pfx)
    stage_uri = stage_html.resolve().as_uri()
    slides_16x9 = stage_data.get("slides_16x9") or stage_data["slides"]
    slides_9x16 = stage_data.get("slides_9x16") or stage_data["slides"]
    section_folders = inspect_package_segments(
        package_dir=package_dir,
        slides_16x9=slides_16x9,
        slides_9x16=slides_9x16,
        script_text=stage_data.get("script_text", ""),
        meta_header=meta_header,
    )

    # Phase 2: Determine which slides & thumbnails need headless Chromium capture
    slides_16x9_dir = package_dir / "slides_16x9"
    slides_9x16_dir = package_dir / "slides_9x16"
    slides_16x9_dir.mkdir(parents=True, exist_ok=True)
    slides_9x16_dir.mkdir(parents=True, exist_ok=True)

    thumb_16x9_png = package_dir / f"{pfx}_thumb.png"
    thumb_9x16_png = package_dir / f"{pfx}_short_thumb.png"
    need_thumb_16 = force or not thumb_16x9_png.exists()
    need_thumb_9 = force or not thumb_9x16_png.exists()

    jobs_16x9: list[tuple[int, list[Path]]] = []
    jobs_9x16: list[tuple[int, list[Path]]] = []

    for sf in section_folders:
        sec_id = sf["section_id"]
        folder_name = sf["folder_name"]
        steps_dir = segments_root / folder_name / "steps"
        steps_dir.mkdir(parents=True, exist_ok=True)
        s16 = [s for s in slides_16x9 if s.get("section_id") == sec_id]
        s9 = [s for s in slides_9x16 if s.get("section_id") == sec_id]
        force_this = force or _matches_target(sf)
        if target_norm and not _matches_target(sf) and sf["slides_16x9_ready"] and sf["slides_9x16_ready"]:
            continue

        if force_this or not sf["slides_16x9_ready"]:
            for k, sl in enumerate(s16):
                g_idx = sl["index"]
                p_seg = steps_dir / f"step_{k:02d}_16x9.png"
                p_leg = slides_16x9_dir / f"slide_{g_idx:02d}.png"
                jobs_16x9.append((g_idx, [p_seg, p_leg]))

        if force_this or not sf["slides_9x16_ready"]:
            for k, sl in enumerate(s9):
                g_idx = sl["index"]
                p_seg = steps_dir / f"step_{k:02d}_9x16.png"
                p_leg = slides_9x16_dir / f"slide_{g_idx:02d}.png"
                jobs_9x16.append((g_idx, [p_seg, p_leg]))

    t_cap_start = time.monotonic()
    chrome_bin = str(find_chrome())
    cdp_ok = _cdp_capture_stage_slides(
        chrome_bin=chrome_bin,
        stage_uri=stage_uri,
        jobs_16x9=jobs_16x9,
        jobs_9x16=jobs_9x16,
        thumb_16x9_png=thumb_16x9_png if need_thumb_16 else None,
        thumb_9x16_png=thumb_9x16_png if need_thumb_9 else None,
    )
    t_cap = time.monotonic()

    if need_thumb_16 and thumb_16x9_png.exists():
        ensure_compressed_thumbnail(thumb_16x9_png)
    if need_thumb_9 and thumb_9x16_png.exists():
        ensure_compressed_thumbnail(thumb_9x16_png)

    # Ensure legacy slides_16x9 / slides_9x16 copies exist from segment steps
    for sf in section_folders:
        sec_id = sf["section_id"]
        steps_dir = segments_root / sf["folder_name"] / "steps"
        for k, sl in enumerate([s for s in slides_16x9 if s.get("section_id") == sec_id]):
            src_p = steps_dir / f"step_{k:02d}_16x9.png"
            dst_p = slides_16x9_dir / f"slide_{sl['index']:02d}.png"
            if src_p.exists() and not dst_p.exists():
                shutil.copy2(src_p, dst_p)
        for k, sl in enumerate([s for s in slides_9x16 if s.get("section_id") == sec_id]):
            src_p = steps_dir / f"step_{k:02d}_9x16.png"
            dst_p = slides_9x16_dir / f"slide_{sl['index']:02d}.png"
            if src_p.exists() and not dst_p.exists():
                shutil.copy2(src_p, dst_p)

    # Phase 3: Encode per-segment MP4 clips (`clip_16x9.mp4` and `clip_9x16.mp4`) only for dirty/targeted sections
    rebuilt_sections: list[str] = []
    cached_sections: list[str] = []
    clip_16_list: list[Path] = []
    clip_9_list: list[Path] = []

    for sf in section_folders:
        sec_idx = sf["index"]
        sec_id = sf["section_id"]
        folder_name = sf["folder_name"]
        sec_dir = segments_root / folder_name
        steps_dir = sec_dir / "steps"
        sec_wav = sec_dir / "audio.wav"
        clip_16 = sec_dir / "clip_16x9.mp4"
        clip_9 = sec_dir / "clip_9x16.mp4"
        concat_16 = sec_dir / "concat_16x9.txt"
        concat_9 = sec_dir / "concat_9x16.txt"
        sec_scr_file = sec_dir / "section.scr"
        sec_json_file = sec_dir / "section.json"

        s16 = [s for s in slides_16x9 if s.get("section_id") == sec_id]
        s9 = [s for s in slides_9x16 if s.get("section_id") == sec_id]
        force_this = force or _matches_target(sf)

        sec_scr_file.write_text(sf.get("section_scr", ""), encoding="utf-8")

        pngs_16 = [steps_dir / f"step_{k:02d}_16x9.png" for k in range(len(s16))]
        pngs_9 = [steps_dir / f"step_{k:02d}_9x16.png" for k in range(len(s9))]
        durs_16 = [float(s["duration"]) for s in s16]
        sec_total_dur = sum(durs_16) if durs_16 else 1.0
        raw_durs_9 = [float(s["duration"]) for s in s9]
        sum_9 = sum(raw_durs_9) or 1.0
        durs_9 = [d * (sec_total_dur / sum_9) for d in raw_durs_9] if len(s9) != len(s16) else durs_16

        _write_segment_concat_manifest(pngs_16, durs_16, concat_16)
        _write_segment_concat_manifest(pngs_9, durs_9, concat_9)

        need_encode = force_this or not sf["clips_ready"] or not clip_16.exists() or not clip_9.exists()
        if target_norm and not _matches_target(sf) and clip_16.exists() and clip_9.exists():
            need_encode = False

        if need_encode:
            with ThreadPoolExecutor(max_workers=2) as pool:
                f1 = pool.submit(_encode_segment_clip, concat_16, sec_wav, clip_16)
                f2 = pool.submit(_encode_segment_clip, concat_9, sec_wav, clip_9)
                f1.result()
                f2.result()
            rebuilt_sections.append(folder_name)
        else:
            cached_sections.append(folder_name)

        steps_manifest = []
        for k, sl in enumerate(s16):
            steps_manifest.append({
                "step_in_section": k,
                "global_index_16x9": sl["index"],
                "step_num": sl["step_num"],
                "step_kind": sl["step_kind"],
                "duration_sec": round(float(sl["duration"]), 3),
                "start_sec": round(float(sl["start_sec"]), 3),
                "end_sec": round(float(sl["end_sec"]), 3),
                "symbolic": sl.get("symbolic", ""),
                "say_raw": sl.get("say_raw", ""),
                "say_clean": sl.get("say_clean", ""),
                "step_wav": f"steps/step_{k:02d}.wav",
                "png_16x9": f"steps/step_{k:02d}_16x9.png",
                "png_9x16": f"steps/step_{min(k, max(0, len(s9) - 1)):02d}_9x16.png" if s9 else "",
            })

        sec_json_file.write_text(
            json.dumps(
                {
                    "folder_name": folder_name,
                    "section_index": sec_idx,
                    "section_id": sec_id,
                    "bell": sf["bell"],
                    "audio_hash": sf["hashes"]["audio_hash"],
                    "visual_hash_16x9": sf["hashes"]["visual_hash_16x9"],
                    "visual_hash_9x16": sf["hashes"]["visual_hash_9x16"],
                    "clip_hash": sf["hashes"]["clip_hash"],
                    "start_sec": sf["start_sec"],
                    "end_sec": sf["end_sec"],
                    "duration_sec": sf["duration_sec"],
                    "audio_wav": "audio.wav",
                    "clip_16x9": "clip_16x9.mp4",
                    "clip_9x16": "clip_9x16.mp4",
                    "steps": steps_manifest,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        clip_16_list.append(clip_16.resolve())
        clip_9_list.append(clip_9.resolve())

    # Phase 4: Fast stream-copy assembly (`-c:v copy`) of all segment clips into `<prefix>_1080p.mp4` and `<prefix>_short.mp4`
    clips_16_txt = segments_root / "clips_16x9.txt"
    clips_9_txt = segments_root / "clips_9x16.txt"
    clips_16_txt.write_text("\n".join(f"file '{p}'" for p in clip_16_list) + "\n", encoding="utf-8")
    clips_9_txt.write_text("\n".join(f"file '{p}'" for p in clip_9_list) + "\n", encoding="utf-8")

    mp4_16x9 = package_dir / f"{pfx}_1080p.mp4"
    mp4_9x16 = package_dir / f"{pfx}_short.mp4"

    def _assemble_master_mp4(pair: tuple[Path, Path]) -> None:
        clips_txt, out_mp4 = pair
        cmd = [
            "/usr/bin/ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(clips_txt),
            "-i",
            str(master_wav),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-shortest",
            "-movflags",
            "+faststart",
            str(out_mp4),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(_assemble_master_mp4, [(clips_16_txt, mp4_16x9), (clips_9_txt, mp4_9x16)]))
    t_enc = time.monotonic()

    # Write SRT and VTT captions
    srt_blocks: list[str] = []
    vtt_blocks: list[str] = ["WEBVTT", ""]
    for i, s in enumerate(slides_16x9, start=1):
        st = float(s["start_sec"])
        en = float(s["end_sec"])
        txt = s["say_clean"]
        srt_blocks.append(f"{i}\n{_format_srt_time(st)} --> {_format_srt_time(en)}\n{txt}\n")
        vtt_blocks.append(f"{_format_vtt_time(st)} --> {_format_vtt_time(en)}\n{txt}\n")

    (package_dir / f"{pfx}.srt").write_text("\n".join(srt_blocks), encoding="utf-8")
    (package_dir / f"{pfx}.vtt").write_text("\n".join(vtt_blocks), encoding="utf-8")

    # Refresh stage.html so segment status badges reflect the completed build
    build_stage_html(package_dir, prefix=pfx)

    return {
        "status": "ok",
        "prefix": pfx,
        "stage_html": str(stage_html),
        "audio_wav": str(master_wav),
        "mp4_16x9": str(mp4_16x9),
        "mp4_9x16": str(mp4_9x16),
        "segments_dir": str(segments_root),
        "rebuilt_sections": rebuilt_sections,
        "cached_sections": cached_sections,
        "audio_regenerated": any_audio_regen,
        "slides_captured_16x9": len(jobs_16x9),
        "slides_captured_9x16": len(jobs_9x16),
        "slides_count": len(slides_16x9),
        "slides_9x16_count": len(slides_9x16),
        "total_duration_sec": stage_data["total_duration"],
        "cdp_single_browser": cdp_ok,
        "capture_sec": round(t_cap - t_cap_start, 2),
        "encode_sec": round(t_enc - t_cap, 2),
        "total_render_sec": round(t_enc - t0, 2),
    }



