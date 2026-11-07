"""Direct in-memory compiler for Studio Script (.scr) files (Elements & ETC).

Eliminates the need for on-disk scenes.json files by parsing top-level metadata,
modular `=== <section> ===` boundaries, `[screen: ...]` visual state blocks,
symbolic geometric selectors (`/ A B /`, `( A B )`, `< A B C >`), and `say:` lines
directly from `script.scr`.
"""

from __future__ import annotations

import html
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET


EUCLID_SVG_ROOT = Path("/home/phi/PROJECTS/geometor/euclid/resources/heath")

_COLOR_TAG_TO_CLS = {
    "gold": "tok-gold",
    "orange": "tok-orange",
    "teal": "tok-teal",
    "blue": "tok-blue",
    "purple": "tok-purple",
    "orchid": "tok-orchid",
    "pink": "tok-pink",
    "green": "tok-green",
    "lime": "tok-lime",
    "muted": "muted-tok",
}


def _normalize_geom_ref(ref: str) -> str:
    """Normalize whitespace and HTML entities in a symbolic geometric token."""
    s = html.unescape(ref).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _canonical_variants(ref: str) -> list[str]:
    """Return equivalent symbolic variants (e.g., `/ A C /` <-> `/ C A /` for segments, `< A B C >` permutations)."""
    s = _normalize_geom_ref(ref)
    out = [s]
    # Segment `/ X Y /` -> also `/ Y X /`
    m_seg = re.match(r"^/\s*([A-Za-z0-9_]+)\s+([A-Za-z0-9_]+)\s*/$", s)
    if m_seg:
        a, b = m_seg.group(1), m_seg.group(2)
        rev = f"/ {b} {a} /"
        if rev not in out:
            out.append(rev)
    return out


class EuclidSvgIndex:
    """Indexes an Euclid proposition SVG by its `data-ref` and `data-aliases` attributes."""

    def __init__(self, svg_path: Path) -> None:
        self.svg_path = svg_path
        self.ref_to_el_ids: dict[str, list[str]] = {}
        self.ref_to_hl_ids: dict[str, list[str]] = {}
        self.ref_to_lbl_ids: dict[str, list[str]] = {}
        self.ref_to_seg_ids: dict[str, list[str]] = {}
        if svg_path.exists():
            self._parse(svg_path)

    def _add(self, mapping: dict[str, list[str]], ref: str, el_id: str) -> None:
        for v in _canonical_variants(ref):
            bucket = mapping.setdefault(v, [])
            if el_id not in bucket:
                bucket.append(el_id)

    def _parse(self, svg_path: Path) -> None:
        txt = svg_path.read_text(encoding="utf-8")
        root = ET.fromstring(txt)
        for elem in root.iter():
            el_id = elem.attrib.get("id", "")
            if not el_id:
                continue
            data_ref = elem.attrib.get("data-ref", "")
            aliases_raw = elem.attrib.get("data-aliases", "")
            refs: list[str] = []
            if data_ref:
                refs.append(data_ref)
            if aliases_raw:
                # Split multiple parenthesized or slash aliases if present
                for m in re.finditer(r"\([^)]+\)|/[^/]+/|<[^>]+>|\[[^\]]+\]", aliases_raw):
                    refs.append(m.group(0))
                if not refs and aliases_raw.strip():
                    refs.append(aliases_raw.strip())

            for r in refs:
                if el_id.startswith("hl-"):
                    self._add(self.ref_to_hl_ids, r, el_id)
                elif el_id.startswith("lbl-"):
                    self._add(self.ref_to_lbl_ids, r, el_id)
                else:
                    self._add(self.ref_to_el_ids, r, el_id)
                    if el_id.startswith("el-seg-"):
                        self._add(self.ref_to_seg_ids, r, el_id)

    @staticmethod
    def _defining_points(t: str) -> list[str]:
        """Extract defining point symbols from `/ A B /`, `[ A B ]`, `( A B )`, or `< A B C >`."""
        m = re.match(r"^(?:/|\[|\(|<|\^)\s*(.+?)\s*(?:/|\]|\)|>|\^)$", t)
        if not m:
            return []
        return [p for p in m.group(1).split() if re.match(r"^[A-Z][A-Za-z0-9_]*$", p)]

    def expand_visible(self, tokens: list[str]) -> list[str]:
        if tokens == ["*"]:
            return ["*"]
        els: list[str] = []
        lbls: list[str] = []
        expanded_tokens: list[str] = []
        for tok in tokens:
            t = _normalize_geom_ref(tok)
            if t not in expanded_tokens:
                expanded_tokens.append(t)
            for pt in self._defining_points(t):
                if pt not in expanded_tokens:
                    expanded_tokens.append(pt)
        for t in expanded_tokens:
            for eid in self.ref_to_el_ids.get(t, []):
                if eid not in els:
                    els.append(eid)
            for lid in self.ref_to_lbl_ids.get(t, []):
                if lid not in lbls:
                    lbls.append(lid)
        return els + lbls

    def expand_active_or_equal(self, tokens: list[str], exclude_Ids: set[str] | None = None, only_polygon_pts: bool = False) -> list[str]:
        excl = exclude_Ids or set()
        out: list[str] = []
        # 1. Primary stated tokens (lines, circles, polygons, or explicit points)
        defining_pts: list[str] = []
        for tok in tokens:
            t = _normalize_geom_ref(tok)
            for eid in self.ref_to_el_ids.get(t, []):
                if eid not in out and eid not in excl:
                    out.append(eid)
            for hid in self.ref_to_hl_ids.get(t, []):
                if hid not in out and hid not in excl:
                    out.append(hid)
            for lid in self.ref_to_lbl_ids.get(t, []):
                if lid not in out and lid not in excl:
                    out.append(lid)
            if not only_polygon_pts or t.startswith("<"):
                for pt in self._defining_points(t):
                    if pt not in defining_pts:
                        defining_pts.append(pt)
        # 2. Automatically highlight defining points of stated lines/circles/polygons
        for pt in defining_pts:
            for eid in self.ref_to_el_ids.get(pt, []):
                if eid not in out and eid not in excl:
                    out.append(eid)
            for lid in self.ref_to_lbl_ids.get(pt, []):
                if lid not in out and lid not in excl:
                    out.append(lid)
        return out

    def expand_ticks(self, tokens: list[str]) -> list[str]:
        out: list[str] = []
        for tok in tokens:
            t = _normalize_geom_ref(tok)
            # Strip optional `= 1` tick count suffix if provided
            t = re.sub(r"\s*=\s*\d+$", "", t).strip()
            for sid in self.ref_to_seg_ids.get(t, []):
                if sid not in out:
                    out.append(sid)
        return out


