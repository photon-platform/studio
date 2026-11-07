#!/usr/bin/env python3
"""GEOMETOR Proposition & Shorts Dual-Format Video Compiler (9:16 Short + 16:9 1080p).

From a single spoken script (<id>_script.txt / .wav / .json) and step specification
(scenes.json), this compiler generates:
  1. 1080x1920 (9:16) Vertical PNG slides (slides_9x16/slide_XX.png) + <prefix>_short.mp4
  2. 1920x1080 (16:9) Widescreen PNG slides (slides_16x9/slide_XX.png) + <prefix>_1080p.mp4
  3. SubRip (.srt) & WebVTT (.vtt) Closed Captions (<prefix>.srt, <prefix>.vtt)
  4. YouTube Publishing Metadata (youtube.txt & youtube.json) with auto-chapters
  5. Interactive Dual-Format Studio Player (<prefix>_stage.html) with 9:16 / 16:9 toggle & CC
"""

import argparse
import base64
import html
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess

CHROME_CANDIDATES = [
    Path("/home/phi/.cache/ms-playwright/chromium-1134/chrome-linux/chrome"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/google-chrome"),
]
MERMAID_JS = Path(
    "/usr/share/antigravity/resources/app/node_modules/mermaid/dist/mermaid.min.js"
)

STEP_KIND_COLORS = {
    "GOAL": ("#e8b85a", "rgba(232, 184, 90, 0.14)"),
    "ENUNCIATION": ("#e8b85a", "rgba(232, 184, 90, 0.14)"),
    "GIVEN": ("#60a5fa", "rgba(96, 165, 250, 0.14)"),
    "CONSTRUCT": ("#e8b85a", "rgba(232, 184, 90, 0.16)"),
    "INTERSECT": ("#38bdf8", "rgba(56, 189, 248, 0.15)"),
    "ASSERT": ("#5ec27e", "rgba(94, 194, 126, 0.16)"),
    "PROVE": ("#5ec27e", "rgba(94, 194, 126, 0.16)"),
    "CONCLUDE": ("#5ec27e", "rgba(94, 194, 126, 0.22)"),
    "∴": ("#5ec27e", "rgba(94, 194, 126, 0.22)"),
    "Q.E.F.": ("#5ec27e", "rgba(94, 194, 126, 0.22)"),
    "Q.E.D.": ("#5ec27e", "rgba(94, 194, 126, 0.22)"),
    "COMMENTARY": ("#a78bfa", "rgba(167, 139, 250, 0.16)"),
    "GENEALOGY": ("#38bdf8", "rgba(56, 189, 248, 0.16)"),
    "EXPLORE": ("#e8b85a", "rgba(232, 184, 90, 0.18)"),
    "PARTICIPATE": ("#e8b85a", "rgba(232, 184, 90, 0.18)"),
}


def find_chrome() -> Path:
  for c in CHROME_CANDIDATES:
    if c.exists():
      return c
  raise FileNotFoundError("Headless Chromium binary not found.")


def clean_caption_text(text: str) -> str:
  text = re.sub(r"</div\s*>|<br\s*/?>", " ", text, flags=re.I)
  text = re.sub(r"<[^>]+>", "", text)
  text = re.sub(r"\[(?:bell|style|sfx):[^\]]+\]", "", text)
  return re.sub(r"\s+", " ", text).strip()


def data_uri_for_file(path: Path) -> str:
  mime, _ = mimetypes.guess_type(str(path))
  if not mime:
    if path.suffix.lower() == ".vtt":
      mime = "text/vtt"
    else:
      mime = "application/octet-stream"
  b64 = base64.b64encode(path.read_bytes()).decode("ascii")
  return f"data:{mime};base64,{b64}"


def chrome_screenshot(
    url_or_path: str,
    out_png: Path,
    width: int = 1080,
    height: int = 1920,
    wait_ms: int = 650,
) -> Path:
  """Captures an exact width x height viewport screenshot via headless Chromium."""
  import shutil
  import uuid

  chrome = find_chrome()
  out_png.parent.mkdir(parents=True, exist_ok=True)
  raw_png = out_png.with_name(f"_raw_{out_png.name}")

  target = (
      url_or_path
      if url_or_path.startswith(("http://", "https://", "file://"))
      else f"file://{Path(url_or_path).resolve()}"
  )
  win_h = height + 100

  last_err = None
  for attempt in range(3):
    uid = uuid.uuid4().hex[:10]
    tmp_root = Path(f"/tmp/geometor_chrome_{uid}")
    tmp_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(tmp_root / "config")
    env["XDG_CACHE_HOME"] = str(tmp_root / "cache")
    env["HOME"] = str(tmp_root / "home")
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)

    cmd = [
        str(chrome),
        "--headless=new",
        f"--user-data-dir={tmp_root / 'profile'}",
        "--disk-cache-size=1",
        "--media-cache-size=1",
        "--aggressive-cache-discard",
        "--disable-application-cache",
        "--allow-file-access-from-files",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        f"--window-size={width},{win_h}",
        f"--virtual-time-budget={wait_ms}",
        f"--screenshot={raw_png}",
    ]
    if target.startswith(("http://", "https://")):
      cmd.append("--incognito")
    cmd.append(target)
    try:
      res = subprocess.run(
          cmd, env=env, capture_output=True, text=True, timeout=45
      )
      if res.returncode == 0 and raw_png.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
        break
      last_err = f"rc={res.returncode}: {res.stderr[:300]}"
    except subprocess.TimeoutExpired as exc:
      last_err = f"TimeoutExpired on attempt {attempt + 1}: {exc}"
    finally:
      shutil.rmtree(tmp_root, ignore_errors=True)
  else:
    raise RuntimeError(f"Chromium screenshot failed: {last_err}")

  subprocess.run(
      [
          "/usr/bin/convert",
          str(raw_png),
          "-crop",
          f"{width}x{height}+0+0",
          "+repage",
          str(out_png),
      ],
      check=True,
  )
  raw_png.unlink(missing_ok=True)
  return out_png


def prepare_euclid_dark_svg(svg_path: Path) -> str:
  """Reads an Euclid or GEOMETOR ETC .svg and enforces high-contrast dark mode + crisp stroke weights."""
  raw = svg_path.read_text(encoding="utf-8")
  raw = re.sub(r"<\?xml[^>]*\?>", "", raw)
  if "geometor-etc-drawing" in raw:
    raw = re.sub(r"<script>//<!\[CDATA\[.*?//\]\]></script>", "", raw, flags=re.DOTALL)
    etc_css = """
    svg.geometor-etc-drawing {
      width: 100% !important;
      height: 100% !important;
      max-height: none !important;
      background-color: transparent !important;
      border: none !important;
      overflow: hidden !important;
    }
    .etc-svg-toolbar,
    #labels text.subtitle-label {
      display: none !important;
    }
    .geometor-etc-drawing #elements line,
    .geometor-etc-drawing #elements path.line,
    .geometor-etc-drawing #elements .bisector {
      fill: none !important;
      stroke: #cbd5e1;
      stroke-width: 2.5px;
      stroke-dasharray: 2, 4.5 !important;
      stroke-linecap: round !important;
      stroke-opacity: 0.85;
      vector-effect: non-scaling-stroke !important;
    }
    .geometor-etc-drawing #elements line.sideline,
    .geometor-etc-drawing #elements path.line.sideline {
      stroke: #cbd5e1 !important;
      stroke-width: 2.4px !important;
      stroke-dasharray: 2, 4.5 !important;
      stroke-linecap: round !important;
      stroke-opacity: 0.85 !important;
    }
    .geometor-etc-drawing #graphics polygon.reference {
      stroke: none !important;
      fill: rgba(54, 102, 204, 0.12) !important;
    }
    .geometor-etc-drawing.has-step-highlights #elements line:not(.geom-active-any):not(.sideline),
    .geometor-etc-drawing.has-step-highlights #elements path.line:not(.geom-active-any):not(.sideline) {
      stroke: #cbd5e1 !important;
      stroke-width: 2.3px !important;
      stroke-dasharray: 2, 4.5 !important;
      stroke-linecap: round !important;
      stroke-opacity: 0.78 !important;
    }
    .geometor-etc-drawing.has-step-highlights #elements circle:not(.geom-active-any) {
      stroke: #cbd5e1 !important;
      stroke-width: 2.4px !important;
      stroke-opacity: 0.78 !important;
      stroke-dasharray: 2, 4.5 !important;
      stroke-linecap: round !important;
    }
    .geometor-etc-drawing.has-step-highlights #graphics polygon.secondary:not(.geom-active-any) {
      stroke: #94a3b8 !important;
      stroke-width: 2.3px !important;
      stroke-dasharray: 2, 4.5 !important;
      stroke-opacity: 0.72 !important;
      fill: rgba(148, 163, 184, 0.06) !important;
    }
    .geometor-etc-drawing.has-step-highlights #elements line.incident:not(.geom-active-any),
    .geometor-etc-drawing.has-step-highlights #elements path.line.incident:not(.geom-active-any) {
      stroke-opacity: 0.25 !important;
    }
    .geometor-etc-drawing.has-step-highlights #points circle.incident:not(.geom-active-any),
    .geometor-etc-drawing.has-step-highlights #points circle.guide:not(.geom-active-any) {
      opacity: 0.28 !important;
    }
    .geometor-etc-drawing.has-step-highlights #labels text.incident:not(.geom-active-any),
    .geometor-etc-drawing.has-step-highlights #labels text.guide-label:not(.geom-active-any) {
      display: none !important;
    }
    .geometor-etc-drawing #elements .geom-active-any,
    .geometor-etc-drawing #graphics polyline.geom-active-any {
      stroke-width: 3.6px !important;
      stroke-dasharray: none !important;
      stroke-opacity: 1.0 !important;
      opacity: 1.0 !important;
      filter: none !important;
    }
    .geometor-etc-drawing #graphics polygon.geom-active-any {
      stroke-width: 3.2px !important;
      stroke-dasharray: none !important;
      stroke-opacity: 1.0 !important;
      fill-opacity: 1.0 !important;
    }
    .geometor-etc-drawing #points circle.geom-active-any {
      stroke: #0b0e15 !important;
      stroke-width: 2.4px !important;
    }
    .geometor-etc-drawing #labels text.geom-active-any,
    .geometor-etc-drawing #labels text.geom-active-any * {
      stroke: #0b0e15 !important;
      stroke-width: 0.16em !important;
    }
    /* Legacy + 9 Central Palette Color Classes */
    .geometor-etc-drawing #elements .geom-highlight-active,
    .geometor-etc-drawing #graphics polyline.geom-highlight-active,
    .geometor-etc-drawing #elements .geom-color-gold,
    .geometor-etc-drawing #graphics polyline.geom-color-gold { stroke: #e8b85a !important; }
    .geometor-etc-drawing #graphics polygon.geom-highlight-active,
    .geometor-etc-drawing #graphics polygon.geom-color-gold { stroke: #e8b85a !important; fill: rgba(232, 184, 90, 0.16) !important; }
    .geometor-etc-drawing #points circle.geom-highlight-active,
    .geometor-etc-drawing #points circle.geom-color-gold,
    .geometor-etc-drawing #labels text.geom-highlight-active,
    .geometor-etc-drawing #labels text.geom-highlight-active *,
    .geometor-etc-drawing #labels text.geom-color-gold,
    .geometor-etc-drawing #labels text.geom-color-gold * { fill: #e8b85a !important; }

    .geometor-etc-drawing #elements .geom-color-orange,
    .geometor-etc-drawing #graphics polyline.geom-color-orange { stroke: #fb923c !important; }
    .geometor-etc-drawing #graphics polygon.geom-color-orange { stroke: #fb923c !important; fill: rgba(251, 146, 60, 0.16) !important; }
    .geometor-etc-drawing #points circle.geom-color-orange,
    .geometor-etc-drawing #labels text.geom-color-orange,
    .geometor-etc-drawing #labels text.geom-color-orange * { fill: #fb923c !important; }

    .geometor-etc-drawing #elements .geom-color-teal,
    .geometor-etc-drawing #graphics polyline.geom-color-teal { stroke: #2dd4bf !important; }
    .geometor-etc-drawing #graphics polygon.geom-color-teal { stroke: #2dd4bf !important; fill: rgba(45, 212, 191, 0.16) !important; }
    .geometor-etc-drawing #points circle.geom-color-teal,
    .geometor-etc-drawing #labels text.geom-color-teal,
    .geometor-etc-drawing #labels text.geom-color-teal * { fill: #2dd4bf !important; }

    .geometor-etc-drawing #elements .geom-coincide-active,
    .geometor-etc-drawing #graphics polyline.geom-coincide-active,
    .geometor-etc-drawing #elements .geom-color-blue,
    .geometor-etc-drawing #graphics polyline.geom-color-blue { stroke: #38bdf8 !important; }
    .geometor-etc-drawing #graphics polygon.geom-coincide-active,
    .geometor-etc-drawing #graphics polygon.geom-color-blue { stroke: #38bdf8 !important; fill: rgba(56, 189, 248, 0.16) !important; }
    .geometor-etc-drawing #points circle.geom-coincide-active,
    .geometor-etc-drawing #points circle.geom-color-blue,
    .geometor-etc-drawing #labels text.geom-coincide-active,
    .geometor-etc-drawing #labels text.geom-coincide-active *,
    .geometor-etc-drawing #labels text.geom-color-blue,
    .geometor-etc-drawing #labels text.geom-color-blue * { fill: #38bdf8 !important; }

    .geometor-etc-drawing #elements .geom-color-purple,
    .geometor-etc-drawing #graphics polyline.geom-color-purple { stroke: #a855f7 !important; }
    .geometor-etc-drawing #graphics polygon.geom-color-purple { stroke: #a855f7 !important; fill: rgba(168, 85, 247, 0.16) !important; }
    .geometor-etc-drawing #points circle.geom-color-purple,
    .geometor-etc-drawing #labels text.geom-color-purple,
    .geometor-etc-drawing #labels text.geom-color-purple * { fill: #a855f7 !important; }

    .geometor-etc-drawing #elements .geom-color-orchid,
    .geometor-etc-drawing #graphics polyline.geom-color-orchid { stroke: #e879f9 !important; }
    .geometor-etc-drawing #graphics polygon.geom-color-orchid { stroke: #e879f9 !important; fill: rgba(232, 121, 249, 0.16) !important; }
    .geometor-etc-drawing #points circle.geom-color-orchid,
    .geometor-etc-drawing #labels text.geom-color-orchid,
    .geometor-etc-drawing #labels text.geom-color-orchid * { fill: #e879f9 !important; }

    .geometor-etc-drawing #elements .geom-color-pink,
    .geometor-etc-drawing #graphics polyline.geom-color-pink { stroke: #f472b6 !important; }
    .geometor-etc-drawing #graphics polygon.geom-color-pink { stroke: #f472b6 !important; fill: rgba(244, 114, 182, 0.16) !important; }
    .geometor-etc-drawing #points circle.geom-color-pink,
    .geometor-etc-drawing #labels text.geom-color-pink,
    .geometor-etc-drawing #labels text.geom-color-pink * { fill: #f472b6 !important; }

    .geometor-etc-drawing #elements .geom-assert-active,
    .geometor-etc-drawing #graphics polyline.geom-assert-active,
    .geometor-etc-drawing #elements .geom-color-green,
    .geometor-etc-drawing #graphics polyline.geom-color-green { stroke: #5ec27e !important; }
    .geometor-etc-drawing #graphics polygon.geom-assert-active,
    .geometor-etc-drawing #graphics polygon.geom-color-green { stroke: #5ec27e !important; fill: rgba(94, 194, 126, 0.16) !important; }
    .geometor-etc-drawing #points circle.geom-assert-active,
    .geometor-etc-drawing #points circle.geom-color-green,
    .geometor-etc-drawing #labels text.geom-assert-active,
    .geometor-etc-drawing #labels text.geom-assert-active *,
    .geometor-etc-drawing #labels text.geom-color-green,
    .geometor-etc-drawing #labels text.geom-color-green * { fill: #5ec27e !important; }

    .geometor-etc-drawing #elements .geom-color-lime,
    .geometor-etc-drawing #graphics polyline.geom-color-lime { stroke: #a3e635 !important; }
    .geometor-etc-drawing #graphics polygon.geom-color-lime { stroke: #a3e635 !important; fill: rgba(163, 230, 53, 0.16) !important; }
    .geometor-etc-drawing #points circle.geom-color-lime,
    .geometor-etc-drawing #labels text.geom-color-lime,
    .geometor-etc-drawing #labels text.geom-color-lime * { fill: #a3e635 !important; }

    .geom-step-hidden {
      opacity: 0 !important;
      display: none !important;
    }
    .geom-step-ghost {
      opacity: 0.11 !important;
    }
    """
    return raw.replace("</style>", etc_css + "\n  </style>", 1)

  raw = re.sub(
      r":root\s*\{[^}]*\}",
      """:root {
      --geom-fg: #d6cfc5;
      --geom-bg: transparent;
      --geom-point-stroke: #111622;
      --geom-poly-fill: #d6cfc5;
      --geom-accent: #e8b85a;
      --geom-assert: #5ec27e;
    }""",
      raw,
      count=1,
  )
  extra_css = """
    .euclid-graphic {
      width: 100% !important;
      height: 100% !important;
      background-color: transparent !important;
    }
    .geom-line, .geom-circle, .geom-curve {
      stroke-width: 2.1 !important;
    }
    .geom-highlight-active {
      stroke-width: 3.0 !important;
      filter: none !important;
    }
    circle.geom-circle.geom-highlight-active,
    circle.geom-overlay.geom-highlight-active {
      fill: none !important;
      stroke: var(--geom-accent) !important;
      stroke-width: 3.0 !important;
      filter: none !important;
    }
    .geom-assert-active {
      stroke-width: 3.0 !important;
      filter: none !important;
    }
    circle.geom-circle.geom-assert-active,
    circle.geom-overlay.geom-assert-active {
      fill: none !important;
      stroke: var(--geom-assert) !important;
      stroke-width: 3.0 !important;
      filter: none !important;
    }
    circle.geom-point,
    circle.geom-point.geom-highlight-active,
    circle.geom-point.geom-assert-active,
    circle.geom-dot,
    circle.geom-dot.geom-highlight-active,
    circle.geom-dot.geom-assert-active,
    #points circle.geom-point,
    #points circle.geom-dot,
    #points circle.geom-highlight-active,
    #points circle.geom-assert-active {
      stroke: var(--geom-point-stroke) !important;
      stroke-width: 2.2px !important;
      paint-order: stroke fill !important;
      filter: none !important;
    }
    text,
    text *,
    .geom-label,
    .geom-label-roman,
    .geom-label-sm,
    .geom-label-sub,
    text.geom-highlight-active,
    text.geom-highlight-secondary,
    text.geom-highlight-active *,
    text.geom-highlight-secondary *,
    text.geom-assert-active,
    text.geom-assert-secondary,
    text.geom-assert-active *,
    text.geom-assert-secondary * {
      paint-order: stroke fill !important;
      stroke: var(--geom-point-stroke) !important;
      stroke-width: 3.6px !important;
      stroke-linejoin: round !important;
      stroke-linecap: round !important;
      filter: none !important;
    }
    .geom-tick {
      display: none !important;
    }
    .geom-tick-mark {
      stroke: #5ec27e !important;
      stroke-width: 2.6 !important;
    }
    .geom-coincide-active {
      stroke: #38bdf8 !important;
      stroke-width: 3.0 !important;
      stroke-dasharray: 5 3.5 !important;
      filter: none !important;
    }
    circle.geom-point.geom-coincide-active,
    #points circle.geom-coincide-active {
      fill: #38bdf8 !important;
      stroke: var(--geom-point-stroke) !important;
      stroke-width: 2.2px !important;
      stroke-dasharray: none !important;
    }
    text.geom-coincide-active,
    text.geom-coincide-active * {
      fill: #38bdf8 !important;
      stroke: var(--geom-point-stroke) !important;
      stroke-width: 3.6px !important;
      stroke-dasharray: none !important;
    }
    .geom-step-hidden {
      opacity: 0 !important;
      display: none !important;
    }
    .geom-step-ghost {
      opacity: 0.11 !important;
    }
  """
  raw = raw.replace("</style>", extra_css + "\n  </style>", 1)
  return raw


