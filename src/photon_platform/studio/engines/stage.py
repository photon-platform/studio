#!/usr/bin/env python3
"""GEOMETOR 1080p Visual Stage Renderer & Video Sequencer.

Renders sequenced 1920x1080 (16:9) visual frames from:
  1. 'graphviz' : Graphviz DOT specifications rendered via /usr/bin/dot -Tsvg
  2. 'mermaid'  : Mermaid diagrams rendered via local mermaid.min.js
  3. 'web'      : Live 1920x1080 screen captures of GEOMETOR project docs & web demos
  4. 'svg'      : Native SVG figures / Pacioli folios / D3 / split-screen cards
  5. 'image'    : 16:9 Nano Banana conceptual visuals with lower-third overlays

Then sequences the 1080p PNG frames against the exact segment timestamps in
<turn>.json (emitted by speak.py) to assemble:
  - Individual 1920x1080 PNG slides (slide_00.png, slide_01.png, ...)
  - A synchronized 1920x1080 H.264 MP4 video (<turn>.mp4) via ffmpeg
  - A self-contained interactive HTML stage player (<turn>_stage.html) for <agent-embed>
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
D3_JS = Path(
    "/usr/share/antigravity/resources/app/node_modules/d3/dist/d3.min.js"
)

BELL_META = {
    "bell:context": ("CONTEXT • 3:2 FIFTH", "#60a5fa"),
    "bell:proposition": ("PROPOSITION • 2:1 OCTAVE", "#d4af37"),
    "bell:construction": ("CONSTRUCTION • 3:2 FIFTH", "#34d399"),
    "bell:insight": ("SYNTHESIS • 1 : φ : 2 GOLDEN TRIAD", "#f59e0b"),
    "bell:punchline": ("ASIDE • MAJOR TRIAD", "#c084fc"),
}


def find_chrome() -> Path:
  for c in CHROME_CANDIDATES:
    if c.exists():
      return c
  raise FileNotFoundError("Headless Chromium binary not found.")


def clean_caption_text(text: str) -> str:
  """Removes TTS prosody tags (<short pause>, [bell:...], etc.) for visual display."""
  text = re.sub(r"<[^>]+>", "", text)
  text = re.sub(r"\[(?:bell|style):[^\]]+\]", "", text)
  return re.sub(r"\s+", " ", text).strip()


def chrome_screenshot(url_or_path: str, out_png: Path, wait_ms: int = 800) -> Path:
  """Captures an exact 1920x1080 viewport screenshot with zero bottom bar cutoff."""
  chrome = find_chrome()
  out_png.parent.mkdir(parents=True, exist_ok=True)
  raw_png = out_png.with_name(f"_raw_{out_png.name}")

  env = os.environ.copy()
  env["XDG_CONFIG_HOME"] = "/tmp/geometor_xdg_config"
  env["XDG_CACHE_HOME"] = "/tmp/geometor_xdg_cache"
  env["HOME"] = "/tmp"

  target = (
      url_or_path
      if url_or_path.startswith(("http://", "https://", "file://"))
      else f"file://{Path(url_or_path).resolve()}"
  )
  # Use 1920x1160 window size so the inner viewport is >=1080px tall, then crop to exact 1920x1080
  cmd = [
      str(chrome),
      "--headless=new",
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
      "--window-size=1920,1160",
      f"--virtual-time-budget={wait_ms}",
      f"--screenshot={raw_png}",
  ]
  if target.startswith(("http://", "https://")):
    cmd.append("--incognito")
  cmd.append(target)
  res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30)
  if res.returncode != 0 or not raw_png.exists():
    raise RuntimeError(
        f"Chromium screenshot failed ({res.returncode}): {res.stderr[:400]}"
    )
  subprocess.run(
      ["/usr/bin/convert", str(raw_png), "-crop", "1920x1080+0+0", "+repage", str(out_png)],
      check=True,
  )
  raw_png.unlink(missing_ok=True)
  return out_png


def render_dot_to_svg(dot_source: str) -> str:
  """Renders Graphviz DOT source to SVG string with GEOMETOR dark theme defaults."""
  if "bgcolor" not in dot_source:
    theme_defaults = """
    graph [bgcolor="transparent", pad="0.25", ranksep="0.75", nodesep="0.55", fontname="Helvetica Neue, sans-serif", fontcolor="#e5e0d5"];
    node  [shape=box, style="rounded,filled", fillcolor="#141821", color="#d4af37", penwidth=2.0, fontname="JetBrains Mono, monospace", fontcolor="#f4f1ea", fontsize=16, margin="0.26,0.18"];
    edge  [color="#64748b", penwidth=1.8, arrowsize=0.9, fontname="JetBrains Mono, monospace", fontcolor="#cbd5e1", fontsize=13];
    """
    dot_source = re.sub(
        r"(\b(?:digraph|graph)\b[^{]*\{)",
        r"\1" + theme_defaults,
        dot_source,
        count=1,
    )
  res = subprocess.run(
      ["/usr/bin/dot", "-Tsvg"],
      input=dot_source,
      capture_output=True,
      text=True,
      check=True,
  )
  svg = res.stdout
  svg = re.sub(r"<\?xml[^>]*\?>", "", svg)
  svg = re.sub(r"<!DOCTYPE[^>]*>", "", svg)
  # Remove fixed width/height pts on root <svg> so CSS scales it to fill the 1080p stage
  svg = re.sub(
      r'(<svg\b[^>]*?)\s+width="[^"]*"\s+height="[^"]*"',
      r'\1 width="100%" height="100%"',
      svg,
      count=1,
  )
  return svg.strip()


def data_uri_for_file(path: Path) -> str:
  mime, _ = mimetypes.guess_type(str(path))
  if not mime:
    mime = "application/octet-stream"
  b64 = base64.b64encode(path.read_bytes()).decode("ascii")
  return f"data:{mime};base64,{b64}"


def build_stage_html(
    scene: dict,
    slide_idx: int,
    total_slides: int,
    segment_meta: dict | None,
    work_dir: Path,
) -> Path:
  """Builds a 1920x1080 HTML stage document for a scene and returns its path."""
  kind = scene.get("type", "graphviz").lower()
  title = scene.get("title", f"Scene {slide_idx + 1}")
  subtitle = scene.get("subtitle", "")
  badge = scene.get("badge", "GEOMETOR • STAGE")
  raw_caption = scene.get("caption") or (
      segment_meta.get("text", "") if segment_meta else ""
  )
  caption = clean_caption_text(raw_caption)
  bell_key = scene.get("bell") or (
      segment_meta.get("bell") if segment_meta else None
  )
  bell_label, bell_color = BELL_META.get(
      bell_key or "", ("DIALOGUE • ARGON III-C", "#d4af37")
  )

  center_html = ""
  extra_scripts = ""

  if kind == "graphviz":
    dot_code = scene["dot"]
    svg_markup = render_dot_to_svg(dot_code)
    center_html = f'<div class="stage-vector-wrap graphviz-stage">{svg_markup}</div>'

  elif kind == "mermaid":
    # Convert literal \n inside Mermaid labels to <br/>
    mmd_raw = scene["mermaid"].replace("\\n", "<br/>")
    mmd_src = f"file://{MERMAID_JS}" if MERMAID_JS.exists() else ""
    center_html = f'<div class="mermaid-wrap"><pre class="mermaid">{mmd_raw}</pre></div>'
    extra_scripts = f"""
    <script src="{mmd_src}"></script>
    <script>
      mermaid.initialize({{
        startOnLoad: true,
        securityLevel: 'loose',
        theme: 'base',
        themeVariables: {{
          darkMode: true,
          background: 'transparent',
          primaryColor: '#161b26',
          primaryTextColor: '#f4f1ea',
          primaryBorderColor: '#d4af37',
          lineColor: '#5eead4',
          secondaryColor: '#1e293b',
          tertiaryColor: '#0f172a',
          fontFamily: 'JetBrains Mono, monospace',
          fontSize: '18px'
        }}
      }});
    </script>
    """

  elif kind == "web":
    if scene.get("screenshot_path"):
      raw_web_png = Path(scene["screenshot_path"])
    else:
      raw_web_png = work_dir / f"raw_web_{slide_idx:02d}.png"
      chrome_screenshot(
          scene["url"], raw_web_png, wait_ms=scene.get("wait_ms", 1200)
      )
    img_uri = data_uri_for_file(raw_web_png)
    full_bleed = scene.get("full_bleed", False)
    if full_bleed:
      center_html = f'<img class="web-capture-full" src="{img_uri}" />'
    else:
      url_display = html.escape(scene.get("url_label", scene.get("url", "")))
      center_html = f"""
      <div class="browser-frame">
        <div class="browser-bar">
          <span class="dot r"></span><span class="dot y"></span><span class="dot g"></span>
          <span class="browser-url">{url_display}</span>
        </div>
        <div class="browser-viewport">
          <img src="{img_uri}" />
        </div>
      </div>
      """

  elif kind == "svg":
    svg_path = Path(scene["path"])
    if svg_path.suffix.lower() == ".svg":
      raw_svg = svg_path.read_text(encoding="utf-8")
      raw_svg = re.sub(r"<\?xml[^>]*\?>", "", raw_svg)
      center_html = f'<div class="stage-vector-wrap">{raw_svg}</div>'
    else:
      img_uri = data_uri_for_file(svg_path)
      center_html = f'<div class="stage-vector-wrap"><img src="{img_uri}" /></div>'

  elif kind == "image":
    img_path = Path(scene["path"])
    img_uri = data_uri_for_file(img_path)
    center_html = f'<div class="stage-image-wrap"><img src="{img_uri}" /></div>'

  elif kind == "html":
    center_html = scene["html"]
    if D3_JS.exists():
      extra_scripts = f'<script src="file://{D3_JS}"></script>\n' + scene.get(
          "script", ""
      )

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
    background: radial-gradient(circle at 50% 38%, #151924 0%, #0b0d13 70%, #07080c 100%);
    color: #f4f1ea;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .stage-grid {{
    position: absolute;
    width: 1920px;
    height: 1080px;
    top: 0;
    left: 0;
    background-image:
      linear-gradient(rgba(212, 175, 55, 0.035) 1px, transparent 1px),
      linear-gradient(90deg, rgba(212, 175, 55, 0.035) 1px, transparent 1px);
    background-size: 60px 60px;
    pointer-events: none;
  }}
  .stage-frame {{
    position: relative;
    width: 1920px;
    height: 1080px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 34px 56px 36px 56px;
  }}
  .stage-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid rgba(212, 175, 55, 0.28);
    padding-bottom: 16px;
    z-index: 2;
  }}
  .header-left {{
    display: flex;
    align-items: baseline;
    gap: 18px;
    min-width: 0;
    overflow: hidden;
    flex: 1;
    margin-right: 20px;
  }}
  .brand-pill {{
    font-family: "JetBrains Mono", monospace;
    font-size: 13px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: #d4af37;
    background: rgba(212, 175, 55, 0.1);
    border: 1px solid rgba(212, 175, 55, 0.4);
    padding: 6px 14px;
    border-radius: 4px;
    white-space: nowrap;
  }}
  .stage-title {{
    font-family: Georgia, "Times New Roman", serif;
    font-size: 30px;
    font-weight: 400;
    color: #f8f6f0;
    letter-spacing: 0.02em;
    white-space: nowrap;
  }}
  .stage-subtitle {{
    font-size: 16px;
    color: #94a3b8;
    font-style: italic;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .header-right {{
    display: flex;
    align-items: center;
    gap: 16px;
    white-space: nowrap;
  }}
  .bell-pill {{
    font-family: "JetBrains Mono", monospace;
    font-size: 13px;
    letter-spacing: 0.12em;
    padding: 6px 14px;
    border-radius: 999px;
    border: 1px solid {bell_color};
    color: {bell_color};
    background: rgba(15, 23, 42, 0.75);
  }}
  .slide-counter {{
    font-family: "JetBrains Mono", monospace;
    font-size: 15px;
    color: #94a3b8;
  }}
  .stage-main {{
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    overflow: hidden;
    padding: 16px 0;
  }}
  .stage-vector-wrap {{
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .graphviz-stage svg {{
    width: 92% !important;
    max-height: 720px !important;
    height: auto !important;
  }}
  .stage-vector-wrap img, .stage-image-wrap img {{
    max-width: 1760px;
    max-height: 760px;
    border-radius: 10px;
    border: 1px solid rgba(212, 175, 55, 0.3);
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.65);
  }}
  .mermaid-wrap {{
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  pre.mermaid {{
    display: flex;
    align-items: center;
    justify-content: center;
    width: 90%;
    height: 100%;
  }}
  pre.mermaid svg {{
    width: 100% !important;
    max-width: 1680px !important;
    max-height: 720px !important;
    height: auto !important;
  }}
  .browser-frame {{
    width: 1680px;
    height: 760px;
    background: #0d1017;
    border: 1px solid rgba(212, 175, 55, 0.38);
    border-radius: 12px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    box-shadow: 0 25px 70px rgba(0, 0, 0, 0.75);
  }}
  .browser-bar {{
    height: 38px;
    background: #161b26;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    display: flex;
    align-items: center;
    padding: 0 16px;
    gap: 8px;
  }}
  .browser-bar .dot {{
    width: 11px;
    height: 11px;
    border-radius: 50%;
    display: inline-block;
  }}
  .browser-bar .dot.r {{ background: #ef4444; }}
  .browser-bar .dot.y {{ background: #f59e0b; }}
  .browser-bar .dot.g {{ background: #10b981; }}
  .browser-url {{
    margin-left: 14px;
    font-family: "JetBrains Mono", monospace;
    font-size: 13px;
    color: #cbd5e1;
    background: rgba(0, 0, 0, 0.35);
    padding: 3px 14px;
    border-radius: 4px;
    border: 1px solid rgba(255, 255, 255, 0.06);
  }}
  .browser-viewport {{
    flex: 1;
    overflow: hidden;
    display: flex;
    align-items: flex-start;
    justify-content: center;
    background: #111;
  }}
  .browser-viewport img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: top center;
  }}
  .stage-footer {{
    background: rgba(16, 20, 29, 0.9);
    border: 1px solid rgba(212, 175, 55, 0.28);
    border-left: 4px solid {bell_color};
    border-radius: 8px;
    padding: 16px 26px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 24px;
    z-index: 2;
  }}
  .caption-text {{
    font-family: Georgia, "Times New Roman", serif;
    font-size: 21px;
    line-height: 1.4;
    color: #f4f1ea;
  }}
  .voice-signature {{
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    letter-spacing: 0.14em;
    color: #94a3b8;
    white-space: nowrap;
    text-transform: uppercase;
  }}
</style>
</head>
<body>
  <div class="stage-grid"></div>
  <div class="stage-frame">
    <header class="stage-header">
      <div class="header-left">
        <span class="brand-pill">{html.escape(badge)}</span>
        <h1 class="stage-title">{html.escape(title)}</h1>
        <span class="stage-subtitle">{html.escape(subtitle)}</span>
      </div>
      <div class="header-right">
        <span class="bell-pill">{html.escape(bell_label)}</span>
        <span class="slide-counter">{slide_idx + 1:02d} / {total_slides:02d}</span>
      </div>
    </header>
    <main class="stage-main">
      {center_html}
    </main>
    <footer class="stage-footer">
      <div class="caption-text">“{html.escape(caption)}”</div>
      <div class="voice-signature">ARGON III-C • 1080P STAGE</div>
    </footer>
  </div>
  {extra_scripts}
</body>
</html>
"""
  html_path = work_dir / f"slide_{slide_idx:02d}.html"
  html_path.write_text(html_doc, encoding="utf-8")
  return html_path