def _resolve_euclid_svg_path(svg_spec: str, package_dir: Path) -> Path:
    p = Path(svg_spec)
    if p.is_absolute() and p.exists():
        return p
    local = package_dir / svg_spec
    if local.exists():
        return local.resolve()
    if EUCLID_SVG_ROOT.exists():
        matches = sorted(EUCLID_SVG_ROOT.rglob(p.name))
        if matches:
            return matches[0]
    return local


def _split_top_level_commas(val: str) -> list[str]:
    """Split a comma-separated list while keeping `{1, 2, 8, 10}` and `circle(A, B)` intact."""
    items: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in val:
        if ch in "{([":
            depth += 1
            cur.append(ch)
        elif ch in "})]":
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch == "," and depth == 0:
            s = "".join(cur).strip()
            if s:
                items.append(s)
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        items.append(tail)
    return items


def _highlight_euclid_clause_line(line_str: str) -> str:
    """Convert a symbolic Euclid clause line into syntax-highlighted HTML."""
    # Tokenize function calls, geometric tokens (`/ A B /`, `( A B )`, `< A B C >`, single capital letters), and `=`
    pattern = re.compile(
        r"(?P<fn>\b[a-z_]+)(?=\()|"
        r"(?P<geom>/\s*[A-Za-z0-9_]+(?:\s+[A-Za-z0-9_]+)+\s*/|"
        r"\(\s*[A-Za-z0-9_]+(?:\s+[A-Za-z0-9_]+)+\s*\)|"
        r"<\s*[A-Za-z0-9_]+(?:\s+[A-Za-z0-9_]+)+\s*>|"
        r"\b[A-Z]\b)|"
        r"(?P<eq>=)"
    )
    pos = 0
    out: list[str] = []
    for m in pattern.finditer(line_str):
        if m.start() > pos:
            out.append(html.escape(line_str[pos : m.start()]))
        if m.group("fn"):
            out.append(f'<span class="fn-tok">{html.escape(m.group("fn"))}</span>')
        elif m.group("geom"):
            out.append(f'<span class="g-tok">{html.escape(m.group("geom"))}</span>')
        elif m.group("eq"):
            out.append('<span class="eq-tok">=</span>')
        pos = m.end()
    if pos < len(line_str):
        out.append(html.escape(line_str[pos:]))
    return "".join(out)


