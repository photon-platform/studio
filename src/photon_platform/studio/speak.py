"""Unified script parser and Argon III-C TTS integration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


DEFAULT_SPEAK_SCRIPT = Path("/home/phi/.gemini/config/skills/dialogue/scripts/speak.py")
DEFAULT_VOICE_PROFILE = Path("/home/phi/.gemini/config/skills/dialogue/voice_profile.json")


@dataclass
class ScreenCue:
    action: str
    svg: str = ""
    weight: float = 1.0
    punchline: bool = False
    directives: dict[str, list[str]] = field(default_factory=dict)
    say_text: str = ""


@dataclass
class BellBlock:
    bell: str
    style: str = ""
    screens: list[ScreenCue] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)


def parse_unified_script(script_path: Path) -> dict[str, Any]:
    """Parse both 5-bell narrative scripts (Elements) and [screen:...] DSL scripts (ETC)."""
    text = Path(script_path).read_text(encoding="utf-8")
    header: dict[str, str] = {}
    bells: list[BellBlock] = []
    current_bell: BellBlock | None = None
    current_screen: ScreenCue | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        bell_match = re.match(r"^\[bell:([^\]]+)\]\s*(.*)$", line)
        if bell_match:
            bell_name = bell_match.group(1).strip()
            rest = bell_match.group(2).strip()
            current_bell = BellBlock(bell=bell_name)
            bells.append(current_bell)
            current_screen = None
            if rest:
                style_m = re.match(r"^\[style:([^\]]+)\]\s*(.*)$", rest)
                if style_m:
                    current_bell.style = style_m.group(1).strip()
                    if style_m.group(2).strip():
                        current_bell.paragraphs.append(style_m.group(2).strip())
                else:
                    current_bell.paragraphs.append(rest)
            continue

        style_match = re.match(r"^\[style:([^\]]+)\]\s*(.*)$", line)
        if style_match and current_bell is not None:
            current_bell.style = style_match.group(1).strip()
            if style_match.group(2).strip():
                current_bell.paragraphs.append(style_match.group(2).strip())
            continue

        screen_match = re.match(r"^\[screen:([^\]]+)\]$", line)
        if screen_match:
            if current_bell is None:
                current_bell = BellBlock(bell="proposition")
                bells.append(current_bell)
            parts = [p.strip() for p in screen_match.group(1).split("|")]
            action = parts[0] if parts else "STEP"
            svg_name = ""
            weight = 1.0
            punchline = False
            for part in parts[1:]:
                if ":" in part:
                    k, v = [x.strip() for x in part.split(":", 1)]
                    if k == "svg":
                        svg_name = v
                    elif k == "weight":
                        weight = float(v)
                    elif k == "punchline":
                        punchline = v.lower() in ("true", "1", "yes")
            current_screen = ScreenCue(
                action=action, svg=svg_name, weight=weight, punchline=punchline
            )
            current_bell.screens.append(current_screen)
            continue

        if current_screen is not None and ":" in line:
            k, v = [x.strip() for x in line.split(":", 1)]
            if k == "say":
                current_screen.say_text = v
            else:
                current_screen.directives.setdefault(k, []).append(v)
            continue

        if current_bell is None and ":" in line and not line.startswith("["):
            k, v = [x.strip() for x in line.split(":", 1)]
            header[k] = v
            continue

        if current_bell is not None:
            current_bell.paragraphs.append(line)

    return {
        "script_path": str(script_path),
        "header": header,
        "bells": [asdict(b) for b in bells],
    }


def synthesize_script(
    script_path: Path,
    output_wav: Path,
    speak_script: Path = DEFAULT_SPEAK_SCRIPT,
) -> dict[str, Any]:
    """Run Argon III-C TTS synthesis via speak.py and return paths to .wav and .json manifest."""
    script_path = Path(script_path).resolve()
    output_wav = Path(output_wav).resolve()
    manifest_json = output_wav.with_suffix(".json")

    cmd = [
        sys.executable,
        str(speak_script),
        "-f",
        str(script_path),
        "-o",
        str(output_wav),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return {
        "status": "ok" if res.returncode == 0 else "error",
        "returncode": res.returncode,
        "wav_path": str(output_wav),
        "manifest_json": str(manifest_json),
        "stdout": res.stdout.strip(),
        "stderr": res.stderr.strip(),
    }