def assemble_mp4(
    slide_pngs: list[Path],
    durations: list[float],
    audio_wav: Path | None,
    out_mp4: Path,
) -> Path:
  """Uses ffmpeg concat demuxer to build a 2560x1440 (1440p QHD) 30fps H.264 MP4 synced to audio."""
  concat_file = out_mp4.parent / f"{out_mp4.stem}_concat.txt"
  lines = []
  for png, dur in zip(slide_pngs, durations):
    lines.append(f"file '{png.resolve()}'")
    lines.append(f"duration {max(0.5, dur):.3f}")
  lines.append(f"file '{slide_pngs[-1].resolve()}'")
  concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

  vf_chain = "fps=30,scale=2560:1440:flags=lanczos,setsar=1:1,noise=alls=2:allf=t,format=yuv420p"

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
      "5.1",
      "-crf",
      "16",
      "-maxrate",
      "16M",
      "-bufsize",
      "32M",
      "-g",
      "30",
      "-keyint_min",
      "30",
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
  return out_mp4


def build_interactive_player(
    expanded_slides: list[dict],
    out_mp4: Path,
    out_html: Path,
) -> Path:
  """Builds an inline <agent-embed> 16:9 video player playing the merged 1080p MP4."""
  video_uri = data_uri_for_file(out_mp4)
  slides_data = []
  for idx, item in enumerate(expanded_slides):
    sc = item["scene"]
    seg = item["seg"] or {}
    png = item["png"]
    slides_data.append({
        "index": idx,
        "title": sc.get("title", f"Slide {idx + 1}"),
        "type": sc.get("type", "stage").upper(),
        "bell": sc.get("bell") or seg.get("bell") or "bell:context",
        "start_sec": item["start_sec"],
        "end_sec": item["end_sec"],
        "text": clean_caption_text(sc.get("caption") or seg.get("text", "")),
        "png_path": str(png.resolve()),
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
  .player-shell {{
    max-width: 100%;
    background: #11151f;
    border: 1px solid rgba(212, 175, 55, 0.35);
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.6);
  }}
  .viewport-16x9 {{
    position: relative;
    width: 100%;
    aspect-ratio: 16 / 9;
    background: #07080c;
    overflow: hidden;
  }}
  .viewport-16x9 video {{
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }}
  .controls-bar {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 10px 16px;
    background: #161b26;
    border-top: 1px solid rgba(212, 175, 55, 0.22);
    flex-wrap: wrap;
  }}
  .thumb-strip {{
    display: flex;
    gap: 8px;
    padding: 10px 16px;
    background: #0d1017;
    border-top: 1px solid rgba(255, 255, 255, 0.06);
    overflow-x: auto;
  }}
  .thumb-btn {{
    background: #161b26;
    border: 1px solid rgba(212, 175, 55, 0.25);
    color: #cbd5e1;
    padding: 6px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-family: monospace;
    font-size: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
    white-space: nowrap;
    transition: all 0.15s ease;
  }}
  .thumb-btn.active {{
    border-color: #d4af37;
    background: rgba(212, 175, 55, 0.16);
    color: #fff;
  }}
  .meta-tag {{
    font-family: monospace;
    font-size: 12px;
    color: #d4af37;
  }}