def _format_euclid_clause_html(raw_clause: str) -> str:
    if not raw_clause:
        return ""
    if "<span" in raw_clause or "<div" in raw_clause:
        return raw_clause
    lines = [p.strip() for p in raw_clause.split("|") if p.strip()]
    if len(lines) == 1:
        return _highlight_euclid_clause_line(lines[0])
    return "".join(f'<div class="clause-line">{_highlight_euclid_clause_line(ln)}</div>' for ln in lines)


def _format_etc_clause_html(
    sections: list[str],
    head_str: str,
    rows: list[str],
    text_lines: list[str],
    raw_clause: str,
) -> str:
    """Compile ETC `section:`, `head:`/`row:`, or `text:` lines into `clause_html`."""
    if raw_clause:
        return raw_clause
    if sections:
        sec_blocks: list[str] = []
        for sec in sections:
            parts = [p.strip() for p in sec.split("|") if p.strip()]
            if not parts:
                continue
            sec_title = html.escape(parts[0])
            cell_vals: list[tuple[str, str]] = []
            for p in parts[1:]:
                if ":" in p:
                    val_part, c_tag = p.rsplit(":", 1)
                    cls_name = _COLOR_TAG_TO_CLS.get(c_tag.strip(), "tok-gold")
                    cell_vals.append((cls_name, val_part.strip()))
                else:
                    cell_vals.append(("tok-gold", p))
            max_len = max((len(v.split("=", 1)[-1].strip()) for _, v in cell_vals), default=0)
            stack_cls = " stack" if max_len > 11 else ""
            spans_html = "".join(f'<span class="{c}">{html.escape(v)}</span>' for c, v in cell_vals)
            sec_blocks.append(
                f'<div class="etc-section"><div class="etc-sec-head">{sec_title}</div>'
                f'<div class="etc-coord-grid{stack_cls}">{spans_html}</div></div>'
            )
        return f'<div class="etc-table-block">{"".join(sec_blocks)}</div>'

    if rows:
        row_blocks: list[str] = []
        for r in rows:
            parts = [p.strip() for p in r.split("|")]
            if len(parts) < 2:
                continue
            lbl = parts[0]
            cells_raw = parts[1:]
            if len(cells_raw) == 1:
                rhs = cells_raw[0]
                if ":" in rhs:
                    val_part, c_tag = rhs.rsplit(":", 1)
                    c_tag = c_tag.strip()
                    val_part = val_part.strip()
                else:
                    val_part, c_tag = rhs, "gold"
                cls_name = _COLOR_TAG_TO_CLS.get(c_tag, "tok-gold")
                row_cls = "etc-data-row muted-row" if c_tag == "muted" else "etc-data-row"
                row_blocks.append(
                    f'<div class="{row_cls}"><span class="etc-row-label">{html.escape(lbl)}</span>'
                    f'<span class="{cls_name}">{html.escape(val_part)}</span></div>'
                )
            else:
                default_c_tag = "gold"
                if ":" in cells_raw[-1]:
                    _, maybe_c = cells_raw[-1].rsplit(":", 1)
                    if maybe_c.strip() in _COLOR_TAG_TO_CLS:
                        default_c_tag = maybe_c.strip()
                cell_spans: list[str] = []
                all_muted = True
                for cell_str in cells_raw:
                    c_tag = default_c_tag
                    val_part = cell_str
                    if ":" in cell_str:
                        vp, ct = cell_str.rsplit(":", 1)
                        if ct.strip() in _COLOR_TAG_TO_CLS:
                            val_part = vp.strip()
                            c_tag = ct.strip()
                    if c_tag != "muted":
                        all_muted = False
                    cls_name = _COLOR_TAG_TO_CLS.get(c_tag, "tok-gold")
                    cell_spans.append(f'<span class="{cls_name}">{html.escape(val_part)}</span>')
                row_cls = "etc-data-row muted-row" if all_muted else "etc-data-row"
                row_blocks.append(
                    f'<div class="{row_cls}"><span class="etc-row-label">{html.escape(lbl)}</span>'
                    f'<div class="etc-row-cells">{"".join(cell_spans)}</div></div>'
                )

        if "|" in (head_str or ""):
            h_parts = [p.strip() for p in head_str.split("|") if p.strip()]
            h_title = html.escape(h_parts[0])
            col_spans = "".join(f"<span>{html.escape(cp)}</span>" for cp in h_parts[1:])
            head_html = (
                f'<div class="etc-sec-head-row"><span class="etc-sec-head">{h_title}</span>'
                f'<div class="etc-head-cols">{col_spans}</div></div>'
            )
        else:
            head_html = f'<div class="etc-sec-head">{html.escape(head_str or "Incident Central Lines")}</div>'
        return f'<div class="etc-table-block">{head_html}<div class="etc-data-list">{"".join(row_blocks)}</div></div>'

    if text_lines:
        combined = " ".join(text_lines).strip()
        combined = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", combined)

        def _tok_repl(m: re.Match) -> str:
            tok_txt = m.group(1)
            c_tag = m.group(2).strip()
            cls_name = _COLOR_TAG_TO_CLS.get(c_tag, "tok-gold")
            return f'<span class="{cls_name}">{tok_txt}</span>'

        combined = re.sub(
            r"\{([^{}]+?):(gold|orange|teal|blue|purple|orchid|pink|green|lime|muted)\}",
            _tok_repl,
            combined,
        )
        return f'<span class="clause-line">{combined}</span>'

    return ""