def _svg_step_script(scene: dict) -> str:
  state_json = json.dumps({
      "step_kind": str(scene.get("step_kind", "")).upper(),
      "visible_ids": scene.get("visible_ids", ["*"]),
      "hidden_ids": scene.get("hidden_ids", []),
      "active_ids": scene.get("active_ids", []),
      "equal_ids": scene.get("equal_ids", []),
      "coincide_ids": scene.get("coincide_ids", []),
      "coincide_pairs": scene.get("coincide_pairs", []),
      "tick_ids": scene.get("tick_ids", []),
      "tick_2_ids": scene.get("tick_2_ids", []),
      "tick_3_ids": scene.get("tick_3_ids", []),
      "ghost_future": bool(scene.get("ghost_future", False)),
      "strict_step_ids": bool(scene.get("strict_step_ids", False)),
      "zoom": scene.get("zoom", []),
      "thumb_zoom": scene.get("thumb_zoom", []),
      "is_thumbnail": bool(scene.get("_is_thumbnail", False)),
  })
  return f"""
  <script>
    (function() {{
      const STATE = {state_json};
      const svg = document.querySelector('#svg-holder svg');
      if (!svg) return;
      const isEtcDrawing = svg.classList.contains('geometor-etc-drawing');
      const svgNs = 'http://www.w3.org/2000/svg';

      if (!isEtcDrawing) {{
        // Ensure labels placed directly below a point have enough vertical clearance for knockout strokes
        Array.from(svg.querySelectorAll('#labels text')).forEach((txtEl) => {{
          const id = txtEl.getAttribute('id') || '';
          if (!id.startsWith('lbl-')) return;
          const ptEl = svg.querySelector('#el-pt-' + CSS.escape(id.slice(4)));
          if (!ptEl) return;
          const tx = parseFloat(txtEl.getAttribute('x') || '0');
          const ty = parseFloat(txtEl.getAttribute('y') || '0');
          const cx = parseFloat(ptEl.getAttribute('cx') || '0');
          const cy = parseFloat(ptEl.getAttribute('cy') || '0');
          const dy = ty - cy;
          if (Math.abs(tx - cx) < 10 && dy > 0 && dy < 17) {{
            txtEl.setAttribute('y', String(ty + 3.5));
          }}
        }});

        // Ensure any 2-point <line> or <path> in #graphics/#elements can host a midpoint tick marker
        let defs = svg.querySelector('defs');
        if (!defs) {{
          defs = document.createElementNS(svgNs, 'defs');
          svg.insertBefore(defs, svg.firstChild);
        }}
        if (!defs.querySelector('#geom-tick-1')) {{
          const markerSpecs = [
            {{ id: 'geom-tick-1', xs: [0] }},
            {{ id: 'geom-tick-2', xs: [-1.8, 1.8] }},
            {{ id: 'geom-tick-3', xs: [-3.2, 0, 3.2] }},
          ];
          markerSpecs.forEach((spec) => {{
            const m = document.createElementNS(svgNs, 'marker');
            m.setAttribute('id', spec.id);
            m.setAttribute('viewBox', '-6 -6 12 12');
            m.setAttribute('refX', '0');
            m.setAttribute('refY', '0');
            m.setAttribute('markerWidth', '12');
            m.setAttribute('markerHeight', '12');
            m.setAttribute('markerUnits', 'userSpaceOnUse');
            m.setAttribute('orient', 'auto');
            spec.xs.forEach((xVal) => {{
              const ln = document.createElementNS(svgNs, 'line');
              ln.setAttribute('x1', String(xVal));
              ln.setAttribute('y1', '-4.5');
              ln.setAttribute('x2', String(xVal));
              ln.setAttribute('y2', '4.5');
              ln.setAttribute('class', 'geom-tick-mark');
              m.appendChild(ln);
            }});
            defs.appendChild(m);
          }});
        }}
        // Convert <line> elements to 3-point <path> so marker-mid renders at midpoint
        Array.from(svg.querySelectorAll('line')).forEach((lineEl) => {{
          if (lineEl.closest('defs')) return;
          const x1 = parseFloat(lineEl.getAttribute('x1') || '0');
          const y1 = parseFloat(lineEl.getAttribute('y1') || '0');
          const x2 = parseFloat(lineEl.getAttribute('x2') || '0');
          const y2 = parseFloat(lineEl.getAttribute('y2') || '0');
          const mx = (x1 + x2) / 2;
          const my = (y1 + y2) / 2;
          const p = document.createElementNS(svgNs, 'path');
          Array.from(lineEl.attributes).forEach((attr) => {{
            if (!['x1', 'y1', 'x2', 'y2'].includes(attr.name)) {{
              p.setAttribute(attr.name, attr.value);
            }}
          }});
          p.setAttribute('d', `M ${{x1}} ${{y1}} L ${{mx}} ${{my}} L ${{x2}} ${{y2}}`);
          lineEl.parentNode.replaceChild(p, lineEl);
        }});
      }}

      const visSet = new Set(STATE.visible_ids || []);
      const showAll = visSet.has('*');

      if (!showAll) {{
        const controllable = svg.querySelectorAll('#elements > *, #graphics > *, #points > *, #labels > *');
        controllable.forEach((el) => {{
          const id = el.getAttribute('id') || '';
          if (id && !visSet.has(id)) {{
            el.classList.add(STATE.ghost_future ? 'geom-step-ghost' : 'geom-step-hidden');
          }}
        }});
      }}

      // Unconditionally hide any suppressed IDs (e.g. unused circumference letters D, E, H, K, F)
      (STATE.hidden_ids || []).forEach((id) => {{
        const el = svg.querySelector('#' + CSS.escape(id));
        if (el) {{
          el.classList.add('geom-step-hidden');
        }}
      }});

      const ptsGroup = svg.querySelector('#points') || svg;
      let anyHighlighted = false;

      const vbParts = (svg.getAttribute('viewBox') || '0 0 800 600').trim().split(/[\\s,]+/).map(parseFloat);
      const vbWidth = (vbParts.length >= 4 && vbParts[2] > 0) ? vbParts[2] : 800;
      const colTol = vbWidth * 0.0003;

      const ALL_COLOR_CLASSES = [
        'geom-highlight-active', 'geom-coincide-active', 'geom-assert-active',
        'geom-color-gold', 'geom-color-orange', 'geom-color-teal', 'geom-color-blue',
        'geom-color-purple', 'geom-color-orchid', 'geom-color-pink', 'geom-color-green', 'geom-color-lime',
      ];

      const isAnyActive = (el) => {{
        if (!el) return false;
        return el.classList.contains('geom-active-any') ||
               ALL_COLOR_CLASSES.some((c) => el.classList.contains(c));
      }};

      const markElement = (el, cls, tickCls) => {{
        if (!el) return;
        const effCls = (isEtcDrawing && el.classList.contains('target')) ? 'geom-color-gold' : cls;
        el.classList.remove('geom-step-hidden', 'geom-step-ghost');
        el.classList.remove(...ALL_COLOR_CLASSES);
        el.classList.add('geom-active-any', effCls);
        if (tickCls) el.classList.add(tickCls);
        anyHighlighted = true;
        const elId = el.getAttribute('id') || el.getAttribute('data-id') || '';
        if (elId) {{
          const hlEl = svg.querySelector('#hl-' + CSS.escape(elId)) ||
                       svg.querySelector('[data-highlight-for="' + CSS.escape(elId) + '"]');
          if (hlEl) {{
            hlEl.classList.remove('geom-step-hidden', 'geom-step-ghost');
            hlEl.classList.remove(...ALL_COLOR_CLASSES);
            hlEl.classList.add('geom-active-any', effCls);
          }}
          const lblEl = svg.querySelector('#lbl-' + CSS.escape(elId)) ||
                        svg.querySelector('[data-point-id="' + CSS.escape(elId) + '"]');
          if (lblEl) {{
            lblEl.classList.remove('geom-step-hidden', 'geom-step-ghost');
            lblEl.classList.remove(...ALL_COLOR_CLASSES);
            lblEl.classList.add('geom-active-any', effCls);
          }}
        }}
        if (elId.startsWith('el-seg-')) {{
          const hlSeg = svg.querySelector('#hl-seg-' + CSS.escape(elId.slice(7)));
          if (hlSeg) {{
            hlSeg.classList.remove('geom-step-hidden', 'geom-step-ghost');
            hlSeg.classList.add('geom-active-any', effCls);
          }}
        }}
      }};

      const highlightLineThroughCenters = (nums, cls, tickCls) => {{
        const ptSet = new Set(nums.map((n) => 'X_' + n));
        const coords = [];
        nums.forEach((n) => {{
          const ptId = 'X_' + n;
          const ptEl = svg.querySelector('#' + CSS.escape(ptId)) ||
                       svg.querySelector('[data-id="' + CSS.escape(ptId) + '"]');
          if (ptEl) {{
            markElement(ptEl, cls, null);
            coords.push({{
              x: parseFloat(ptEl.getAttribute('cx') || '0'),
              y: parseFloat(ptEl.getAttribute('cy') || '0'),
            }});
          }}
        }});
        let matchedByPtAttrs = false;
        Array.from(svg.querySelectorAll('#elements line, #elements path.line')).forEach((lnEl) => {{
          const p1 = lnEl.getAttribute('data-pt1') || '';
          const p2 = lnEl.getAttribute('data-pt2') || '';
          if (p1 && p2 && ptSet.has(p1) && ptSet.has(p2)) {{
            markElement(lnEl, cls, tickCls);
            matchedByPtAttrs = true;
          }}
        }});
        if (!matchedByPtAttrs && coords.length >= 2) {{
          Array.from(svg.querySelectorAll('#elements line, #elements path.line')).forEach((lnEl) => {{
            let x1 = 0, y1 = 0, x2 = 0, y2 = 0;
            if (lnEl.hasAttribute('x1')) {{
              x1 = parseFloat(lnEl.getAttribute('x1') || '0');
              y1 = parseFloat(lnEl.getAttribute('y1') || '0');
              x2 = parseFloat(lnEl.getAttribute('x2') || '0');
              y2 = parseFloat(lnEl.getAttribute('y2') || '0');
            }} else {{
              const dAttr = lnEl.getAttribute('d') || '';
              const m = dAttr.match(/M\\s*([-\\d.]+)\\s+([-\\d.]+).*L\\s*([-\\d.]+)\\s+([-\\d.]+)$/);
              if (!m) return;
              x1 = parseFloat(m[1]); y1 = parseFloat(m[2]);
              x2 = parseFloat(m[3]); y2 = parseFloat(m[4]);
            }}
            const len = Math.hypot(x2 - x1, y2 - y1);
            if (len < 1e-6) return;
            let hits = 0;
            coords.forEach((pt) => {{
              const dist = Math.abs((y2 - y1) * pt.x - (x2 - x1) * pt.y + x2 * y1 - y2 * x1) / len;
              if (dist < colTol) hits += 1;
            }});
            if (hits >= 2) {{
              markElement(lnEl, cls, tickCls);
            }}
          }});
        }}
      }};

      const applyTokenHighlight = (rawToken, defaultCls, tickCls) => {{
        let token = String(rawToken || '').trim().replace(/^[△Δ]\\s*/, '');
        let explicitColor = '';
        const colMatch = token.match(/^(.*):(gold|orange|teal|blue|purple|orchid|pink|green|lime|muted)$/i);
        if (colMatch) {{
          token = colMatch[1].trim();
          explicitColor = colMatch[2].toLowerCase();
        }}
        if (!token || token === 'ABC' || explicitColor === 'muted') return;

        const resolveCls = (el) => {{
          if (explicitColor) return 'geom-color-' + explicitColor;
          if (el) {{
            const dc = el.getAttribute('data-color-token');
            if (dc) return 'geom-color-' + dc;
          }}
          return defaultCls;
        }};

        // 1. Direct #id or [data-id] match
        const directEl = svg.querySelector('#' + CSS.escape(token)) ||
                         svg.querySelector('[data-id="' + CSS.escape(token) + '"]');
        if (directEl) {{
          const chosenCls = resolveCls(directEl);
          markElement(directEl, chosenCls, tickCls);
          if ((directEl.tagName || '').toLowerCase() === 'line') {{
            ['data-pt1', 'data-pt2'].forEach((attr) => {{
              const pid = directEl.getAttribute(attr) || '';
              if (pid && !['A', 'B', 'C'].includes(pid)) {{
                const pEl = svg.querySelector('#' + CSS.escape(pid));
                if (pEl) markElement(pEl, chosenCls, null);
              }}
            }});
          }}
          return;
        }}

        // 2. Brace set of centers like "{1, 2, 8, 10}" or "{ 1, 3, 5 }"
        if (token.startsWith('{{') && token.endsWith('}}')) {{
          const nums = (token.match(/\\d+/g) || []).map((n) => parseInt(n, 10));
          const chosenCls = explicitColor ? ('geom-color-' + explicitColor) : defaultCls;
          highlightLineThroughCenters(nums, chosenCls, tickCls);
          return;
        }}

        // 3. L(i,j) or L(i,j): Name format
        const lm = token.match(/^L\\(\\s*(\\d+)\\s*,\\s*(\\d+)\\s*\\)(?:\\s*:\\s*(.+))?$/i);
        if (lm) {{
          const nums = [parseInt(lm[1], 10), parseInt(lm[2], 10)];
          const chosenCls = explicitColor ? ('geom-color-' + explicitColor) : defaultCls;
          highlightLineThroughCenters(nums, chosenCls, tickCls);
          if (lm[3]) {{
            const subName = lm[3].trim().toLowerCase();
            Array.from(svg.querySelectorAll('[data-name]')).forEach((namedEl) => {{
              const dName = (namedEl.getAttribute('data-name') || '').toLowerCase();
              if (dName && (dName.includes(subName) || subName.includes(dName))) {{
                markElement(namedEl, resolveCls(namedEl), tickCls);
              }}
            }});
          }}
          return;
        }}

        // 4. Center prefix like "X_2 (Centroid)" or "X_75"
        const xm = token.match(/^(X_(\\d+))\\b/);
        if (xm) {{
          const ptId = xm[1];
          const ptEl = svg.querySelector('#' + CSS.escape(ptId)) ||
                       svg.querySelector('[data-id="' + CSS.escape(ptId) + '"]');
          if (ptEl) {{
            markElement(ptEl, resolveCls(ptEl), tickCls);
          }}
          return;
        }}

        // 5. 2-point cevian/line token like "AI_a", "AM_a", "A–Tan_a", "[A I_a]", "J_a M_a"
        const cleanBr = token.replace(/^\\[|\\s*\\]$/g, '').trim();
        const pairMatch = cleanBr.match(/^([A-Z])(?:\\s+|[-–]\\s*)?([A-Za-z0-9'_]+)$/) ||
                          cleanBr.match(/^([A-Za-z0-9'_]+)\\s*(?:[-–]|\\s+)\\s*([A-Za-z0-9'_]+)$/);
        if (pairMatch) {{
          const p1 = pairMatch[1];
          const p2 = pairMatch[2];
          const cand1 = `[ ${{p1}} ${{p2}} ]`;
          const cand2 = `[ ${{p2}} ${{p1}} ]`;
          const lnEl = svg.querySelector('[data-id="' + CSS.escape(cand1) + '"]') ||
                       svg.querySelector('[data-id="' + CSS.escape(cand2) + '"]') ||
                       svg.querySelector(`[data-pt1="${{CSS.escape(p1)}}"][data-pt2="${{CSS.escape(p2)}}"]`) ||
                       svg.querySelector(`[data-pt1="${{CSS.escape(p2)}}"][data-pt2="${{CSS.escape(p1)}}"]`);
          if (lnEl) {{
            const chosenCls = resolveCls(lnEl);
            markElement(lnEl, chosenCls, tickCls);
            const pt2El = svg.querySelector('#' + CSS.escape(p2));
            if (pt2El) markElement(pt2El, chosenCls, null);
            return;
          }}
        }}

        // 6. 3-point triangle or point-list token like "T_a T_b T_c" or "M_a, M_b, M_c"
        const parts = token.replace(/^<|\\s*>$/g, '').split(/[\\s,]+/).filter(Boolean);
        if (parts.length === 3) {{
          let matchedAnyPt = false;
          const chosenCls = explicitColor ? ('geom-color-' + explicitColor) : defaultCls;
          parts.forEach((p) => {{
            const ptEl = svg.querySelector('#' + CSS.escape(p)) ||
                         svg.querySelector('[data-id="' + CSS.escape(p) + '"]');
            if (ptEl) {{
              markElement(ptEl, chosenCls, null);
              matchedAnyPt = true;
            }}
          }});
          const polyId = `< ${{parts[0]}} ${{parts[1]}} ${{parts[2]}} >`;
          const polyEl = svg.querySelector('[data-id="' + CSS.escape(polyId) + '"]') ||
                         svg.querySelector('#graphics polygon.secondary');
          if (polyEl && matchedAnyPt) {{
            markElement(polyEl, chosenCls, null);
            return;
          }}
          if (matchedAnyPt) return;
        }}

        // 7. Named element match via [data-name]
        if (token.length >= 3) {{
          const lowerTok = token.toLowerCase();
          Array.from(svg.querySelectorAll('[data-name]')).forEach((namedEl) => {{
            const dName = (namedEl.getAttribute('data-name') || '').toLowerCase();
            if (dName && (dName === lowerTok || dName.includes(lowerTok) || lowerTok.includes(dName))) {{
              markElement(namedEl, resolveCls(namedEl), tickCls);
            }}
          }});
        }}
      }};

      // Auto-sync colored tokens in .step-clause for .geometor-etc-drawing SVGs
      if (svg.classList.contains('geometor-etc-drawing') && !STATE.strict_step_ids) {{
        const tokPairs = [
          ['.g-tok, .tok-gold', 'geom-color-gold'],
          ['.tok-orange', 'geom-color-orange'],
          ['.tok-teal', 'geom-color-teal'],
          ['.coincide-tok, .tok-blue', 'geom-color-blue'],
          ['.tok-purple', 'geom-color-purple'],
          ['.tok-orchid', 'geom-color-orchid'],
          ['.tok-pink', 'geom-color-pink'],
          ['.eq-tok, .tok-green', 'geom-color-green'],
          ['.tok-lime', 'geom-color-lime'],
        ];
        tokPairs.forEach(([sel, cCls]) => {{
          Array.from(document.querySelectorAll('.step-clause ' + sel)).forEach((sp) => {{
            applyTokenHighlight(sp.textContent, cCls, null);
          }});
        }});
      }}

      (STATE.active_ids || []).forEach((id) => applyTokenHighlight(id, 'geom-color-gold', null));
      (STATE.coincide_ids || []).forEach((id) => applyTokenHighlight(id, 'geom-color-blue', null));

      // Draw curved dotted correspondence arcs between coinciding points (e.g. [["el-pt-A", "el-pt-D"]])
      (STATE.coincide_pairs || []).forEach((pair) => {{
        if (!Array.isArray(pair) || pair.length < 2) return;
        const p1 = svg.querySelector('#' + CSS.escape(pair[0]));
        const p2 = svg.querySelector('#' + CSS.escape(pair[1]));
        if (!p1 || !p2) return;
        const x1 = parseFloat(p1.getAttribute('cx') || '0');
        const y1 = parseFloat(p1.getAttribute('cy') || '0');
        const x2 = parseFloat(p2.getAttribute('cx') || '0');
        const y2 = parseFloat(p2.getAttribute('cy') || '0');
        const mx = (x1 + x2) / 2;
        const my = (y1 + y2) / 2 - 22;
        const arc = document.createElementNS(svgNs, 'path');
        arc.setAttribute('d', `M ${{x1}} ${{y1}} Q ${{mx}} ${{my}} ${{x2}} ${{y2}}`);
        arc.setAttribute('fill', 'none');
        arc.setAttribute('stroke', '#38bdf8');
        arc.setAttribute('stroke-width', '2.0');
        arc.setAttribute('stroke-dasharray', '4 4');
        arc.setAttribute('opacity', '0.88');
        ptsGroup.insertBefore(arc, ptsGroup.firstChild);
      }});

      (STATE.equal_ids || []).forEach((id) => applyTokenHighlight(id, 'geom-color-green', null));
      (STATE.tick_ids || []).forEach((id) => applyTokenHighlight(id, 'geom-color-green', 'geom-tick-1'));
      (STATE.tick_2_ids || []).forEach((id) => applyTokenHighlight(id, 'geom-color-green', 'geom-tick-2'));
      (STATE.tick_3_ids || []).forEach((id) => applyTokenHighlight(id, 'geom-color-green', 'geom-tick-3'));

      if (anyHighlighted && isEtcDrawing) {{
        svg.classList.add('has-step-highlights');
      }}

      // Dynamic viewBox zoom and screen-space element/label scaling for .geometor-etc-drawing SVGs
      if (isEtcDrawing) {{
        const rawZoom = STATE.is_thumbnail ? (STATE.thumb_zoom || []) : (STATE.zoom || []);
        const zoomPtSet = new Set();
        let hasCircleInZoom = false;
        if (Array.isArray(rawZoom) && rawZoom.length > 0 && rawZoom[0] !== 'all' && rawZoom[0] !== '*') {{
          const bboxPts = [];
          const addPt = (x, y) => {{
            if (Number.isFinite(x) && Number.isFinite(y)) bboxPts.push({{ x, y }});
          }};
          const addElementBounds = (el) => {{
            if (!el) return;
            const tag = (el.tagName || '').toLowerCase();
            if (tag === 'circle') {{
              const cx = parseFloat(el.getAttribute('cx') || '0');
              const cy = parseFloat(el.getAttribute('cy') || '0');
              if (el.closest('#points') !== null) {{
                addPt(cx, cy);
                const pid = el.getAttribute('id') || el.getAttribute('data-id') || '';
                if (pid) zoomPtSet.add(pid);
              }} else {{
                const r = parseFloat(el.getAttribute('r') || '0');
                addPt(cx - r, cy - r);
                addPt(cx + r, cy + r);
                hasCircleInZoom = true;
              }}
            }} else if (tag === 'polygon' || tag === 'polyline') {{
              const ptsStr = (el.getAttribute('points') || '').trim();
              ptsStr.split(/\\s+/).forEach((pair) => {{
                const parts = pair.split(',').map(parseFloat);
                if (parts.length >= 2) addPt(parts[0], parts[1]);
              }});
            }}
          }};

          const tokensToResolve = (rawZoom.length === 1 && rawZoom[0] === 'active')
            ? [...(STATE.active_ids || []), ...(STATE.coincide_ids || []), ...(STATE.equal_ids || [])]
            : rawZoom;

          tokensToResolve.forEach((rawTok) => {{
            const tok = String(rawTok || '').trim().replace(/:(gold|orange|teal|blue|purple|orchid|pink|green|lime|muted)$/i, '').trim();
            if (!tok) return;
            const direct = svg.querySelector('#' + CSS.escape(tok)) ||
                           svg.querySelector('[data-id="' + CSS.escape(tok) + '"]');
            if (direct) {{
              addElementBounds(direct);
              return;
            }}
            if (tok.startsWith('{{') && tok.endsWith('}}')) {{
              (tok.match(/\\d+/g) || []).forEach((n) => {{
                const pEl = svg.querySelector('#X_' + CSS.escape(n)) ||
                            svg.querySelector('[data-id="X_' + CSS.escape(n) + '"]');
                if (pEl) addElementBounds(pEl);
              }});
              return;
            }}
            tok.replace(/^<|\\s*>$/g, '').split(/[\\s,]+/).filter(Boolean).forEach((p) => {{
              const pEl = svg.querySelector('#' + CSS.escape(p)) ||
                          svg.querySelector('[data-id="' + CSS.escape(p) + '"]');
              if (pEl) addElementBounds(pEl);
            }});
          }});

          if (bboxPts.length > 0) {{
            const xs = bboxPts.map((p) => p.x);
            const ys = bboxPts.map((p) => p.y);
            const minX = Math.min(...xs);
            const maxX = Math.max(...xs);
            const minY = Math.min(...ys);
            const maxY = Math.max(...ys);
            const spanX = maxX - minX;
            const spanY = maxY - minY;
            const holder = document.getElementById('svg-holder') || svg.parentElement;
            const hRect = (holder && holder.getBoundingClientRect().width > 0)
              ? holder.getBoundingClientRect()
              : svg.getBoundingClientRect();
            const aspect = (hRect.width > 0 && hRect.height > 0) ? (hRect.width / hRect.height) : (4.0 / 3.0);
            const padFactor = hasCircleInZoom ? 1.24 : 1.48;
            let vbW = Math.max(spanX * padFactor, 0.21);
            let vbH = Math.max(spanY * padFactor, 0.16);
            if (vbW / vbH < aspect) {{
              vbW = vbH * aspect;
            }} else {{
              vbH = vbW / aspect;
            }}
            const cx = 0.5 * (minX + maxX);
            const cy = 0.5 * (minY + maxY);
            const newVb = [cx - 0.5 * vbW, cy - 0.5 * vbH, vbW, vbH];
            svg.setAttribute('viewBox', newVb.map((v) => v.toFixed(6)).join(' '));
          }}
        }}

        // Rescale point radii and de-overlap visible labels in screen pixels
        const holder = document.getElementById('svg-holder') || svg.parentElement;
        const hRect = (holder && holder.getBoundingClientRect().width > 0)
          ? holder.getBoundingClientRect()
          : svg.getBoundingClientRect();
        const widthPx = (hRect.width || 960) * 0.96;
        const heightPx = (hRect.height || 720) * 0.96;
        const curVb = (svg.getAttribute('viewBox') || '0 0 1 1').trim().split(/[\\s,]+/).map(parseFloat);
        const unitsPerPx = Math.max(curVb[2] / widthPx, curVb[3] / heightPx);
        const targetRPx = parseFloat(svg.getAttribute('data-target-r-px') || '10.5');

        const vcx = curVb[0] + 0.5 * curVb[2];
        const vcy = curVb[1] + 0.5 * curVb[3];
        const clipHalfLen = Math.hypot(curVb[2], curVb[3]) * 1.5;
        const visibleLinesPx = [];
        Array.from(svg.querySelectorAll('#elements line')).forEach((lnEl) => {{
          let x1 = parseFloat(lnEl.getAttribute('x1') || '0');
          let y1 = parseFloat(lnEl.getAttribute('y1') || '0');
          let x2 = parseFloat(lnEl.getAttribute('x2') || '0');
          let y2 = parseFloat(lnEl.getAttribute('y2') || '0');
          const len = Math.hypot(x2 - x1, y2 - y1);
          if (len > clipHalfLen * 3.0) {{
            const ux = (x2 - x1) / len;
            const uy = (y2 - y1) / len;
            const tProj = (vcx - x1) * ux + (vcy - y1) * uy;
            const px = x1 + tProj * ux;
            const py = y1 + tProj * uy;
            x1 = px - clipHalfLen * ux;
            y1 = py - clipHalfLen * uy;
            x2 = px + clipHalfLen * ux;
            y2 = py + clipHalfLen * uy;
            lnEl.setAttribute('x1', x1.toFixed(6));
            lnEl.setAttribute('y1', y1.toFixed(6));
            lnEl.setAttribute('x2', x2.toFixed(6));
            lnEl.setAttribute('y2', y2.toFixed(6));
          }}
          if (isAnyActive(lnEl)) {{
            const lx1 = (x1 - curVb[0]) / unitsPerPx;
            const ly1 = (y1 - curVb[1]) / unitsPerPx;
            const lx2 = (x2 - curVb[0]) / unitsPerPx;
            const ly2 = (y2 - curVb[1]) / unitsPerPx;
            const llen = Math.hypot(lx2 - lx1, ly2 - ly1);
            if (llen > 1.0) {{
              visibleLinesPx.push({{ x1: lx1, y1: ly1, x2: lx2, y2: ly2, len: llen }});
            }}
          }}
        }});

        const pointEls = Array.from(svg.querySelectorAll('#points circle'));
        const visiblePtsPx = [];
        pointEls.forEach((c) => {{
          const pid = c.getAttribute('id') || c.getAttribute('data-id') || '';
          const isTarget = c.classList.contains('target') || c.classList.contains('selected');
          const isHl = isAnyActive(c);
          const isZoomPt = zoomPtSet.has(pid);
          if (isZoomPt && !isHl && !isTarget) {{
            c.style.setProperty('opacity', '0.92', 'important');
          }}
          const isGuide = c.classList.contains('guide') && !isHl && !isZoomPt;
          const rPx = isTarget ? targetRPx : (isHl ? 8.5 : (isGuide ? 5.0 : 7.0));
          c.setAttribute('r', (rPx * unitsPerPx).toFixed(6));
          const cx = parseFloat(c.getAttribute('cx') || '0');
          const cy = parseFloat(c.getAttribute('cy') || '0');
          if (cx >= curVb[0] - curVb[2] * 0.05 && cx <= curVb[0] + curVb[2] * 1.05 &&
              cy >= curVb[1] - curVb[3] * 0.05 && cy <= curVb[1] + curVb[3] * 1.05) {{
            visiblePtsPx.push({{
              id: pid,
              x: (cx - curVb[0]) / unitsPerPx,
              y: (cy - curVb[1]) / unitsPerPx,
              r: rPx,
            }});
          }}
        }});

        const labelEls = Array.from(svg.querySelectorAll('#labels text[data-point-id]'));
        labelEls.forEach((lbl) => {{
          const pid = lbl.getAttribute('data-point-id') || '';
          const isHl = isAnyActive(lbl);
          if (isHl || zoomPtSet.has(pid)) {{
            lbl.classList.remove('dense-label');
            lbl.style.setProperty('display', 'block', 'important');
          }}
        }});

        // Sort visible labels so target and highlighted labels get placed first
        const activeLabels = labelEls.filter((lbl) => {{
          const disp = window.getComputedStyle(lbl).display;
          if (disp === 'none') return false;
          const cx = parseFloat(lbl.getAttribute('data-anchor-x') || '0');
          const cy = parseFloat(lbl.getAttribute('data-anchor-y') || '0');
          return (cx >= curVb[0] && cx <= curVb[0] + curVb[2] &&
                  cy >= curVb[1] && cy <= curVb[1] + curVb[3]);
        }});

        activeLabels.sort((a, b) => {{
          const prio = (el) => {{
            if (el.classList.contains('target-label')) return 0;
            if (isAnyActive(el)) return 1;
            return 2;
          }};
          return prio(a) - prio(b);
        }});

        const placedBoxes = [];
        const boxesOverlap = (a, b) => !(a.x1 < b.x0 || a.x0 > b.x1 || a.y1 < b.y0 || a.y0 > b.y1);

        activeLabels.forEach((lbl) => {{
          const cx = parseFloat(lbl.getAttribute('data-anchor-x') || '0');
          const cy = parseFloat(lbl.getAttribute('data-anchor-y') || '0');
          const origOx = parseFloat(lbl.getAttribute('data-offset-x') || '12');
          const origOy = parseFloat(lbl.getAttribute('data-offset-y') || '-10.5');
          const isGuideLbl = lbl.classList.contains('guide-label') && !isAnyActive(lbl);
          const fontSizePx = isGuideLbl ? 18.5 : 22.0;
          lbl.setAttribute('font-size', (fontSizePx * unitsPerPx).toFixed(6));

          const ptPxX = (cx - curVb[0]) / unitsPerPx;
          const ptPxY = (cy - curVb[1]) / unitsPerPx;
          const txtLen = Math.max(2, (lbl.textContent || '').trim().length);
          const lblW = Math.max(26, txtLen * 12.2);
          const lblH = 22.0;

          const candidates = [
            [origOx, origOy, -6],
            [14, -10, 0],
            [-14 - lblW, -10, 0],
            [14, 22, 0],
            [-14 - lblW, 22, 0],
            [-0.5 * lblW, -18, 2],
            [-0.5 * lblW, 28, 2],
            [20, 6, 3],
            [-20 - lblW, 6, 3],
            [24, -20, 4],
            [-24 - lblW, -20, 4],
            [24, 28, 4],
            [-24 - lblW, 28, 4],
          ];

          let bestOx = origOx;
          let bestOy = origOy;
          let bestScore = 1e9;
          let bestBox = null;

          candidates.forEach(([ox, oy, basePen]) => {{
            const box = {{
              x0: ptPxX + ox - 3,
              y0: ptPxY + oy - lblH + 3,
              x1: ptPxX + ox + lblW + 3,
              y1: ptPxY + oy + 5,
            }};
            let score = basePen;
            if (box.x0 < 10 || box.y0 < 10 || box.x1 > widthPx - 10 || box.y1 > heightPx - 10) {{
              score += 500;
            }}
            visiblePtsPx.forEach((pt) => {{
              const pBox = {{
                x0: pt.x - pt.r - 4,
                y0: pt.y - pt.r - 4,
                x1: pt.x + pt.r + 4,
                y1: pt.y + pt.r + 4,
              }};
              if (boxesOverlap(box, pBox)) score += 5000;
            }});
            placedBoxes.forEach((pBox) => {{
              if (boxesOverlap(box, pBox)) score += 4000;
            }});
            const bcx = 0.5 * (box.x0 + box.x1);
            const bcy = 0.5 * (box.y0 + box.y1);
            visibleLinesPx.forEach((ln) => {{
              const dist = Math.abs((ln.y2 - ln.y1) * bcx - (ln.x2 - ln.x1) * bcy + ln.x2 * ln.y1 - ln.y2 * ln.x1) / ln.len;
              if (dist < 13) score += 260;
            }});
            if (score < bestScore) {{
              bestScore = score;
              bestOx = ox;
              bestOy = oy;
              bestBox = box;
            }}
          }});

          if (bestBox) placedBoxes.push(bestBox);
          lbl.setAttribute('x', (cx + bestOx * unitsPerPx).toFixed(6));
          lbl.setAttribute('y', (cy + bestOy * unitsPerPx).toFixed(6));
        }});
      }}
    }})();
  </script>
  """