</style>
</head>
<body>
  <div class="player-shell">
    <div class="viewport-16x9">
      <video id="stage-video" controls preload="metadata" src="{video_uri}"></video>
    </div>
    <div class="controls-bar">
      <span class="meta-tag" id="slide-label">01 / {len(slides_data):02d} • Merged 1080p MP4 (H.264 + AAC 24kHz)</span>
      <span class="meta-tag">{html.escape(out_mp4.name)}</span>
    </div>
    <div class="thumb-strip" id="thumb-strip"></div>
  </div>
  <script>
    const SLIDES = {slides_json};
    const videoEl = document.getElementById('stage-video');
    const labelEl = document.getElementById('slide-label');
    const stripEl = document.getElementById('thumb-strip');
    let currentIdx = -1;

    function updateActive(idx) {{
      if (idx < 0 || idx >= SLIDES.length || idx === currentIdx) return;
      currentIdx = idx;
      const s = SLIDES[idx];
      labelEl.textContent = `${{String(idx + 1).padStart(2, '0')}} / ${{String(SLIDES.length).padStart(2, '0')}} • [${{s.type}}] ${{s.title}}`;
      document.querySelectorAll('.thumb-btn').forEach((b, i) => {{
        b.classList.toggle('active', i === idx);
      }});
    }}

    SLIDES.forEach((s, i) => {{
      const btn = document.createElement('button');
      btn.className = 'thumb-btn';
      btn.textContent = `${{i + 1}}. [${{s.type}}] ${{s.title}} (${{s.start_sec.toFixed(1)}}s)`;
      btn.onclick = () => {{
        videoEl.currentTime = s.start_sec + 0.05;
        videoEl.play();
        updateActive(i);
      }};
      stripEl.appendChild(btn);
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


def render_stage_sequence(
    scenes_path: Path,
    manifest_path: Path,
    output_dir: Path,
    prefix: str = "turn",
    play_video: bool = False,
) -> dict:
  scenes = json.loads(scenes_path.read_text(encoding="utf-8"))
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  segments = manifest.get("segments", [])
  audio_wav = Path(manifest["audio_wav"])

  output_dir.mkdir(parents=True, exist_ok=True)
  work_dir = output_dir / f"{prefix}_work"
  work_dir.mkdir(parents=True, exist_ok=True)

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

  slide_pngs = []
  durations = []

  total_frames = len(expanded_specs)
  for f_idx, item in enumerate(expanded_specs):
    sc = item["scene"]
    seg = item["seg"]
    dur = item["duration"]
    durations.append(dur)

    html_path = build_stage_html(sc, f_idx, total_frames, seg, work_dir)
    out_png = output_dir / f"{prefix}_slide_{f_idx:02d}.png"
    wait_ms = 1200 if sc.get("type") == "mermaid" else 600
    chrome_screenshot(str(html_path), out_png, wait_ms=wait_ms)
    item["png"] = out_png
    slide_pngs.append(out_png)
    print(f"Rendered 1080p slide {f_idx + 1}/{total_frames} ({dur:.2f}s): {out_png}")

  out_mp4 = output_dir / f"{prefix}.mp4"
  assemble_mp4(slide_pngs, durations, audio_wav, out_mp4)
  print(f"Assembled 1080p MP4:  {out_mp4}")

  out_player = output_dir / f"{prefix}_stage.html"
  build_interactive_player(expanded_specs, out_mp4, out_player)
  print(f"Interactive Player:   {out_player}")

  if play_video and Path("/usr/bin/mpv").exists():
    subprocess.run(["/usr/bin/mpv", "--keep-open=no", str(out_mp4)], check=False)

  return {
      "slides": [str(p) for p in slide_pngs],
      "mp4": str(out_mp4),
      "player_html": str(out_player),
  }


def main() -> None:
  parser = argparse.ArgumentParser(
      description="Render 1920x1080 stage graphics and sequence with speak.py audio."
  )
  parser.add_argument(
      "-s",
      "--scenes",
      type=Path,
      required=True,
      help="Path to scenes JSON file.",
  )
  parser.add_argument(
      "-m",
      "--manifest",
      type=Path,
      required=True,
      help="Path to <turn>.json timing manifest emitted by speak.py.",
  )
  parser.add_argument(
      "-o",
      "--output-dir",
      type=Path,
      required=True,
      help="Directory for 1080p PNG slides, MP4 video, and interactive stage HTML.",
  )
  parser.add_argument(
      "-p",
      "--prefix",
      default="stage_demo",
      help="Filename prefix for generated outputs.",
  )
  parser.add_argument(
      "--play",
      action="store_true",
      help="Play the merged 1080p MP4 via mpv after rendering.",
  )
  args = parser.parse_args()
  render_stage_sequence(
      args.scenes, args.manifest, args.output_dir, args.prefix, args.play
  )


if __name__ == "__main__":
  main()