def compile_scr_text(script_text: str, package_dir: Path) -> list[dict[str, Any]]:
    """Compile a `.scr` (or unified `.txt`) script directly into the in-memory `scenes` list."""
    meta: dict[str, str] = {}
    sections: list[dict[str, Any]] = []
    cur_section: dict[str, Any] | None = None
    cur_screen: dict[str, Any] | None = None

    def _ensure_section(sec_name: str = "section_1", bell: str | None = None) -> dict[str, Any]:
        nonlocal cur_section
        if cur_section is None:
            cur_section = {"name": sec_name, "bell": bell, "screens": []}
            sections.append(cur_section)
        return cur_section

    def _flush_screen() -> None:
        nonlocal cur_screen
        if not cur_screen:
            return
        sec = _ensure_section()
        sec["screens"].append(cur_screen)
        cur_screen = None

    for raw_line in script_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # 1. Modular `=== <section> ===` boundary
        sec_m = re.match(r"^===+\s*(.+?)\s*===+$", line)
        if sec_m:
            _flush_screen()
            sec_name = sec_m.group(1).strip()
            cur_section = {"name": sec_name, "bell": None, "screens": []}
            sections.append(cur_section)
            continue

        # 2. `[bell:...]` tag
        bell_m = re.match(r"^\[bell:([^\]]+)\]$", line, flags=re.IGNORECASE)
        if bell_m:
            b_val = f"bell:{bell_m.group(1).strip()}"
            if cur_screen is not None and cur_section and cur_section["screens"]:
                _flush_screen()
                cur_section = {"name": f"act_{len(sections) + 1}", "bell": b_val, "screens": []}
                sections.append(cur_section)
            else:
                sec = _ensure_section(bell=b_val)
                if not sec.get("bell"):
                    sec["bell"] = b_val
            continue

        if line.lower().startswith("[style:"):
            continue

        # 3. `[screen: ...]` block header
        screen_m = re.match(r"^\[screen:\s*(.+)\]$", line, flags=re.IGNORECASE)
        if screen_m:
            _flush_screen()
            parts = [p.strip() for p in screen_m.group(1).split("|") if p.strip()]
            step_kind = parts[0] if parts else "CONSTRUCT"
            attrs: dict[str, str] = {}
            for p in parts[1:]:
                if ":" in p:
                    k, v = p.split(":", 1)
                    attrs[k.strip().lower()] = v.strip()
            cur_screen = {
                "step_kind": step_kind,
                "svg": attrs.get("svg", ""),
                "weight": float(attrs["weight"]) if "weight" in attrs and attrs["weight"] != "auto" else None,
                "is_punchline": attrs.get("punchline", "").lower() in ("true", "1", "yes"),
                "show": [],
                "active": [],
                "coincide": [],
                "equal": [],
                "ticks": [],
                "zoom": [],
                "thumb_zoom": [],
                "symbolic": "",
                "sections": [],
                "head": "",
                "rows": [],
                "text_lines": [],
                "raw_clause": "",
                "just_raw": "",
                "say_lines": [],
            }
            continue

        # 4. Top-level metadata header (before any section or screen)
        if cur_screen is None and not sections:
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip()
            continue

        # 5. Directives inside `[screen: ...]`
        if cur_screen is not None and ":" in line:
            key, val = line.split(":", 1)
            k_low = key.strip().lower()
            val = val.strip()
            if k_low in ("show", "visible"):
                cur_screen["show"] = _split_top_level_commas(val)
            elif k_low in ("active", "highlight"):
                cur_screen["active"] = _split_top_level_commas(val)
            elif k_low == "coincide":
                cur_screen["coincide"] = _split_top_level_commas(val)
            elif k_low == "equal":
                cur_screen["equal"] = _split_top_level_commas(val)
            elif k_low == "ticks":
                cur_screen["ticks"] = _split_top_level_commas(val)
            elif k_low == "zoom":
                cur_screen["zoom"] = _split_top_level_commas(val)
            elif k_low == "thumb_zoom":
                cur_screen["thumb_zoom"] = _split_top_level_commas(val)
            elif k_low == "symbolic":
                cur_screen["symbolic"] = val
            elif k_low == "section":
                cur_screen["sections"].append(val)
            elif k_low == "head":
                cur_screen["head"] = val
            elif k_low == "row":
                cur_screen["rows"].append(val)
            elif k_low == "text":
                cur_screen["text_lines"].append(val)
            elif k_low == "clause":
                cur_screen["raw_clause"] = val
            elif k_low == "just":
                cur_screen["just_raw"] = val
            elif k_low == "say":
                cur_screen["say_lines"].append(val)

    _flush_screen()
    sections = [s for s in sections if s["screens"]]

    is_euclid = "prop" in meta or ("svg" in meta and "center" not in meta and "topic" not in meta)
    total_steps = sum(len(s["screens"]) for s in sections)

    if is_euclid:
        svg_path = _resolve_euclid_svg_path(meta.get("svg", "0255.g1.svg"), package_dir)
        svg_index = EuclidSvgIndex(svg_path)
        hide_tokens = _split_top_level_commas(meta["hide"]) if "hide" in meta else []
        hidden_ids = svg_index.expand_visible(hide_tokens) if hide_tokens else []

        scenes: list[dict[str, Any]] = []
        step_counter = 0
        for sec in sections:
            subslides: list[dict[str, Any]] = []
            for scr in sec["screens"]:
                step_counter += 1
                say_joined = " ".join(scr["say_lines"]).strip()
                clean_words = re.sub(r"<[^>]+>|\[[^\]]+\]", "", say_joined).split()
                auto_w = max(0.35, len(clean_words) / 14.0) if clean_words else 1.0
                weight = scr["weight"] if scr["weight"] is not None else round(auto_w, 2)

                vis_ids = svg_index.expand_visible(scr["show"] or ["*"])
                # For active: if a polygon `< A B C >` is active and equal sides are also listed, don't auto-pull circle center points into equal
                act_ids = svg_index.expand_active_or_equal(scr["active"], only_polygon_pts=bool(scr["equal"]))
                eq_ids = svg_index.expand_active_or_equal(scr["equal"], exclude_Ids=set(act_ids))
                t_ids = svg_index.expand_ticks(scr["ticks"] or scr["equal"])

                j_parts = [p.strip() for p in scr["just_raw"].split("|")] if scr["just_raw"] else []
                just_id = j_parts[0] if len(j_parts) >= 1 else ""
                just_title = j_parts[1] if len(j_parts) >= 2 else ""

                sub_dict: dict[str, Any] = {
                    "type": "euclid_step",
                    "weight": weight,
                    "prop_id": meta.get("prop", "BOOK I • PROP. 1"),
                    "short_title": meta.get("title", ""),
                    "g_id": meta.get("g_id", ""),
                    "folio": meta.get("folio", ""),
                    "enunciation": meta.get("enunciation", ""),
                    "step_num": step_counter,
                    "step_total": total_steps,
                    "step_kind": scr["step_kind"],
                    "chapter": scr["symbolic"] or scr["raw_clause"],
                    "svg_path": str(svg_path),
                    "visible_ids": vis_ids,
                    "hidden_ids": hidden_ids,
                    "active_ids": act_ids,
                    "equal_ids": eq_ids,
                    "tick_ids": t_ids,
                    "symbolic": scr["symbolic"],
                    "clause_html": _format_euclid_clause_html(scr["raw_clause"]),
                    "just_id": just_id,
                    "just_title": just_title,
                }
                if scr["is_punchline"]:
                    sub_dict["is_punchline"] = True
                subslides.append(sub_dict)

            scenes.append({
                "id": sec["name"],
                "bell": sec.get("bell") or "bell:proposition",
                "subslides": subslides,
            })
        return scenes

    # Otherwise: ETC Center / Topic / Intro format
    svgs_dir = package_dir / "svgs"
    short_code = meta.get("center") or meta.get("topic") or package_dir.name
    short_title = meta.get("title", "")
    prop_id = (
        f"ETC • {short_code}"
        if short_code.startswith("X_")
        else (meta.get("prop") or f"ETC.{short_code}")
    )
    common = {
        "prop_id": prop_id,
        "short_code": short_code,
        "g_id": short_title,
        "folio": meta.get("folio", "Kimberling ETC • Collection 1 (X_1–X_19)"),
        "enunciation": meta.get("enunciation", ""),
        "step_total": total_steps,
        "visible_ids": ["*"],
    }

    scenes = []
    step_counter = 0
    for sec_idx, sec in enumerate(sections, start=1):
        subslides = []
        for scr in sec["screens"]:
            step_counter += 1
            say_joined = " ".join(scr["say_lines"]).strip()
            clean_words = re.sub(r"<[^>]+>|\[[^\]]+\]", "", say_joined).split()
            auto_w = max(0.35, len(clean_words) / 18.0) if clean_words else 1.0
            weight = scr["weight"] if scr["weight"] is not None else round(auto_w, 2)

            clause_html = _format_etc_clause_html(
                scr["sections"],
                scr["head"],
                scr["rows"],
                scr["text_lines"],
                scr["raw_clause"],
            )
            j_parts = [p.strip() for p in scr["just_raw"].split("|")] if scr["just_raw"] else []
            just_tag = j_parts[0] if len(j_parts) > 0 else ""
            just_id = j_parts[1] if len(j_parts) > 1 else ""
            just_title = j_parts[2] if len(j_parts) > 2 else ""

            svg_name = scr["svg"] or "01_primary.svg"
            svg_full = (svgs_dir / svg_name) if (svgs_dir / svg_name).exists() else (package_dir / svg_name)

            has_highlights = bool(scr["active"] or scr["coincide"] or scr["equal"])
            sub_dict = {
                **common,
                "type": "euclid_step",
                "weight": weight,
                "step_num": step_counter,
                "step_kind": scr["step_kind"],
                "svg_path": str(svg_full),
                "symbolic": scr["symbolic"],
                "clause_html": clause_html,
                "just_tag": just_tag,
                "just_id": just_id,
                "just_title": just_title,
            }
            if scr["is_punchline"]:
                sub_dict["is_punchline"] = True
            if has_highlights:
                sub_dict["strict_step_ids"] = True
                sub_dict["active_ids"] = scr["active"]
                sub_dict["coincide_ids"] = scr["coincide"]
                sub_dict["equal_ids"] = scr["equal"]
            if scr["zoom"]:
                sub_dict["zoom"] = scr["zoom"]
            if scr["thumb_zoom"]:
                sub_dict["thumb_zoom"] = scr["thumb_zoom"]
            subslides.append(sub_dict)

        scenes.append({
            "id": f"act_{sec_idx}_{sec['name']}",
            "bell": sec.get("bell"),
            "title": f"{short_code} {short_title} — {sec['name']}",
            "subslides": subslides,
        })

    return scenes


def load_scenes_or_compile_scr(path: Path) -> list[dict[str, Any]]:
    """Load scenes from a `.scr` / `.txt` script (compiled in memory) or a legacy `.json` file."""
    path = Path(path).resolve()
    if path.suffix.lower() in (".scr", ".txt"):
        return compile_scr_text(path.read_text(encoding="utf-8"), path.parent)
    import json
    return json.loads(path.read_text(encoding="utf-8"))
