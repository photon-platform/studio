"""Shared SVG dark-mode preparation, step color palette, caption cleaner, and Chromium locator."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

STEP_KIND_COLORS: dict[str, tuple[str, str]] = {
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
    """Locate headless Chromium/Chrome binary (preferring Playwright's Chromium build)."""
    pw_root = Path.home() / ".cache" / "ms-playwright"
    if pw_root.is_dir():
        for candidate in sorted(pw_root.glob("chromium-*/chrome-linux*/chrome"), reverse=True):
            if candidate.exists():
                return candidate
    for name in (
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    ):
        resolved = shutil.which(name)
        if resolved:
            return Path(resolved)
        p = Path(name)
        if p.exists():
            return p
    raise FileNotFoundError("Headless Chromium binary not found.")


def clean_caption_text(text: str) -> str:
    """Strip HTML tags and Studio `[bell:...]` / `[style:...]` / `[sfx:...]` / `[voice:...]` cues."""
    text = re.sub(r"</div\s*>|<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[(?:bell|style|sfx|voice):[^\]]+\]", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def prepare_euclid_dark_svg(svg_path: Path) -> str:
    """Read an Euclid or GEOMETOR ETC `.svg` and enforce high-contrast dark mode + crisp stroke weights."""
    raw = svg_path.read_text(encoding="utf-8")
    raw = re.sub(r"<\?xml[^>]*\?>", "", raw)
    if "geometor-etc-drawing" in raw:
        raw = re.sub(r"<script\b[^>]*>.*?</script>", "", raw, flags=re.DOTALL | re.IGNORECASE)
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