def _format_etc_display_name(raw_g_id: str) -> str:
  name = raw_g_id.title() if raw_g_id.isupper() else raw_g_id
  name = re.sub(r"\bX_(\d+)\b", r"X_\1", name, flags=re.I)
  name = re.sub(r"\b1St\b", "1st", name)
  name = re.sub(r"\b2Nd\b", "2nd", name)
  name = re.sub(r"\b3Rd\b", "3rd", name)
  name = re.sub(r"\bOi\b", "OI", name)
  return name


def build_euclid_step_html_9x16(
    scene: dict,
    slide_idx: int,
    total_slides: int,
    work_dir: Path,
) -> Path:
  """Builds a 1080x1920 (9:16) vertical HTML document without bottom footer and with control margin."""
  prop_id = scene.get("prop_id", "BOOK I • PROP. 1")
  is_etc = str(prop_id).upper().startswith("ETC")
  short_code = scene.get("short_code") or prop_id.split("•")[-1].strip()
  g_id = scene.get("g_id", "G.5")
  etc_name = _format_etc_display_name(g_id)
  folio = scene.get("folio", "Folio 0255 • p. 241")
  folio_display = (
      folio
      if any(k in folio.lower() for k in ("heath", "kimberling", "etc", "geometor"))
      else f"Heath (1908) • {folio}"
  )
  enunciation = scene.get(
      "enunciation",
      "On a given finite straight line to construct an equilateral triangle.",
  )
  step_num = int(scene.get("step_num", slide_idx + 1))
  step_total = min(int(scene.get("step_total", total_slides)), total_slides)
  step_kind = scene.get("step_kind", "CONSTRUCT").upper()
  if step_kind in ("Q.E.F.", "Q.E.D.", "CONCLUDE", "∴ CONCLUDE"):
    step_kind = "∴"
  kind_fg, kind_bg = STEP_KIND_COLORS.get(
      step_kind, ("#e8b85a", "rgba(232, 184, 90, 0.15)")
  )

  svg_path = Path(scene["svg_path"])
  svg_markup = prepare_euclid_dark_svg(svg_path)

  symbolic = scene.get("symbolic", "")
  clause_html = scene.get("clause_html", "")
  just_tag = scene.get("just_tag", "∵ PREREQUISITE")
  just_id = re.sub(r"\s*\(Q\.E\.[DF]\.\)", "", scene.get("just_id", "")).strip()
  just_title = scene.get("just_title", "")
  footer_note = scene.get("footer_note", "")

  inset_html = ""
  if scene.get("inset_img"):
    inset_p = Path(scene["inset_img"])
    if inset_p.exists():
      inset_uri = data_uri_for_file(inset_p)
      inset_label = html.escape(scene.get("inset_label", "1908 Folio"))
      inset_html = f"""
      <div class="folio-inset">
        <img src="{inset_uri}" />
        <span>{inset_label}</span>
      </div>
      """

  p_bars = []
  for i in range(1, step_total + 1):
    cls = (
        "p-seg done"
        if i < step_num
        else ("p-seg current" if i == step_num else "p-seg future")
    )
    p_bars.append(f'<div class="{cls}"></div>')
  progress_html = "\n".join(p_bars)

  if just_id:
    just_card_html = f"""
    <div class="just-card">
      <div class="just-header">
        <span class="just-tag">{html.escape(just_tag)}</span>
        <span class="just-pill">[{html.escape(just_id)}]</span>
      </div>
      <div class="just-desc">{html.escape(just_title)}</div>
    </div>
    """
  elif footer_note:
    just_card_html = f"""
    <div class="just-card">
      <div class="just-desc">{footer_note}</div>
    </div>
    """
  else:
    just_card_html = ""

  if is_etc:
    header_top_9x16 = f"""
      <div class="badge-row">
        <div class="etc-identity-banner">
          <span class="etc-id-code">{html.escape(short_code)}</span>
          <span class="etc-id-sep">•</span>
          <span class="etc-id-name">{html.escape(etc_name)}</span>
        </div>
        <span class="heath-pill">{html.escape(folio_display)}</span>
      </div>
    """
    stage_hud_9x16 = ""
    stage_pad_9x16 = "20px"
    svg_max_h_9x16 = "712px"
  else:
    header_top_9x16 = f"""
      <div class="badge-row">
        <div class="badge-group">
          <span class="brand-pill">{html.escape(prop_id)}</span>
          <span class="heath-pill">{html.escape(folio_display)}</span>
        </div>
        <span class="step-counter-pill">STEP {step_num:02d} / {step_total:02d}</span>
      </div>
    """
    stage_hud_9x16 = f"""
      <div class="stage-hud-top">
        <span class="g-pill">G-INDEX • {html.escape(g_id)}</span>
        {f'<span class="hud-symbolic">{html.escape(symbolic)}</span>' if symbolic else ''}
      </div>
    """
    stage_pad_9x16 = "64px 28px 24px 28px"
    svg_max_h_9x16 = "640px"

  html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 1080px;
    height: 1920px;
    overflow: hidden;
    background: radial-gradient(circle at 50% 42%, #151b28 0%, #0b0e15 70%, #07090e 100%);
    color: #f4f1ea;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .stage-grid {{
    position: absolute;
    inset: 0;
    background-image:
      linear-gradient(rgba(232, 184, 90, 0.035) 1px, transparent 1px),
      linear-gradient(90deg, rgba(232, 184, 90, 0.035) 1px, transparent 1px);
    background-size: 54px 54px;
    pointer-events: none;
  }}
  /* Top-aligned stack: fixed 4:3 graphics panel with text beginning immediately below,
     leaving ~500px at the bottom clear of YouTube Shorts mobile UI overlays */
  .short-frame {{
    position: relative;
    width: 1080px;
    height: 1920px;
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
    padding: 44px 48px 48px 48px;
    gap: 22px;
  }}
  .short-header {{
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 16px;
    background: rgba(16, 20, 29, 0.9);
    border: 1px solid rgba(232, 184, 90, 0.32);
    border-radius: 16px;
    padding: 24px 28px 20px 28px;
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.5);
  }}
  .badge-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }}
  .badge-group {{
    display: flex;
    align-items: center;
    gap: 12px;
  }}
  .etc-identity-banner {{
    display: flex;
    align-items: baseline;
    gap: 12px;
    min-width: 0;
  }}
  .etc-id-code {{
    font-family: Georgia, "Times New Roman", serif;
    font-size: 38px;
    font-weight: 700;
    color: #e8b85a;
    line-height: 1.05;
    white-space: nowrap;
  }}
  .etc-id-sep {{
    font-size: 30px;
    color: rgba(232, 184, 90, 0.65);
  }}
  .etc-id-name {{
    font-family: Georgia, "Times New Roman", serif;
    font-size: 36px;
    font-weight: 700;
    color: #f8f6f0;
    line-height: 1.05;
  }}
  .brand-pill {{
    font-family: "JetBrains Mono", monospace;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.14em;
    color: #e8b85a;
    background: rgba(232, 184, 90, 0.12);
    border: 1px solid rgba(232, 184, 90, 0.45);
    padding: 7px 16px;
    border-radius: 6px;
  }}
  .g-pill {{
    font-family: "JetBrains Mono", monospace;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: #38bdf8;
    background: rgba(56, 189, 248, 0.12);
    border: 1px solid rgba(56, 189, 248, 0.4);
    padding: 7px 14px;
    border-radius: 6px;
    white-space: nowrap;
    flex-shrink: 0;
  }}
  .heath-pill {{
    font-family: "JetBrains Mono", monospace;
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: #cbd5e1;
    background: rgba(148, 163, 184, 0.12);
    border: 1px solid rgba(148, 163, 184, 0.35);
    padding: 7px 12px;
    border-radius: 6px;
    white-space: nowrap;
  }}
  .step-counter-pill {{
    font-family: "JetBrains Mono", monospace;
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: #cbd5e1;
    background: rgba(30, 41, 59, 0.85);
    border: 1px solid rgba(148, 163, 184, 0.3);
    padding: 7px 16px;
    border-radius: 999px;
  }}
  .enunciation-text {{
    font-family: Georgia, "Times New Roman", serif;
    font-size: 31px;
    line-height: 1.32;
    color: #f8f6f0;
    font-style: italic;
  }}
  .progress-track {{
    display: flex;
    gap: 8px;
    width: 100%;
    padding-top: 4px;
  }}
  .p-seg {{
    flex: 1;
    height: 7px;
    border-radius: 999px;
    background: rgba(148, 163, 184, 0.2);
  }}
  .p-seg.done {{ background: rgba(232, 184, 90, 0.65); }}
  .p-seg.current {{
    background: {kind_fg};
    box-shadow: 0 0 12px {kind_fg};
  }}

  .geom-stage-card {{
    position: relative;
    flex: 0 0 760px;
    height: 760px;
    background: radial-gradient(circle at 50% 50%, #141926 0%, #0d111a 100%);
    border: 1.5px solid rgba(232, 184, 90, 0.36);
    border-radius: 20px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: {stage_pad_9x16};
    box-shadow: inset 0 0 60px rgba(0, 0, 0, 0.65), 0 22px 55px rgba(0, 0, 0, 0.65);
    overflow: hidden;
  }}
  .stage-hud-top {{
    position: absolute;
    top: 18px;
    left: 24px;
    right: 24px;
    display: flex;
    align-items: center;
    justify-content: flex-start;
    gap: 12px;
    z-index: 3;
    pointer-events: none;
  }}
  .hud-folio {{
    font-family: "JetBrains Mono", monospace;
    font-size: 15px;
    color: #94a3b8;
    background: rgba(11, 14, 21, 0.85);
    border: 1px solid rgba(148, 163, 184, 0.25);
    padding: 6px 14px;
    border-radius: 6px;
  }}
  .hud-symbolic {{
    font-family: "JetBrains Mono", monospace;
    font-size: 18px;
    font-weight: 700;
    color: {kind_fg};
    background: rgba(11, 14, 21, 0.92);
    border: 1px solid {kind_fg};
    padding: 7px 16px;
    border-radius: 8px;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5);
  }}
  .svg-canvas-holder {{
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .svg-canvas-holder svg {{
    width: 97% !important;
    height: 97% !important;
    max-height: {svg_max_h_9x16} !important;
  }}
  .folio-inset {{
    position: absolute;
    bottom: 20px;
    right: 22px;
    background: rgba(13, 17, 26, 0.94);
    border: 1.5px solid rgba(232, 184, 90, 0.45);
    border-radius: 12px;
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    box-shadow: 0 12px 30px rgba(0, 0, 0, 0.7);
  }}
  .folio-inset img {{
    height: 135px;
    width: auto;
    border-radius: 6px;
  }}
  .folio-inset span {{
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    color: #cbd5e1;
  }}

  /* Expanded text block with no bottom footer bar */
  .step-panel {{
    background: rgba(16, 20, 29, 0.95);
    border: 1.5px solid rgba(232, 184, 90, 0.36);
    border-left: 8px solid {kind_fg};
    border-radius: 18px;
    padding: 30px 34px;
    display: flex;
    flex-direction: column;
    gap: 20px;
    box-shadow: 0 18px 45px rgba(0, 0, 0, 0.65);
  }}
  .step-panel-top {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }}
  .step-kind-badge {{
    font-family: "JetBrains Mono", monospace;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.14em;
    color: {kind_fg};
    background: {kind_bg};
    border: 1.5px solid {kind_fg};
    padding: 7px 18px;
    border-radius: 8px;
  }}
  .step-clause {{
    font-family: Georgia, "Times New Roman", serif;
    font-size: 38px;
    line-height: 1.38;
    color: #f8f6f0;
  }}
  .clause-line {{
    display: block;
  }}
  .clause-line + .clause-line {{
    margin-top: 10px;
  }}
  .g-tok, .tok-gold, .eq-tok, .tok-green, .coincide-tok, .tok-blue,
  .tok-orange, .tok-teal, .tok-purple, .tok-orchid, .tok-pink, .tok-lime {{
    display: inline-block;
    font-family: "JetBrains Mono", monospace;
    font-size: 32px;
    font-weight: 700;
    padding: 1px 12px;
    border-radius: 8px;
    margin: 0 2px;
  }}
  .g-tok, .tok-gold {{
    color: #e8b85a;
    background: rgba(232, 184, 90, 0.15);
    border: 1.5px solid rgba(232, 184, 90, 0.58);
  }}
  .tok-orange {{
    color: #fb923c;
    background: rgba(251, 146, 60, 0.15);
    border: 1.5px solid rgba(251, 146, 60, 0.62);
  }}
  .tok-teal {{
    color: #2dd4bf;
    background: rgba(45, 212, 191, 0.15);
    border: 1.5px solid rgba(45, 212, 191, 0.62);
  }}
  .coincide-tok, .tok-blue {{
    color: #38bdf8;
    background: rgba(56, 189, 248, 0.15);
    border: 1.5px solid rgba(56, 189, 248, 0.65);
  }}
  .tok-purple {{
    color: #a855f7;
    background: rgba(168, 85, 247, 0.15);
    border: 1.5px solid rgba(168, 85, 247, 0.62);
  }}
  .tok-orchid {{
    color: #e879f9;
    background: rgba(232, 121, 249, 0.15);
    border: 1.5px solid rgba(232, 121, 249, 0.62);
  }}
  .tok-pink {{
    color: #f472b6;
    background: rgba(244, 114, 182, 0.15);
    border: 1.5px solid rgba(244, 114, 182, 0.62);
  }}
  .eq-tok, .tok-green {{
    color: #5ec27e;
    background: rgba(94, 194, 126, 0.15);
    border: 1.5px solid rgba(94, 194, 126, 0.62);
  }}
  .tok-lime {{
    color: #a3e635;
    background: rgba(163, 230, 53, 0.15);
    border: 1.5px solid rgba(163, 230, 53, 0.62);
  }}
  .muted-tok, .tok-muted {{
    display: inline-block;
    font-family: "JetBrains Mono", monospace;
    font-size: 25px;
    font-weight: 600;
    color: #94a3b8;
    background: rgba(148, 163, 184, 0.10);
    border: 1px solid rgba(148, 163, 184, 0.32);
    padding: 3px 13px;
    border-radius: 8px;
  }}
  .etc-table-block {{
    display: flex;
    flex-direction: column;
    gap: 14px;
    width: 100%;
  }}
  .etc-section {{
    display: flex;
    flex-direction: column;
    gap: 9px;
    background: rgba(11, 14, 21, 0.58);
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 12px;
    padding: 12px 16px;
  }}
  .etc-sec-head {{
    font-family: "JetBrains Mono", monospace;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #94a3b8;
  }}
  .etc-sec-head-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 16px;
  }}
  .etc-head-cols, .etc-row-cells {{
    display: grid;
    grid-template-columns: repeat(3, 72px);
    gap: 8px;
    align-items: center;
  }}
  .etc-head-cols > span {{
    font-family: Georgia, "Times New Roman", serif;
    font-style: italic;
    font-size: 20px;
    font-weight: 700;
    color: #94a3b8;
    text-align: center;
  }}
  .etc-row-cells > span {{
    display: flex;
    align-items: center;
    justify-content: center;
    width: 72px;
    font-size: 24px;
    padding: 3px 0 !important;
    margin: 0 !important;
    text-align: center;
  }}
  .etc-coord-grid {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    align-items: center;
  }}
  .etc-coord-grid.stack {{
    grid-template-columns: 1fr;
    gap: 7px;
  }}
  .etc-coord-grid > span {{
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 25px;
    padding: 5px 10px;
    margin: 0;
    width: 100%;
    text-align: center;
  }}
  .etc-coord-grid.stack > span {{
    justify-content: flex-start;
    font-size: 24px;
    padding: 4px 12px;
  }}
  .etc-data-list {{
    display: flex;
    flex-direction: column;
    gap: 9px;
    width: 100%;
  }}
  .etc-data-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    background: rgba(11, 14, 21, 0.58);
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 10px;
    padding: 9px 15px;
  }}
  .etc-data-row.muted-row {{
    opacity: 0.52;
  }}
  .etc-row-label {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 25px;
    font-weight: 600;
    color: #e2e8f0;
  }}
  .etc-data-row > span:not(.etc-row-label) {{
    font-size: 25px;
    padding: 3px 13px;
    margin: 0;
    white-space: nowrap;
  }}
  .just-card {{
    background: rgba(11, 14, 21, 0.88);
    border: 1px solid rgba(148, 163, 184, 0.3);
    border-radius: 12px;
    padding: 16px 22px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }}
  .just-header {{
    display: flex;
    align-items: center;
    gap: 12px;
  }}
  .just-tag {{
    font-family: "JetBrains Mono", monospace;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.12em;
    color: #94a3b8;
  }}
  .just-pill {{
    font-family: "JetBrains Mono", monospace;
    font-size: 18px;
    font-weight: 700;
    color: #e8b85a;
    background: rgba(232, 184, 90, 0.12);
    border: 1px solid rgba(232, 184, 90, 0.45);
    padding: 3px 12px;
    border-radius: 6px;
  }}
  .just-desc {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 23px;
    line-height: 1.38;
    color: #cbd5e1;
  }}
</style>
</head>
<body>
  <div class="stage-grid"></div>
  <div class="short-frame">
    <header class="short-header">
      {header_top_9x16}
      <div class="enunciation-text">“{html.escape(enunciation)}”</div>
      <div class="progress-track">
        {progress_html}
      </div>
    </header>

    <main class="geom-stage-card">
      {stage_hud_9x16}
      <div class="svg-canvas-holder" id="svg-holder">
        {svg_markup}
      </div>
      {inset_html}
    </main>

    <section class="step-panel">
      <div class="step-panel-top">
        <span class="step-kind-badge">STEP {step_num:02d} • {html.escape(step_kind)}</span>
      </div>
      <div class="step-clause">{clause_html}</div>
      {just_card_html}
    </section>
  </div>
  {_svg_step_script(scene)}
</body>
</html>
"""
  html_path = work_dir / f"slide_9x16_{slide_idx:02d}.html"
  html_path.write_text(html_doc, encoding="utf-8")
  return html_path


def build_euclid_step_html_16x9(
    scene: dict,
    slide_idx: int,
    total_slides: int,
    work_dir: Path,
) -> Path:
  """Builds a 1920x1080 (16:9) widescreen HTML document for the exact same Euclid proposition step."""
  prop_id = scene.get("prop_id", "BOOK I • PROP. 1")
  is_etc = str(prop_id).upper().startswith("ETC")
  short_code = scene.get("short_code") or prop_id.split("•")[-1].strip()
  g_id = scene.get("g_id", "G.5")
  etc_name = _format_etc_display_name(g_id)
  folio = scene.get("folio", "Folio 0255 • p. 241")
  folio_display = (
      folio
      if any(k in folio.lower() for k in ("heath", "kimberling", "etc", "geometor"))
      else f"Heath (1908) • {folio}"
  )
  enunciation = scene.get(
      "enunciation",
      "On a given finite straight line to construct an equilateral triangle.",
  )
  step_num = int(scene.get("step_num", slide_idx + 1))
  step_total = int(scene.get("step_total", total_slides))
  step_kind = scene.get("step_kind", "CONSTRUCT").upper()
  if step_kind in ("Q.E.F.", "Q.E.D.", "CONCLUDE", "∴ CONCLUDE"):
    step_kind = "∴"
  kind_fg, kind_bg = STEP_KIND_COLORS.get(
      step_kind, ("#e8b85a", "rgba(232, 184, 90, 0.15)")
  )

  svg_path = Path(scene["svg_path"]) if scene.get("svg_path") else None
  if scene.get("stage_html"):
    stage_inner_markup = scene["stage_html"]
  elif svg_path and svg_path.exists():
    stage_inner_markup = prepare_euclid_dark_svg(svg_path)
  else:
    stage_inner_markup = ""

  symbolic = scene.get("symbolic", "")
  clause_html = scene.get("clause_html", "")
  just_tag = scene.get("just_tag", "∵ PREREQUISITE")
  just_id = re.sub(r"\s*\(Q\.E\.[DF]\.\)", "", scene.get("just_id", "")).strip()
  just_title = scene.get("just_title", "")
  footer_note = scene.get("footer_note", "")

  inset_html = ""
  if scene.get("inset_img"):
    inset_p = Path(scene["inset_img"])
    if inset_p.exists():
      inset_uri = data_uri_for_file(inset_p)
      inset_label = html.escape(scene.get("inset_label", "1908 Folio"))
      inset_html = f"""
      <div class="folio-inset">
        <img src="{inset_uri}" />
        <span>{inset_label}</span>
      </div>
      """

  p_bars = []
  for i in range(1, step_total + 1):
    cls = (
        "p-seg done"
        if i < step_num
        else ("p-seg current" if i == step_num else "p-seg future")
    )
    p_bars.append(f'<div class="{cls}"></div>')
  progress_html = "\n".join(p_bars)

  if just_id:
    just_card_html = f"""
    <div class="just-card">
      <div class="just-header">
        <span class="just-tag">{html.escape(just_tag)}</span>
        <span class="just-pill">[{html.escape(just_id)}]</span>
      </div>
      <div class="just-desc">{html.escape(just_title)}</div>
    </div>
    """
  elif footer_note:
    just_card_html = f"""
    <div class="just-card">
      <div class="just-desc">{footer_note}</div>
    </div>
    """
  else:
    just_card_html = ""

  if is_etc:
    header_top_16x9 = f"""
      <div class="header-top">
        <div class="header-left">
          <div class="etc-identity-banner">
            <span class="etc-id-code">{html.escape(short_code)}</span>
            <span class="etc-id-sep">•</span>
            <span class="etc-id-name">{html.escape(etc_name)}</span>
          </div>
        </div>
        <span class="heath-pill">{html.escape(folio_display)}</span>
      </div>
    """
    stage_hud_16x9 = ""
    holder_pad_16x9 = "0"
    svg_max_h_16x9 = "745px"
  else:
    header_top_16x9 = f"""
      <div class="header-top">
        <div class="header-left">
          <span class="brand-pill">{html.escape(prop_id)}</span>
          <span class="heath-pill">{html.escape(folio_display)}</span>
        </div>
        <span class="step-counter-pill">STEP {step_num:02d} / {step_total:02d}</span>
      </div>
    """
    stage_hud_16x9 = f"""
      <div class="stage-hud-top">
        <span class="g-pill">G-INDEX • {html.escape(g_id)}</span>
        {f'<span class="hud-symbolic">{html.escape(symbolic)}</span>' if symbolic else ''}
      </div>
    """
    holder_pad_16x9 = "20px"
    svg_max_h_16x9 = "700px"

  html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 1920px;
    height: 1080px;
    overflow: hidden;
    background: radial-gradient(circle at 35% 48%, #151b28 0%, #0b0e15 70%, #07090e 100%);
    color: #f4f1ea;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .stage-grid {{
    position: absolute;
    inset: 0;
    background-image:
      linear-gradient(rgba(232, 184, 90, 0.035) 1px, transparent 1px),
      linear-gradient(90deg, rgba(232, 184, 90, 0.035) 1px, transparent 1px);
    background-size: 56px 56px;
    pointer-events: none;
  }}
  /* Generous 64px bottom padding so 16:9 player scrub bar never covers the content */
  .wide-frame {{
    position: relative;
    width: 1920px;
    height: 1080px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 36px 52px 64px 52px;
    gap: 24px;
  }}
  .wide-header {{
    display: flex;
    flex-direction: column;
    gap: 12px;
    background: rgba(16, 20, 29, 0.9);
    border: 1px solid rgba(232, 184, 90, 0.32);
    border-radius: 16px;
    padding: 18px 28px 16px 28px;
    box-shadow: 0 14px 35px rgba(0, 0, 0, 0.5);
  }}
  .header-top {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
  }}
  .header-left {{
    display: flex;
    align-items: center;
    gap: 16px;
    min-width: 0;
    flex: 1;
  }}
  .etc-identity-banner {{
    display: flex;
    align-items: baseline;
    gap: 14px;
    min-width: 0;
  }}
  .etc-id-code {{
    font-family: Georgia, "Times New Roman", serif;
    font-size: 36px;
    font-weight: 700;
    color: #e8b85a;
    line-height: 1.05;
    white-space: nowrap;
  }}
  .etc-id-sep {{
    font-size: 28px;
    color: rgba(232, 184, 90, 0.65);
  }}
  .etc-id-name {{
    font-family: Georgia, "Times New Roman", serif;
    font-size: 34px;
    font-weight: 700;
    color: #f8f6f0;
    line-height: 1.05;
    letter-spacing: 0.01em;
  }}
  .brand-pill {{
    font-family: "JetBrains Mono", monospace;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 0.14em;
    color: #e8b85a;
    background: rgba(232, 184, 90, 0.12);
    border: 1px solid rgba(232, 184, 90, 0.45);
    padding: 6px 14px;
    border-radius: 6px;
    white-space: nowrap;
  }}
  .g-pill {{
    font-family: "JetBrains Mono", monospace;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: #38bdf8;
    background: rgba(56, 189, 248, 0.12);
    border: 1px solid rgba(56, 189, 248, 0.4);
    padding: 6px 14px;
    border-radius: 6px;
    white-space: nowrap;
  }}
  .heath-pill {{
    font-family: "JetBrains Mono", monospace;
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: #cbd5e1;
    background: rgba(148, 163, 184, 0.12);
    border: 1px solid rgba(148, 163, 184, 0.35);
    padding: 6px 12px;
    border-radius: 6px;
    white-space: nowrap;
  }}
  .enunciation-text {{
    font-family: Georgia, "Times New Roman", serif;
    font-size: 23px;
    line-height: 1.32;
    color: #f8f6f0;
    font-style: italic;
  }}
  .step-counter-pill {{
    font-family: "JetBrains Mono", monospace;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: #cbd5e1;
    background: rgba(30, 41, 59, 0.85);
    border: 1px solid rgba(148, 163, 184, 0.3);
    padding: 6px 16px;
    border-radius: 999px;
    white-space: nowrap;
  }}
  .progress-track {{
    display: flex;
    gap: 8px;
    width: 100%;
  }}
  .p-seg {{
    flex: 1;
    height: 6px;
    border-radius: 999px;
    background: rgba(148, 163, 184, 0.2);
  }}
  .p-seg.done {{ background: rgba(232, 184, 90, 0.65); }}
  .p-seg.current {{
    background: {kind_fg};
    box-shadow: 0 0 12px {kind_fg};
  }}

  /* 2-Column Main Stage */
  .wide-main {{
    flex: 1;
    display: grid;
    grid-template-columns: 1060px 1fr;
    gap: 28px;
    min-height: 0;
  }}
  .geom-stage-card {{
    position: relative;
    height: 100%;
    background: radial-gradient(circle at 50% 50%, #141926 0%, #0d111a 100%);
    border: 1.5px solid rgba(232, 184, 90, 0.36);
    border-radius: 20px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 20px;
    box-shadow: inset 0 0 60px rgba(0, 0, 0, 0.65), 0 22px 55px rgba(0, 0, 0, 0.65);
    overflow: hidden;
  }}
  .stage-hud-top {{
    position: absolute;
    top: 20px;
    left: 24px;
    right: 24px;
    display: flex;
    align-items: center;
    justify-content: flex-start;
    gap: 12px;
    z-index: 3;
    pointer-events: none;
  }}
  .hud-folio {{
    font-family: "JetBrains Mono", monospace;
    font-size: 14px;
    color: #94a3b8;
    background: rgba(11, 14, 21, 0.85);
    border: 1px solid rgba(148, 163, 184, 0.25);
    padding: 6px 14px;
    border-radius: 6px;
  }}
  .hud-symbolic {{
    font-family: "JetBrains Mono", monospace;
    font-size: 17px;
    font-weight: 700;
    color: {kind_fg};
    background: rgba(11, 14, 21, 0.92);
    border: 1px solid {kind_fg};
    padding: 6px 16px;
    border-radius: 8px;
  }}
  .svg-canvas-holder {{
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    padding-top: {holder_pad_16x9};
  }}
  .svg-canvas-holder svg {{
    width: 97% !important;
    height: 96% !important;
    max-height: {svg_max_h_16x9} !important;
  }}
  .folio-inset {{
    position: absolute;
    bottom: 20px;
    right: 22px;
    background: rgba(13, 17, 26, 0.94);
    border: 1.5px solid rgba(232, 184, 90, 0.45);
    border-radius: 12px;
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    box-shadow: 0 12px 30px rgba(0, 0, 0, 0.7);
  }}
  .folio-inset img {{
    height: 125px;
    width: auto;
    border-radius: 6px;
  }}
  .folio-inset span {{
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    color: #cbd5e1;
  }}

  /* Right Column Step & Proof Panel */
  .step-panel {{
    height: 100%;
    background: rgba(16, 20, 29, 0.95);
    border: 1.5px solid rgba(232, 184, 90, 0.36);
    border-left: 8px solid {kind_fg};
    border-radius: 20px;
    padding: 36px 38px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 24px;
    box-shadow: 0 18px 45px rgba(0, 0, 0, 0.65);
  }}
  .step-panel-body {{
    display: flex;
    flex-direction: column;
    gap: 26px;
  }}
  .step-panel-top {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }}
  .step-kind-badge {{
    font-family: "JetBrains Mono", monospace;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.14em;
    color: {kind_fg};
    background: {kind_bg};
    border: 1.5px solid {kind_fg};
    padding: 8px 20px;
    border-radius: 8px;
  }}
  .step-clause {{
    font-family: Georgia, "Times New Roman", serif;
    font-size: 38px;
    line-height: 1.42;
    color: #f8f6f0;
  }}
  .clause-line {{
    display: block;
  }}
  .clause-line + .clause-line {{
    margin-top: 12px;
  }}
  .g-tok, .tok-gold, .eq-tok, .tok-green, .coincide-tok, .tok-blue,
  .tok-orange, .tok-teal, .tok-purple, .tok-orchid, .tok-pink, .tok-lime {{
    display: inline-block;
    font-family: "JetBrains Mono", monospace;
    font-size: 32px;
    font-weight: 700;
    padding: 1px 12px;
    border-radius: 8px;
    margin: 0 2px;
  }}
  .g-tok, .tok-gold {{
    color: #e8b85a;
    background: rgba(232, 184, 90, 0.15);
    border: 1.5px solid rgba(232, 184, 90, 0.58);
  }}
  .tok-orange {{
    color: #fb923c;
    background: rgba(251, 146, 60, 0.15);
    border: 1.5px solid rgba(251, 146, 60, 0.62);
  }}
  .tok-teal {{
    color: #2dd4bf;
    background: rgba(45, 212, 191, 0.15);
    border: 1.5px solid rgba(45, 212, 191, 0.62);
  }}
  .coincide-tok, .tok-blue {{
    color: #38bdf8;
    background: rgba(56, 189, 248, 0.15);
    border: 1.5px solid rgba(56, 189, 248, 0.65);
  }}
  .tok-purple {{
    color: #a855f7;
    background: rgba(168, 85, 247, 0.15);
    border: 1.5px solid rgba(168, 85, 247, 0.62);
  }}
  .tok-orchid {{
    color: #e879f9;
    background: rgba(232, 121, 249, 0.15);
    border: 1.5px solid rgba(232, 121, 249, 0.62);
  }}
  .tok-pink {{
    color: #f472b6;
    background: rgba(244, 114, 182, 0.15);
    border: 1.5px solid rgba(244, 114, 182, 0.62);
  }}
  .eq-tok, .tok-green {{
    color: #5ec27e;
    background: rgba(94, 194, 126, 0.15);
    border: 1.5px solid rgba(94, 194, 126, 0.62);
  }}
  .tok-lime {{
    color: #a3e635;
    background: rgba(163, 230, 53, 0.15);
    border: 1.5px solid rgba(163, 230, 53, 0.62);
  }}
  .muted-tok, .tok-muted {{
    display: inline-block;
    font-family: "JetBrains Mono", monospace;
    font-size: 25px;
    font-weight: 600;
    color: #94a3b8;
    background: rgba(148, 163, 184, 0.10);
    border: 1px solid rgba(148, 163, 184, 0.32);
    padding: 3px 13px;
    border-radius: 8px;
  }}
  .etc-table-block {{
    display: flex;
    flex-direction: column;
    gap: 16px;
    width: 100%;
  }}
  .etc-section {{
    display: flex;
    flex-direction: column;
    gap: 10px;
    background: rgba(11, 14, 21, 0.58);
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 12px;
    padding: 14px 18px;
  }}
  .etc-sec-head {{
    font-family: "JetBrains Mono", monospace;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #94a3b8;
  }}
  .etc-sec-head-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 16px;
  }}
  .etc-head-cols, .etc-row-cells {{
    display: grid;
    grid-template-columns: repeat(3, 72px);
    gap: 8px;
    align-items: center;
  }}
  .etc-head-cols > span {{
    font-family: Georgia, "Times New Roman", serif;
    font-style: italic;
    font-size: 20px;
    font-weight: 700;
    color: #94a3b8;
    text-align: center;
  }}
  .etc-row-cells > span {{
    display: flex;
    align-items: center;
    justify-content: center;
    width: 72px;
    font-size: 24px;
    padding: 3px 0 !important;
    margin: 0 !important;
    text-align: center;
  }}
  .etc-coord-grid {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    align-items: center;
  }}
  .etc-coord-grid.stack {{
    grid-template-columns: 1fr;
    gap: 8px;
  }}
  .etc-coord-grid > span {{
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 25px;
    padding: 6px 10px;
    margin: 0;
    width: 100%;
    text-align: center;
  }}
  .etc-coord-grid.stack > span {{
    justify-content: flex-start;
    font-size: 24px;
    padding: 5px 14px;
  }}
  .etc-data-list {{
    display: flex;
    flex-direction: column;
    gap: 10px;
    width: 100%;
  }}
  .etc-data-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    background: rgba(11, 14, 21, 0.58);
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 10px;
    padding: 10px 16px;
  }}
  .etc-data-row.muted-row {{
    opacity: 0.52;
  }}
  .etc-row-label {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 24px;
    font-weight: 600;
    color: #e2e8f0;
  }}
  .etc-data-row > span:not(.etc-row-label) {{
    font-size: 25px;
    padding: 3px 13px;
    margin: 0;
    white-space: nowrap;
  }}
  .just-card {{
    background: rgba(11, 14, 21, 0.9);
    border: 1px solid rgba(148, 163, 184, 0.3);
    border-radius: 14px;
    padding: 20px 24px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}
  .just-header {{
    display: flex;
    align-items: center;
    gap: 12px;
  }}
  .just-tag {{
    font-family: "JetBrains Mono", monospace;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.12em;
    color: #94a3b8;
  }}
  .just-pill {{
    font-family: "JetBrains Mono", monospace;
    font-size: 18px;
    font-weight: 700;
    color: #e8b85a;
    background: rgba(232, 184, 90, 0.12);
    border: 1px solid rgba(232, 184, 90, 0.45);
    padding: 4px 14px;
    border-radius: 6px;
  }}
  .just-desc {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 22px;
    line-height: 1.4;
    color: #cbd5e1;
  }}
</style>
</head>
<body>
  <div class="stage-grid"></div>
  <div class="wide-frame">
    <header class="wide-header">
      {header_top_16x9}
      <div class="enunciation-text">“{html.escape(enunciation)}”</div>
      <div class="progress-track">
        {progress_html}
      </div>
    </header>

    <main class="wide-main">
      <div class="geom-stage-card">
        {stage_hud_16x9}
        <div class="svg-canvas-holder" id="svg-holder">
          {stage_inner_markup}
        </div>
        {inset_html}
      </div>

      <section class="step-panel">
        <div class="step-panel-body">
          <div class="step-panel-top">
            <span class="step-kind-badge">STEP {step_num:02d} • {html.escape(step_kind)}</span>
          </div>
          <div class="step-clause">{clause_html}</div>
        </div>
        {just_card_html}
      </section>
    </main>
  </div>
  {_svg_step_script(scene)}
</body>
</html>
"""
  html_path = work_dir / f"slide_16x9_{slide_idx:02d}.html"
  html_path.write_text(html_doc, encoding="utf-8")
  return html_path


def build_euclid_thumbnail_html_16x9(
    scene: dict,
    work_dir: Path,
) -> Path:
  """Builds a 1920x1080 (16:9) YouTube thumbnail featuring the punchline-highlighted graphic and canonical index (e.g. 'I.1' or 'X_1 • Incenter')."""
  prop_id = scene.get("prop_id", "BOOK I • PROP. 1")
  is_etc = str(prop_id).upper().startswith("ETC")
  m = re.search(r"BOOK\s+([IVX]+)\s*•\s*PROP\.\s*(\d+)", prop_id, re.I)
  if m:
    short_code = f"{m.group(1).upper()}.{m.group(2)}"
  else:
    short_code = scene.get("short_code", prop_id)

  g_id = scene.get("g_id", "")
  etc_name = _format_etc_display_name(g_id) if g_id else ""
  if is_etc and etc_name:
    canon_html = (
        f'<div class="canon-title etc-canon-title">'
        f'<span class="canon-code">{html.escape(short_code)}</span>'
        f'<span class="canon-sep">•</span>'
        f'<span class="canon-name">{html.escape(etc_name)}</span>'
        f'</div>'
    )
  else:
    canon_html = f'<div class="canon-title">{html.escape(short_code)}</div>'

  svg_path = Path(scene["svg_path"])
  svg_markup = prepare_euclid_dark_svg(svg_path)
  thumb_scene = {**scene, "_is_thumbnail": True}

  html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 1920px;
    height: 1080px;
    overflow: hidden;
    background: radial-gradient(circle at 55% 50%, #161d2b 0%, #0b0e15 70%, #07090e 100%);
    color: #f4f1ea;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .stage-grid {{
    position: absolute;
    inset: 0;
    background-image:
      linear-gradient(rgba(232, 184, 90, 0.04) 1px, transparent 1px),
      linear-gradient(90deg, rgba(232, 184, 90, 0.04) 1px, transparent 1px);
    background-size: 54px 54px;
    pointer-events: none;
  }}
  .thumb-frame {{
    position: relative;
    width: 1920px;
    height: 1080px;
    padding: 36px;
    display: flex;
  }}
  .thumb-card {{
    position: relative;
    flex: 1;
    height: 100%;
    background: radial-gradient(circle at 54% 52%, #151b28 0%, #0c1018 100%);
    border: 2.5px solid rgba(232, 184, 90, 0.48);
    border-radius: 26px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: {"130px 100px 36px 100px" if is_etc else "44px 120px 44px 200px"};
    box-shadow: inset 0 0 80px rgba(0, 0, 0, 0.7), 0 24px 60px rgba(0, 0, 0, 0.75);
    overflow: hidden;
  }}
  .canon-title {{
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
  }}
  .canon-title.etc-canon-title {{
    display: flex;
    align-items: baseline;
    gap: 20px;
    font-size: 72px;
    padding: 18px 36px;
    max-width: 1740px;
  }}
  .canon-code {{
    color: #e8b85a;
    white-space: nowrap;
  }}
  .canon-sep {{
    color: rgba(232, 184, 90, 0.65);
    font-size: 56px;
  }}
  .canon-name {{
    color: #f8f6f0;
    font-size: 64px;
  }}
  .svg-canvas-holder {{
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .svg-canvas-holder svg {{
    width: 100%;
    height: 100%;
    max-width: 1520px;
    max-height: 860px;
    overflow: visible;
  }}
</style>
</head>
<body>
  <div class="stage-grid"></div>
  <div class="thumb-frame">
    <div class="thumb-card">
      {canon_html}
      <div class="svg-canvas-holder" id="svg-holder">
        {svg_markup}
      </div>
    </div>
  </div>
  {_svg_step_script(thumb_scene)}
</body>
</html>
"""
  html_path = work_dir / "thumbnail_16x9.html"
  html_path.write_text(html_doc, encoding="utf-8")
  return html_path


def build_euclid_thumbnail_html_9x16(
    scene: dict,
    work_dir: Path,
) -> Path:
  """Builds a 1080x1920 (9:16) YouTube Shorts thumbnail featuring the punchline-highlighted graphic and canonical index (e.g. 'I.1' or 'X_1 • Incenter')."""
  prop_id = scene.get("prop_id", "BOOK I • PROP. 1")
  is_etc = str(prop_id).upper().startswith("ETC")
  m = re.search(r"BOOK\s+([IVX]+)\s*•\s*PROP\.\s*(\d+)", prop_id, re.I)
  if m:
    short_code = f"{m.group(1).upper()}.{m.group(2)}"
  else:
    short_code = scene.get("short_code", prop_id)

  g_id = scene.get("g_id", "")
  etc_name = _format_etc_display_name(g_id) if g_id else ""
  if is_etc and etc_name:
    canon_html = (
        f'<div class="canon-title etc-canon-title">'
        f'<span class="canon-code">{html.escape(short_code)}</span>'
        f'<span class="canon-sep">•</span>'
        f'<span class="canon-name">{html.escape(etc_name)}</span>'
        f'</div>'
    )
  else:
    canon_html = f'<div class="canon-title">{html.escape(short_code)}</div>'

  svg_path = Path(scene["svg_path"])
  svg_markup = prepare_euclid_dark_svg(svg_path)
  thumb_scene = {**scene, "_is_thumbnail": True}

  html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 1080px;
    height: 1920px;
    overflow: hidden;
    background: radial-gradient(circle at 50% 48%, #161d2b 0%, #0b0e15 70%, #07090e 100%);
    color: #f4f1ea;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .stage-grid {{
    position: absolute;
    inset: 0;
    background-image:
      linear-gradient(rgba(232, 184, 90, 0.04) 1px, transparent 1px),
      linear-gradient(90deg, rgba(232, 184, 90, 0.04) 1px, transparent 1px);
    background-size: 54px 54px;
    pointer-events: none;
  }}
  .thumb-frame {{
    position: relative;
    width: 1080px;
    height: 1920px;
    padding: 40px 40px 80px 40px;
    display: flex;
  }}
  .thumb-card {{
    position: relative;
    flex: 1;
    height: 100%;
    background: radial-gradient(circle at 50% 52%, #151b28 0%, #0c1018 100%);
    border: 2.5px solid rgba(232, 184, 90, 0.48);
    border-radius: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 230px 56px 140px 56px;
    box-shadow: inset 0 0 80px rgba(0, 0, 0, 0.7), 0 24px 60px rgba(0, 0, 0, 0.75);
    overflow: hidden;
  }}
  .canon-title {{
    position: absolute;
    top: 52px;
    left: 56px;
    font-family: Georgia, "Times New Roman", serif;
    font-size: 132px;
    font-weight: 700;
    letter-spacing: 0.04em;
    line-height: 1;
    color: #e8b85a;
    background: rgba(11, 14, 21, 0.92);
    border: 2px solid rgba(232, 184, 90, 0.55);
    padding: 22px 48px;
    border-radius: 20px;
    box-shadow: 0 18px 45px rgba(0, 0, 0, 0.75);
    z-index: 5;
  }}
  .canon-title.etc-canon-title {{
    right: 56px;
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 16px;
    font-size: 62px;
    padding: 20px 34px;
  }}
  .canon-code {{
    color: #e8b85a;
    white-space: nowrap;
  }}
  .canon-sep {{
    color: rgba(232, 184, 90, 0.65);
    font-size: 48px;
  }}
  .canon-name {{
    color: #f8f6f0;
    font-size: 56px;
  }}
  .svg-canvas-holder {{
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .svg-canvas-holder svg {{
    width: 100%;
    height: 100%;
    max-width: 900px;
    max-height: 1380px;
    overflow: visible;
  }}
</style>
</head>
<body>
  <div class="stage-grid"></div>
  <div class="thumb-frame">
    <div class="thumb-card">
      {canon_html}
      <div class="svg-canvas-holder" id="svg-holder">
        {svg_markup}
      </div>
    </div>
  </div>
  {_svg_step_script(thumb_scene)}
</body>
</html>
"""
  html_path = work_dir / "thumbnail_9x16.html"
  html_path.write_text(html_doc, encoding="utf-8")
  return html_path


def _fmt_srt_time(sec: float) -> str:
  ms = int(round(max(0.0, sec) * 1000))
  h = ms // 3600000
  ms %= 3600000
  m = ms // 60000
  ms %= 60000
  s = ms // 1000
  ms %= 1000
  return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_vtt_time(sec: float) -> str:
  return _fmt_srt_time(sec).replace(",", ".")


def _fmt_chapter_time(sec: float) -> str:
  total_s = int(max(0.0, sec))
  m = total_s // 60
  s = total_s % 60
  return f"{m:02d}:{s:02d}"


def generate_captions(
    manifest: dict,
    out_srt: Path,
    out_vtt: Path,
) -> tuple[Path, Path]:
  """Generates frame-accurate .srt and .vtt closed captions from speak.py manifest."""
  segments = manifest.get("segments", [])
  cues = []
  for seg in segments:
    subsegs = seg.get("subsegments")
    units = subsegs if subsegs else [seg]
    for u_idx, unit in enumerate(units):
      start_s = float(unit.get("start_sec", 0.0)) + (0.42 if u_idx == 0 else 0.15)
      end_s = float(unit.get("end_sec", start_s + 2.0))
      avail = max(0.6, end_s - start_s)

      raw_text = unit.get("text", "")
      parts = [
          clean_caption_text(p)
          for p in re.split(r"<\s*(?:short\s+|long\s+)?pause\s*>|(?<=[.!?])\s+", raw_text)
          if clean_caption_text(p)
      ]
      if not parts:
        continue

      total_chars = sum(len(p) for p in parts) or 1
      cur_t = start_s
      for p in parts:
        dur = avail * (len(p) / total_chars)
        cues.append((cur_t, cur_t + dur, p))
        cur_t += dur

  srt_lines = []
  vtt_lines = ["WEBVTT", ""]
  for idx, (st, et, txt) in enumerate(cues, start=1):
    srt_lines.append(str(idx))
    srt_lines.append(f"{_fmt_srt_time(st)} --> {_fmt_srt_time(et)}")
    srt_lines.append(txt)
    srt_lines.append("")

    vtt_lines.append(str(idx))
    vtt_lines.append(f"{_fmt_vtt_time(st)} --> {_fmt_vtt_time(et)}")
    vtt_lines.append(txt)
    vtt_lines.append("")

  out_srt.write_text("\n".join(srt_lines), encoding="utf-8")
  out_vtt.write_text("\n".join(vtt_lines), encoding="utf-8")
  return out_srt, out_vtt


DEFAULT_SHORT_TITLES = {
    "I.1": "construct equilateral triangle",
    "I.2": "place equal segment at point",
    "I.3": "cut equal segment from greater",
    "I.4": "side-angle-side triangle congruence",
    "I.5": "isosceles base angles equal",
}


def _concise_chapter_label(sc: dict) -> str:
  if sc.get("chapter"):
    return str(sc["chapter"]).strip()
  sym = str(sc.get("symbolic", "")).strip()
  if sym:
    return sym
  plain = clean_caption_text(sc.get("clause_html", ""))
  return plain[:48].rstrip(".")


def generate_youtube_metadata(
    expanded_specs: list[dict],
    output_dir: Path,
    prefix: str,
    short_mp4: Path,
    wide_mp4: Path,
    srt_path: Path,
    thumb_png: Path | None = None,
    short_thumb_png: Path | None = None,
) -> tuple[Path, Path]:
  """Generates youtube.txt and youtube.json with titles, descriptions, chapters, and tags."""
  first_sc = expanded_specs[0]["scene"] if expanded_specs else {}
  prop_id = first_sc.get("prop_id", "BOOK I • PROP. 1")
  g_id = first_sc.get("g_id", "G.5")
  enunciation = first_sc.get("enunciation", "")

  # Parse book & prop number if present
  m = re.search(r"BOOK\s+([IVX]+)\s*•\s*PROP\.\s*(\d+)", prop_id, re.I)
  if m:
    book_rom, prop_num = m.group(1).upper(), m.group(2)
    short_code = f"{book_rom}.{prop_num}"
    book_slug = f"book_{book_rom.lower()}"
    prop_pad = f"{int(prop_num):02d}"
    euclid_url = f"https://geometor.github.io/euclid/heath/volume_I/{book_slug}/propositions/{prop_pad}/"
  else:
    short_code = prop_id
    euclid_url = "https://geometor.github.io/euclid/"

  elements_url = f"https://geometor.github.io/elements/elements2/{g_id}/"

  short_title = (
      first_sc.get("short_title")
      or DEFAULT_SHORT_TITLES.get(short_code)
      or enunciation.rstrip(".").lower()
  )
  title_1080p = f"{short_code} • {short_title} • Euclid's Elements"
  if len(title_1080p) > 100:
    title_1080p = title_1080p[:97] + "..."
  title_short = title_1080p

  chapters = []
  for idx, item in enumerate(expanded_specs):
    sc = item["scene"]
    t_str = "00:00" if idx == 0 else _fmt_chapter_time(item["start_sec"])
    s_kind = sc.get("step_kind", "STEP").upper()
    chap_label = _concise_chapter_label(sc)
    chapters.append(f"{t_str} {idx + 1} · {s_kind}: {chap_label}")

  chapters_block = "\n".join(chapters)

  yt_json_path = output_dir / "youtube.json"
  existing_uploads = {}
  version = int(first_sc.get("version", 2))
  if yt_json_path.exists():
    try:
      old_data = json.loads(yt_json_path.read_text(encoding="utf-8"))
      if "version" in old_data and "version" not in first_sc:
        version = int(old_data["version"])
      if "uploads" in old_data:
        existing_uploads["uploads"] = old_data["uploads"]
      if "history" in old_data:
        existing_uploads["history"] = old_data["history"]
    except Exception:
      pass

  description = f"""“{enunciation}”
— Euclid's Elements, {prop_id} ({g_id})

{chapters_block}

Explore the interactive Book Codex, symbolic model, and dependency tree:
• Elements Codex ({g_id}): {elements_url}
• Heath Edition ({short_code}): {euclid_url}
• GitHub: https://github.com/geometor/elements

GEOMETOR • {g_id} • v{version}
"""

  def _yt_safe(text: str) -> str:
    # YouTube forbids literal ASCII '<' and '>' in titles, descriptions, and tags.
    # Replace them with Unicode mathematical angle brackets ⟨ (U+27E8) and ⟩ (U+27E9).
    return text.replace("<", "⟨").replace(">", "⟩")

  title_1080p = _yt_safe(title_1080p)
  title_short = _yt_safe(title_short)
  description = _yt_safe(description.strip())

  tags = [
      _yt_safe(t)
      for t in [
          "Euclid",
          "Euclid's Elements",
          f"Euclid {short_code}",
          g_id,
          "Geometry",
          "Constructive Geometry",
          "Compass and Straightedge",
          "Mathematical Proof",
          "GEOMETOR",
          "Thomas Little Heath",
      ]
  ]

  thumb_line = f"\n• 16:9 Thumbnail : {thumb_png.name}" if thumb_png else ""
  short_thumb_line = (
      f"\n• 9:16 Thumbnail : {short_thumb_png.name}" if short_thumb_png else ""
  )
  yt_txt = f"""================================================================================
YOUTUBE 1080P (16:9) TITLE
================================================================================
{title_1080p}

================================================================================
YOUTUBE SHORT (9:16) TITLE
================================================================================
{title_short}

================================================================================
DESCRIPTION
================================================================================
{description}

================================================================================
TAGS
================================================================================
{", ".join(tags)}

================================================================================
MEDIA & CAPTIONS FILES
================================================================================
• 9:16 Short MP4 : {short_mp4.name}
• 16:9 1080p MP4 : {wide_mp4.name}
• Closed Captions: {srt_path.name}{thumb_line}{short_thumb_line}
"""
  yt_txt_path = output_dir / "youtube.txt"
  yt_txt_path.write_text(yt_txt, encoding="utf-8")

  files_dict = {
      "short_mp4": str(short_mp4.resolve()),
      "wide_1080p_mp4": str(wide_mp4.resolve()),
      "captions_srt": str(srt_path.resolve()),
  }
  if thumb_png:
    files_dict["thumbnail_16x9_png"] = str(thumb_png.resolve())
  if short_thumb_png:
    files_dict["thumbnail_9x16_png"] = str(short_thumb_png.resolve())

  payload = {
      "prop_id": prop_id,
      "short_code": short_code,
      "g_id": g_id,
      "version": version,
      "enunciation": _yt_safe(enunciation),
      "title_1080p": title_1080p,
      "title_short": title_short,
      "description": description,
      "tags": tags,
      "categoryId": "27",
      "privacyStatus": "unlisted",
      "euclid_url": euclid_url,
      "elements_url": elements_url,
      "files": files_dict,
      **existing_uploads,
  }
  yt_json_path.write_text(
      json.dumps(payload, indent=2),
      encoding="utf-8",
  )
  return yt_txt_path, yt_json_path


def assemble_mp4(
    slide_pngs: list[Path],
    durations: list[float],
    audio_wav: Path | None,
    out_mp4: Path,
    upscale_qhd: bool = False,
) -> Path:
  concat_file = out_mp4.parent / f"_{out_mp4.stem}_concat.txt"
  lines = []
  total_dur = 0.0
  for png, dur in zip(slide_pngs, durations):
    d = max(0.4, dur)
    total_dur += d
    lines.append(f"file '{png.resolve()}'")
    lines.append(f"duration {d:.3f}")
  lines.append(f"file '{slide_pngs[-1].resolve()}'")
  concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

  # Widescreen 16:9 videos are mastered at 2560x1440 (1440p QHD) so YouTube
  # routes them to its full 7-rung VP9 + H.264 hardware pipeline (144p..1440p)
  # and starts playback in HD instead of 240p.
  if upscale_qhd:
    vf_chain = "fps=30,scale=2560:1440:flags=lanczos,setsar=1:1,format=yuv420p"
    h264_level = "5.1"
    maxrate = "16M"
    bufsize = "32M"
  else:
    vf_chain = "fps=30,setsar=1:1,format=yuv420p"
    h264_level = "4.1"
    maxrate = "10M"
    bufsize = "20M"

  cmd = [
      "/usr/bin/ffmpeg",
      "-y",
      "-f",
      "concat",
      "-safe",
      "0",
      "-i",
      str(concat_file),
  ]
  if audio_wav and audio_wav.exists():
    cmd.extend([
        "-i",
        str(audio_wav),
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-b:a",
        "192k",
        "-shortest",
    ])
  cmd.extend([
      "-t",
      f"{total_dur:.3f}",
      "-vf",
      vf_chain,
      "-vsync",
      "cfr",
      "-r",
      "30",
      "-c:v",
      "libx264",
      "-preset",
      "fast",
      "-profile:v",
      "high",
      "-level:v",
      h264_level,
      "-crf",
      "18",
      "-maxrate",
      maxrate,
      "-bufsize",
      bufsize,
      "-g",
      "90",
      "-keyint_min",
      "90",
      "-sc_threshold",
      "0",
      "-bf",
      "2",
      "-flags",
      "+cgop",
      "-colorspace",
      "bt709",
      "-color_primaries",
      "bt709",
      "-color_trc",
      "bt709",
      "-color_range",
      "tv",
      "-movflags",
      "+faststart",
      str(out_mp4),
  ])
  subprocess.run(cmd, check=True, capture_output=True)
  concat_file.unlink(missing_ok=True)
  return out_mp4


def build_dual_studio_player(
    expanded_slides: list[dict],
    short_mp4: Path,
    wide_mp4: Path,
    vtt_path: Path,
    out_html: Path,
) -> Path:
  """Builds an inline <agent-embed> Studio Player with 9:16 Short / 16:9 1080p toggle + CC."""
  short_uri = data_uri_for_file(short_mp4)
  wide_uri = data_uri_for_file(wide_mp4)
  vtt_uri = data_uri_for_file(vtt_path)

  slides_data = []
  for idx, item in enumerate(expanded_slides):
    sc = item["scene"]
    seg = item["seg"] or {}
    step_kind = sc.get("step_kind") or sc.get("type", "STEP").upper()
    clause = clean_caption_text(
        sc.get("clause_html") or sc.get("caption") or seg.get("text", "")
    )
    slides_data.append({
        "index": idx,
        "step_kind": step_kind,
        "clause": clause,
        "just_id": sc.get("just_id", ""),
        "start_sec": item["start_sec"],
        "end_sec": item["end_sec"],
    })

  slides_json = json.dumps(slides_data)
  doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0b0d13;
    color: #f4f1ea;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 12px;
  }}
  .studio-shell {{
    background: #11151f;
    border: 1px solid rgba(232, 184, 90, 0.35);
    border-radius: 14px;
    padding: 16px;
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.65);
    display: flex;
    flex-direction: column;
    gap: 14px;
  }}
  .studio-topbar {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid rgba(232, 184, 90, 0.24);
    padding-bottom: 12px;
    flex-wrap: wrap;
    gap: 10px;
  }}
  .mode-tabs {{
    display: flex;
    gap: 8px;
  }}
  .mode-btn {{
    font-family: monospace;
    font-size: 13px;
    font-weight: 700;
    padding: 7px 14px;
    border-radius: 8px;
    border: 1px solid rgba(232, 184, 90, 0.35);
    background: #161b26;
    color: #cbd5e1;
    cursor: pointer;
  }}
  .mode-btn.active {{
    background: rgba(232, 184, 90, 0.18);
    border-color: #e8b85a;
    color: #fff;
  }}
  .studio-grid {{
    display: grid;
    grid-template-columns: 360px 1fr;
    gap: 18px;
    align-items: start;
  }}
  .studio-grid.mode-16x9 {{
    grid-template-columns: 1fr;
  }}
  .viewport-box {{
    width: 100%;
    max-width: 360px;
    aspect-ratio: 9 / 16;
    background: #07080c;
    border: 2px solid rgba(232, 184, 90, 0.45);
    border-radius: 16px;
    overflow: hidden;
    margin: 0 auto;
    box-shadow: 0 14px 35px rgba(0, 0, 0, 0.75);
  }}
  .studio-grid.mode-16x9 .viewport-box {{
    max-width: 100%;
    aspect-ratio: 16 / 9;
    border-radius: 12px;
  }}
  video {{
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }}
  .step-list {{
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-height: 580px;
    overflow-y: auto;
  }}
  .studio-grid.mode-16x9 .step-list {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    max-height: none;
  }}
  .step-btn {{
    background: #161b26;
    border: 1px solid rgba(232, 184, 90, 0.22);
    border-radius: 10px;
    padding: 10px 14px;
    text-align: left;
    color: #cbd5e1;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }}
  .step-btn.active {{
    background: rgba(232, 184, 90, 0.15);
    border-color: #e8b85a;
    color: #fff;
  }}
  .step-btn-top {{
    display: flex;
    justify-content: space-between;
    font-family: monospace;
    font-size: 12px;
    color: #e8b85a;
    font-weight: 700;
  }}
  .step-clause-preview {{
    font-family: Georgia, serif;
    font-size: 13.5px;
    line-height: 1.35;
    color: #f4f1ea;
  }}
</style>
</head>
<body>
  <div class="studio-shell">
    <div class="studio-topbar">
      <div class="mode-tabs">
        <button class="mode-btn active" id="btn-9x16" onclick="switchFormat('9x16')">9:16 YouTube Short (1080×1920)</button>
        <button class="mode-btn" id="btn-16x9" onclick="switchFormat('16x9')">16:9 Widescreen 1080p (1920×1080)</button>
      </div>
      <span style="font-family:monospace;font-size:12px;color:#94a3b8;">CC (.srt / .vtt) + YouTube Metadata Ready</span>
    </div>
    <div class="studio-grid" id="studio-grid">
      <div class="viewport-box">
        <video id="studio-video" controls preload="metadata" src="{short_uri}">
          <track kind="captions" src="{vtt_uri}" srclang="en" label="English" default />
        </video>
      </div>
      <div class="step-list" id="step-list"></div>
    </div>
  </div>
  <script>
    const SHORT_URI = "{short_uri}";
    const WIDE_URI = "{wide_uri}";
    const SLIDES = {slides_json};
    const videoEl = document.getElementById('studio-video');
    const gridEl = document.getElementById('studio-grid');
    const listEl = document.getElementById('step-list');
    let currentIdx = -1;

    function switchFormat(fmt) {{
      const t = videoEl.currentTime;
      const wasPlaying = !videoEl.paused;
      if (fmt === '16x9') {{
        gridEl.classList.add('mode-16x9');
        document.getElementById('btn-16x9').classList.add('active');
        document.getElementById('btn-9x16').classList.remove('active');
        videoEl.src = WIDE_URI;
      }} else {{
        gridEl.classList.remove('mode-16x9');
        document.getElementById('btn-9x16').classList.add('active');
        document.getElementById('btn-16x9').classList.remove('active');
        videoEl.src = SHORT_URI;
      }}
      videoEl.currentTime = t;
      if (wasPlaying) videoEl.play();
    }}

    function updateActive(idx) {{
      if (idx < 0 || idx >= SLIDES.length || idx === currentIdx) return;
      currentIdx = idx;
      document.querySelectorAll('.step-btn').forEach((b, i) => {{
        b.classList.toggle('active', i === idx);
      }});
    }}

    SLIDES.forEach((s, i) => {{
      const btn = document.createElement('button');
      btn.className = 'step-btn';
      const justStr = s.just_id ? ` • [${{s.just_id}}]` : '';
      btn.innerHTML = `
        <div class="step-btn-top">
          <span>STEP ${{i + 1}} • ${{s.step_kind}}${{justStr}}</span>
          <span>${{s.start_sec.toFixed(1)}}s</span>
        </div>
        <div class="step-clause-preview">${{s.clause}}</div>
      `;
      btn.onclick = () => {{
        videoEl.currentTime = s.start_sec + 0.05;
        videoEl.play();
        updateActive(i);
      }};
      listEl.appendChild(btn);
    }});

    videoEl.addEventListener('timeupdate', () => {{
      const t = videoEl.currentTime;
      for (let i = SLIDES.length - 1; i >= 0; i--) {{
        if (t >= SLIDES[i].start_sec) {{
          updateActive(i);
          break;
        }}
      }}
    }});

    updateActive(0);
  </script>
</body>
</html>
"""
  out_html.write_text(doc, encoding="utf-8")
  return out_html


def render_shorts_sequence(
    scenes_path: Path,
    manifest_path: Path,
    output_dir: Path,
    prefix: str = "I_01",
    meta_only: bool = False,
    reuse_frames: bool = False,
) -> dict:
  if scenes_path.suffix.lower() in (".scr", ".txt"):
    import sys as _sys
    _studio_src = "/home/phi/PROJECTS/photon-platform/studio/src"
    if _studio_src not in _sys.path:
      _sys.path.insert(0, _studio_src)
    from photon_platform.studio.script_compiler import load_scenes_or_compile_scr
    scenes = load_scenes_or_compile_scr(scenes_path)
  else:
    scenes = json.loads(scenes_path.read_text(encoding="utf-8"))
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  segments = manifest.get("segments", [])
  audio_wav = Path(manifest["audio_wav"])
  if not audio_wav.is_absolute() or not audio_wav.exists():
    candidate_wav = manifest_path.with_suffix(".wav")
    if candidate_wav.exists():
      audio_wav = candidate_wav

  output_dir.mkdir(parents=True, exist_ok=True)

  expanded_specs = []
  running_time = 0.0

  for idx, scene in enumerate(scenes):
    seg = segments[idx] if idx < len(segments) else None
    seg_dur = (
        seg["duration_sec"]
        if seg
        else float(scene.get("duration_sec", 5.0))
    )
    seg_start = seg["start_sec"] if seg else running_time
    subs = scene.get("subslides")
    if subs and isinstance(subs, list):
      subsegs = seg.get("subsegments", []) if seg else []
      if len(subsegs) == len(subs):
        for sub, sseg in zip(subs, subsegs):
          merged = {k: v for k, v in scene.items() if k != "subslides"}
          merged.update(sub)
          expanded_specs.append({
              "scene": merged,
              "seg": sseg,
              "duration": float(sseg["duration_sec"]),
              "start_sec": float(sseg["start_sec"]),
              "end_sec": float(sseg["end_sec"]),
          })
      else:
        total_w = sum(float(s.get("weight", 1.0)) for s in subs) or 1.0
        cur_t = seg_start
        for sub in subs:
          merged = {k: v for k, v in scene.items() if k != "subslides"}
          merged.update(sub)
          w = float(sub.get("weight", 1.0))
          sub_dur = seg_dur * (w / total_w)
          expanded_specs.append({
              "scene": merged,
              "seg": seg,
              "duration": sub_dur,
              "start_sec": cur_t,
              "end_sec": cur_t + sub_dur,
          })
          cur_t += sub_dur
    else:
      expanded_specs.append({
          "scene": scene,
          "seg": seg,
          "duration": seg_dur,
          "start_sec": seg_start,
          "end_sec": seg_start + seg_dur,
      })
    running_time = seg_start + seg_dur

  thumb_png = output_dir / f"{prefix}_thumb.png"
  short_thumb_png = output_dir / f"{prefix}_short_thumb.png"
  short_mp4 = output_dir / f"{prefix}_short.mp4"
  wide_mp4 = output_dir / f"{prefix}_1080p.mp4"
  srt_path = output_dir / f"{prefix}.srt"
  vtt_path = output_dir / f"{prefix}.vtt"

  if meta_only:
    if not srt_path.exists() or not vtt_path.exists():
      generate_captions(manifest, srt_path, vtt_path)
    yt_txt, yt_json = generate_youtube_metadata(
        expanded_specs,
        output_dir,
        prefix,
        short_mp4,
        wide_mp4,
        srt_path,
        thumb_png if thumb_png.exists() else None,
        short_thumb_png if short_thumb_png.exists() else None,
    )
    print(f"Generated YouTube Meta   : {yt_txt} & {yt_json}")
    return {
        "youtube_txt": str(yt_txt),
        "youtube_json": str(yt_json),
    }

  work_dir = output_dir / f"_{prefix}_work"
  work_dir.mkdir(parents=True, exist_ok=True)
  dir_9x16 = output_dir / "slides_9x16"
  dir_16x9 = output_dir / "slides_16x9"
  dir_9x16.mkdir(parents=True, exist_ok=True)
  dir_16x9.mkdir(parents=True, exist_ok=True)

  short_specs = [
      item
      for item in expanded_specs
      if not item["scene"].get("widescreen_only", False)
  ]
  total_frames_9x16 = len(short_specs)
  total_frames_16x9 = len(expanded_specs)

  pngs_9x16 = []
  pngs_16x9 = []
  durations_9x16 = []
  durations_16x9 = []
  rendered_any_9x16 = False
  rendered_any_16x9 = False

  for f_idx, item in enumerate(expanded_specs):
    sc = item["scene"]
    dur = item["duration"]
    durations_16x9.append(dur)

    out_9x16 = dir_9x16 / f"slide_{f_idx:02d}.png"
    out_16x9 = dir_16x9 / f"slide_{f_idx:02d}.png"

    # 1. 9:16 Vertical Frame (1080x1920) — skip if widescreen_only
    if sc.get("widescreen_only", False):
      status_9x16 = "widescreen-only (skipped 9:16)"
    elif reuse_frames and out_9x16.exists():
      pngs_9x16.append(out_9x16)
      durations_9x16.append(dur)
      status_9x16 = "reused 9:16"
    else:
      html_9x16 = build_euclid_step_html_9x16(
          sc, f_idx, total_frames_9x16, work_dir
      )
      chrome_screenshot(str(html_9x16), out_9x16, width=1080, height=1920, wait_ms=600)
      pngs_9x16.append(out_9x16)
      durations_9x16.append(dur)
      rendered_any_9x16 = True
      status_9x16 = "rendered 9:16"

    # 2. 16:9 Widescreen 1080p Frame (1920x1080)
    if reuse_frames and out_16x9.exists():
      pngs_16x9.append(out_16x9)
      status_16x9 = "reused 16:9"
    else:
      html_16x9 = build_euclid_step_html_16x9(
          sc, f_idx, total_frames_16x9, work_dir
      )
      chrome_screenshot(str(html_16x9), out_16x9, width=1920, height=1080, wait_ms=600)
      pngs_16x9.append(out_16x9)
      rendered_any_16x9 = True
      status_16x9 = "rendered 16:9"

    print(
        f"Step {f_idx + 1}/{total_frames_16x9} ({dur:.2f}s) [{status_9x16}, {status_16x9}]"
    )

  # Render 16:9 YouTube Thumbnail (1920x1080) and 9:16 Shorts Thumbnail (1080x1920) from final proof punchline scene
  punch_candidates = [
      item["scene"] for item in expanded_specs if item["scene"].get("is_punchline")
  ] or [item["scene"] for item in short_specs]
  punch_scene = (
      punch_candidates[-1]
      if punch_candidates
      else (expanded_specs[-1]["scene"] if expanded_specs else {})
  )
  if not (reuse_frames and thumb_png.exists()):
    thumb_html = build_euclid_thumbnail_html_16x9(punch_scene, work_dir)
    chrome_screenshot(str(thumb_html), thumb_png, width=1920, height=1080, wait_ms=600)
    print(f"Rendered 16:9 Thumbnail  : {thumb_png}")

  if not (reuse_frames and short_thumb_png.exists()):
    short_thumb_html = build_euclid_thumbnail_html_9x16(punch_scene, work_dir)
    chrome_screenshot(
        str(short_thumb_html), short_thumb_png, width=1080, height=1920, wait_ms=600
    )
    print(f"Rendered 9:16 Thumbnail  : {short_thumb_png}")

  if not (reuse_frames and short_mp4.exists() and not rendered_any_9x16):
    assemble_mp4(pngs_9x16, durations_9x16, audio_wav, short_mp4, upscale_qhd=False)
    print(f"Assembled 9:16 Short MP4 : {short_mp4}")
  else:
    print(f"Reused 9:16 Short MP4    : {short_mp4}")

  if not (reuse_frames and wide_mp4.exists() and not rendered_any_16x9):
    assemble_mp4(pngs_16x9, durations_16x9, audio_wav, wide_mp4, upscale_qhd=True)
    print(f"Assembled 16:9 1440p MP4 : {wide_mp4}")
  else:
    print(f"Reused 16:9 1440p MP4    : {wide_mp4}")

  generate_captions(manifest, srt_path, vtt_path)
  print(f"Generated Captions       : {srt_path} & {vtt_path}")

  yt_txt, yt_json = generate_youtube_metadata(
      expanded_specs,
      output_dir,
      prefix,
      short_mp4,
      wide_mp4,
      srt_path,
      thumb_png,
      short_thumb_png,
  )
  print(f"Generated YouTube Meta   : {yt_txt} & {yt_json}")

  out_html = output_dir / f"{prefix}_stage.html"
  build_dual_studio_player(expanded_specs, short_mp4, wide_mp4, vtt_path, out_html)
  print(f"Built Dual Studio Player : {out_html}")

  # Clean up temporary HTML work_dir
  for f in work_dir.glob("*.html"):
    f.unlink(missing_ok=True)
  work_dir.rmdir()

  return {
      "slides_9x16": [str(p) for p in pngs_9x16],
      "slides_16x9": [str(p) for p in pngs_16x9],
      "thumb_png": str(thumb_png),
      "short_thumb_png": str(short_thumb_png),
      "short_mp4": str(short_mp4),
      "wide_mp4": str(wide_mp4),
      "srt": str(srt_path),
      "vtt": str(vtt_path),
      "youtube_txt": str(yt_txt),
      "youtube_json": str(yt_json),
      "html": str(out_html),
  }


def main():
  parser = argparse.ArgumentParser(
      description="Render both 1080x1920 (9:16 Short) and 1920x1080 (16:9 1080p) proposition videos + CC + YouTube metadata."
  )
  parser.add_argument(
      "-s", "--scenes", required=True, help="Path to scenes.json specification."
  )
  parser.add_argument(
      "-m", "--manifest", required=True, help="Path to <prefix>.json from speak.py."
  )
  parser.add_argument(
      "-o", "--output-dir", required=True, help="Output directory."
  )
  parser.add_argument(
      "-p", "--prefix", default="I_01", help="Filename prefix (e.g., I_01)."
  )
  parser.add_argument(
      "--meta-only",
      action="store_true",
      help="Regenerate only youtube.txt and youtube.json without re-rendering frames or MP4s.",
  )
  parser.add_argument(
      "--reuse-frames",
      action="store_true",
      help="Reuse existing slide PNGs and thumbnails when only audio or timing changed.",
  )
  args = parser.parse_args()

  render_shorts_sequence(
      scenes_path=Path(args.scenes).resolve(),
      manifest_path=Path(args.manifest).resolve(),
      output_dir=Path(args.output_dir).resolve(),
      prefix=args.prefix,
      meta_only=args.meta_only,
      reuse_frames=args.reuse_frames,
  )


if __name__ == "__main__":
  main()
