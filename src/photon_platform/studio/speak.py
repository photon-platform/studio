#!/usr/bin/env python3
"""PHOTON Studio Voice Synthesizer, Multi-Character Cast & Stage Timing Engine.

Synthesizes spoken narration and multi-character dialogue turns using Argon III-C
(`voice_xdhacg1pn1cq`) or inline `[voice: <voice_id>]` overrides on Gemini TTS,
weaving in harmonic crystal chime indicators (`[bell:...]`), sound cues (`[sfx:...]`),
automatic pacing shifts (`[style:...]`), `.tts_cache/` SHA-256 PCM caching, and
emitting a frame-accurate JSON timing manifest (`<output>.json`).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import urllib.error
import urllib.request
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

STUDIO_PKG_DIR = Path(__file__).resolve().parent
PROFILE_PATH = STUDIO_PKG_DIR / "voice_profile.json"
SAMPLE_RATE = 24000

DOUBLE_PAUSE_RE = re.compile(
    r"(?:<\s*pause\s*>\s*){2,}|<\s*long\s+pause\s*>", flags=re.I
)
SINGLE_PAUSE_RE = re.compile(r"<\s*(?:short\s+)?pause\s*>", flags=re.I)
BELL_SPLIT_RE = re.compile(r"\[(bell:[a-z_]+)\]", re.I)
STYLE_SPLIT_RE = re.compile(r"\[style:([a-z_]+)\]", re.I)
VOICE_TAG_RE = re.compile(r"\[voice:\s*([^\]]+)\]", re.I)
SFX_RE = re.compile(r"\[sfx:([a-z0-9_]+)\]", flags=re.I)

SFX_FREQS: dict[str, tuple[list[float], float, float]] = {
    "point": ([1046.5, 1567.98], 0.14, 0.10),
    "segment": ([440.0, 659.25, 880.0], 0.22, 0.11),
    "line": ([440.0, 659.25, 880.0], 0.22, 0.11),
    "circle": ([392.0, 587.33, 783.99, 1174.66], 0.30, 0.11),
    "intersect": ([880.0, 1318.51], 0.18, 0.12),
    "concur": ([523.25, 659.25, 783.99, 1046.5], 0.26, 0.12),
    "equal": ([523.25, 783.99], 0.24, 0.12),
    "ratio": ([493.88, 739.99, 987.77], 0.24, 0.11),
    "conjugate": ([659.25, 523.25, 783.99], 0.25, 0.11),
    "invariant": ([349.23, 523.25, 698.46, 1046.5], 0.32, 0.12),
    "qed": ([523.25, 659.25, 783.99, 1046.5], 0.38, 0.14),
}


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


def load_profile(profile_path: Path | None = None) -> dict[str, Any]:
    path = Path(profile_path) if profile_path else PROFILE_PATH
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_api_key() -> str:
    """Resolve GEMINI_API_KEY or GOOGLE_API_KEY from environment or ~/.photon/.env."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        env_file = Path.home() / ".photon" / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip().removeprefix("export ").strip()
                if stripped.startswith(("GEMINI_API_KEY=", "GOOGLE_API_KEY=")):
                    api_key = stripped.split("=", 1)[1].strip().strip("'\"")
                    os.environ["GEMINI_API_KEY"] = api_key
                    break
    if not api_key:
        raise RuntimeError("Neither GEMINI_API_KEY nor GOOGLE_API_KEY is set")
    return api_key


def resolve_voice_id(raw_spec: str, profile: dict[str, Any]) -> tuple[str, str | None]:
    """Resolve `[voice: <id_or_alias> | style: <style>]` against profile cast or literal ID."""
    parts = [p.strip() for p in raw_spec.split("|") if p.strip()]
    raw_voice = parts[0] if parts else profile["voice_id"]
    override_style: str | None = None
    for part in parts[1:]:
        if ":" in part:
            k, v = part.split(":", 1)
            if k.strip().lower() == "style":
                st_req = v.strip()
                override_style = profile.get("styles", {}).get(st_req.lower(), st_req)
    cast = profile.get("voices", {})
    if raw_voice in cast:
        entry = cast[raw_voice]
        if isinstance(entry, dict):
            return entry.get("voice_id", raw_voice), override_style or entry.get("style")
        return str(entry), override_style
    return raw_voice, override_style


def extract_voice_cues(
    text: str, profile: dict[str, Any], default_voice_id: str, default_style: str
) -> tuple[str, str, str]:
    """Extract optional `[voice:...]` tag from a phrase, returning `(voice_id, style_str, cleaned_text)`."""
    voice_id = default_voice_id
    style_str = default_style
    m = VOICE_TAG_RE.search(text)
    if m:
        v_id, st_override = resolve_voice_id(m.group(1), profile)
        voice_id = v_id
        if st_override:
            style_str = st_override
        text = VOICE_TAG_RE.sub("", text)
        text = re.sub(r"\s+", " ", text).strip()
    return voice_id, style_str, text


def apply_pronunciations(text: str, pronunciations: dict[str, str]) -> str:
    text = re.sub(r"([.!?,;:])\s*<\s*(?:short\s+|long\s+)?pause\s*>", r"\1 ", text, flags=re.I)
    text = SINGLE_PAUSE_RE.sub(", ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for src, dst in pronunciations.items():
        text = re.sub(rf"\b{re.escape(src)}\b", dst, text)
    return text


def api_post(url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def synth_pcm_bytes(
    voice_id: str, text: str, style: str, model: str, api_key: str
) -> bytes:
    payload = {
        "model": model,
        "input": [
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                        "annotations": [
                            {
                                "type": "speech_metadata",
                                "style": style,
                            }
                        ],
                    }
                ],
            }
        ],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice_id}]},
    }
    resp = api_post(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        payload,
        api_key,
    )
    for step in resp.get("steps", []):
        if step.get("type") == "model_output":
            for content in step.get("content", []):
                if content.get("type") == "audio":
                    raw_wav = base64.b64decode(content["data"])
                    with wave.open(io.BytesIO(raw_wav), "rb") as wf:
                        return wf.readframes(wf.getnframes())
    raise RuntimeError(f"No audio returned for {voice_id}")


def synth_pcm_cached(
    voice_id: str,
    text: str,
    style: str,
    model: str,
    api_key: str,
    cache_dir: Path | None = None,
) -> bytes:
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(f"{model}|{voice_id}|{style}|{text}".encode("utf-8")).hexdigest()[:24]
        cache_file = cache_dir / f"{key}.pcm"
        if cache_file.exists() and cache_file.stat().st_size > 0:
            return cache_file.read_bytes()
        pcm = synth_pcm_bytes(voice_id, text, style, model, api_key)
        cache_file.write_bytes(pcm)
        return pcm
    return synth_pcm_bytes(voice_id, text, style, model, api_key)


def make_harmonic_bell_pcm(
    freqs: list[float],
    duration: float = 0.50,
    amp: float = 0.15,
    sample_rate: int = SAMPLE_RATE,
) -> bytes:
    n_samples = int(sample_rate * duration)
    samples = []
    weights = [1.0 / (i + 1) for i in range(len(freqs))]
    w_sum = sum(weights)
    for i in range(n_samples):
        t = i / sample_rate
        attack = min(1.0, t / 0.006)
        decay = math.exp(-6.0 * t)
        val = (
            sum(w * math.sin(2.0 * math.pi * f * t) for w, f in zip(weights, freqs))
            / w_sum
        )
        s = int(max(-1.0, min(1.0, val * attack * decay * amp)) * 32767)
        samples.append(struct.pack("<h", s))
    return b"".join(samples)


def silence_pcm(duration: float = 0.10, sample_rate: int = SAMPLE_RATE) -> bytes:
    return b"\x00\x00" * int(sample_rate * duration)


def extract_sfx_cues(text: str) -> tuple[list[str], str]:
    cues = [m.group(1).lower() for m in SFX_RE.finditer(text)]
    cleaned = SFX_RE.sub("", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cues, cleaned


def make_sfx_pcm(cue: str) -> bytes:
    spec = SFX_FREQS.get(cue.lower())
    if not spec:
        return b""
    freqs, dur, amp = spec
    return make_harmonic_bell_pcm(freqs, duration=dur, amp=amp)


def parse_unified_script(script_path: Path) -> dict[str, Any]:
    """Parse both 5-bell narrative scripts (Elements) and `[screen:...]` DSL scripts (ETC) into a structured dict."""
    text = Path(script_path).read_text(encoding="utf-8")
    header: dict[str, str] = {}
    bells: list[BellBlock] = []
    current_bell: BellBlock | None = None
    current_screen: ScreenCue | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"^===+\s*(.+?)\s*===+$", line):
            continue

        bell_match = re.match(r"^\[bell:([^\]]+)\]\s*(.*)$", line, flags=re.I)
        if bell_match:
            bell_name = bell_match.group(1).strip()
            rest = bell_match.group(2).strip()
            current_bell = BellBlock(bell=bell_name)
            bells.append(current_bell)
            current_screen = None
            if rest:
                style_m = re.match(r"^\[style:([^\]]+)\]\s*(.*)$", rest, flags=re.I)
                if style_m:
                    current_bell.style = style_m.group(1).strip()
                    if style_m.group(2).strip():
                        current_bell.paragraphs.append(style_m.group(2).strip())
                else:
                    current_bell.paragraphs.append(rest)
            continue

        style_match = re.match(r"^\[style:([^\]]+)\]\s*(.*)$", line, flags=re.I)
        if style_match and current_bell is not None:
            current_bell.style = style_match.group(1).strip()
            if style_match.group(2).strip():
                current_bell.paragraphs.append(style_match.group(2).strip())
            continue

        screen_match = re.match(r"^\[screen:([^\]]+)\]$", line, flags=re.I)
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
                    if k.lower() == "svg":
                        svg_name = v
                    elif k.lower() == "weight" and v.lower() != "auto":
                        weight = float(v)
                    elif k.lower() == "punchline":
                        punchline = v.lower() in ("true", "1", "yes")
            current_screen = ScreenCue(
                action=action, svg=svg_name, weight=weight, punchline=punchline
            )
            current_bell.screens.append(current_screen)
            continue

        if current_screen is not None and ":" in line:
            k, v = [x.strip() for x in line.split(":", 1)]
            if k.lower() == "say":
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


def _strip_screen_directives(script_text: str) -> str:
    """Extract only `[bell:...]`, `[style:...]`, `[voice:...]` tags and spoken `say:` lines."""
    if not (
        re.search(r"^\s*\[screen\s*:", script_text, re.I | re.M)
        or re.search(r"^\s*say\s*:", script_text, re.I | re.M)
    ):
        return script_text
    kept: list[str] = []
    for raw_line in script_text.splitlines():
        s = raw_line.strip()
        if not s or s.startswith("#"):
            continue
        if s.lower().startswith(("[bell:", "[style:", "[voice:")):
            kept.append(s)
            continue
        m_say = re.match(r"^say\s*:\s*(.+)$", s, flags=re.I)
        if m_say:
            kept.append(m_say.group(1).strip())
    return "\n".join(kept)


def parse_script_segments(
    script_text: str, profile: dict[str, Any]
) -> list[tuple[str | None, list[tuple[str, str]]]]:
    """Split script text on `[bell:...]` boundaries and inline `[style:...]` phrases."""
    script_text = _strip_screen_directives(script_text)
    styles = profile["styles"]
    default_key = profile.get("default_style", "brisk")
    bells_cfg = profile.get("bells", {})

    raw_blocks: list[tuple[str | None, str]] = []
    pending_bell = None
    pos = 0
    for m in BELL_SPLIT_RE.finditer(script_text):
        chunk = script_text[pos : m.start()].strip()
        if chunk:
            raw_blocks.append((pending_bell, chunk))
            pending_bell = None
        pending_bell = m.group(1).lower()
        pos = m.end()
    tail = script_text[pos:].strip()
    if tail:
        raw_blocks.append((pending_bell, tail))

    segments: list[tuple[str | None, list[tuple[str, str]]]] = []
    for bell_key, block_text in raw_blocks:
        bell_cfg = bells_cfg.get(bell_key or "", {})
        cur_style_key = bell_cfg.get("auto_style", default_key)
        if cur_style_key not in styles:
            cur_style_key = default_key

        raw_style_phrases: list[tuple[str, str]] = []
        s_pos = 0
        for sm in STYLE_SPLIT_RE.finditer(block_text):
            p_chunk = block_text[s_pos : sm.start()].strip()
            req_key = sm.group(1).lower()
            if p_chunk and not re.search(r"[.!?:;>]\s*$", p_chunk):
                continue
            if p_chunk:
                raw_style_phrases.append((styles[cur_style_key], p_chunk))
            if req_key in styles:
                cur_style_key = req_key
            s_pos = sm.end()
        s_tail = STYLE_SPLIT_RE.sub("", block_text[s_pos:]).strip()
        if s_tail:
            raw_style_phrases.append((styles[cur_style_key], s_tail))

        phrases: list[tuple[str, str]] = []
        active_voice_tag = ""
        for st_val, chunk_str in raw_style_phrases:
            for sub_p in DOUBLE_PAUSE_RE.split(chunk_str):
                sub_p = sub_p.strip()
                if not sub_p:
                    continue
                v_only = re.match(r"^(\[voice:[^\]]+\])\s*(.*)$", sub_p, flags=re.I)
                if v_only:
                    active_voice_tag = v_only.group(1)
                    rem = v_only.group(2).strip()
                    if not rem:
                        continue
                    sub_p = f"{active_voice_tag} {rem}"
                elif active_voice_tag and "[voice:" not in sub_p.lower():
                    sub_p = f"{active_voice_tag} {sub_p}"
                phrases.append((st_val, sub_p))

        if phrases:
            segments.append((bell_key, phrases))

    return segments


def parse_structured_scr_segments(
    script_text: str, profile: dict[str, Any], variant: str = "16x9"
) -> list[tuple[str | None, list[tuple[str, str]]]] | None:
    """Parse structured `.scr` scripts (`[screen: ...]`, `=== <section> ===`, `[review: ...]`) into timed phrase blocks."""
    if not (
        re.search(r"^\s*\[screen\s*:", script_text, re.I | re.M)
        or re.search(r"^\s*===+\s*.+?\s*===+\s*$", script_text, re.M)
    ):
        return None

    is_short = variant in ("9x16", "short", "short_9x16")
    styles = profile["styles"]
    default_key = profile.get("default_style", "brisk")
    bells_cfg = profile.get("bells", {})

    sections: list[dict[str, Any]] = []
    cur_sec: dict[str, Any] | None = None
    cur_style_key = default_key
    cur_voice_tag = ""
    cur_screen: dict[str, Any] | None = None

    def _ensure_sec() -> dict[str, Any]:
        nonlocal cur_sec
        if cur_sec is None:
            cur_sec = {"bell": None, "phrases": []}
            sections.append(cur_sec)
        return cur_sec

    core_sfx_phrases: list[str] = []
    in_non_core = False

    def _flush_scr() -> None:
        nonlocal cur_screen
        if not cur_screen:
            return
        only_val = str(cur_screen.get("only", "")).lower()
        if is_short and only_val in ("wide", "16x9", "widescreen"):
            cur_screen = None
            return
        if (not is_short) and only_val in ("short", "9x16", "vertical"):
            cur_screen = None
            return
        if is_short and cur_screen.get("say_short"):
            say_str = " ".join(cur_screen["say_short"]).strip()
        elif (not is_short) and cur_screen.get("say_wide"):
            say_str = " ".join(cur_screen["say_wide"]).strip()
        else:
            say_str = " ".join(cur_screen.get("say", [])).strip()
        if say_str:
            v_tag = cur_screen.get("voice_tag", "")
            if v_tag and "[voice:" not in say_str.lower():
                say_str = f"{v_tag} {say_str}"
            sec = _ensure_sec()
            sec["phrases"].append((cur_screen["style"], say_str))
            if not cur_screen["in_non_core"]:
                cues, _ = extract_sfx_cues(say_str)
                sfx_str = " ".join(f"[sfx:{c}]" for c in cues) if cues else "[sfx:point]"
                core_sfx_phrases.append(sfx_str)
        cur_screen = None

    for raw_line in script_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m_sec = re.match(r"^===+\s*(.+?)\s*===+$", line)
        if m_sec:
            _flush_scr()
            sec_name = m_sec.group(1).strip().lower()
            in_non_core = sec_name in ("intro", "review", "outro")
            cur_sec = {"bell": None, "phrases": []}
            sections.append(cur_sec)
            cur_style_key = default_key
            continue
        m_scr = re.match(r"^\[screen\s*:\s*(.+)\]$", line, flags=re.I)
        if m_scr:
            _flush_scr()
            parts = [p.strip() for p in m_scr.group(1).split("|") if p.strip()]
            sk = parts[0].upper() if parts else "CONSTRUCT"
            only_attr = ""
            scr_voice_tag = cur_voice_tag
            for p in parts[1:]:
                if ":" in p:
                    k, v = p.split(":", 1)
                    kl_attr = k.strip().lower()
                    if kl_attr == "only":
                        only_attr = v.strip().lower()
                    elif kl_attr == "widescreen_only" and v.strip().lower() in ("true", "1", "yes"):
                        only_attr = "wide"
                    elif kl_attr == "voice":
                        scr_voice_tag = f"[voice:{v.strip()}]"
            in_non_core = sk in ("INTRO", "REVIEW", "REVIEW_AUTO", "OUTRO")
            cur_screen = {
                "step_kind": sk,
                "only": only_attr,
                "in_non_core": in_non_core,
                "style": styles[cur_style_key],
                "voice_tag": scr_voice_tag,
                "say": [],
                "say_wide": [],
                "say_short": [],
            }
            continue
        m_rev = re.match(r"^\[review\s*:\s*(.+)\]$", line, flags=re.I)
        if m_rev:
            _flush_scr()
            parts = [p.strip() for p in m_rev.group(1).split("|") if p.strip()]
            pace_val = 0.85
            for p in parts[1:]:
                if ":" in p:
                    k, v = p.split(":", 1)
                    if k.strip().lower() == "pace":
                        pace_val = float(v.strip().rstrip("s"))
            sec = _ensure_sec()
            if not sec.get("bell"):
                sec["bell"] = "bell:construction"
            for r_i, c_sfx in enumerate(core_sfx_phrases):
                dur = round(pace_val * 1.45 if r_i == len(core_sfx_phrases) - 1 else pace_val, 3)
                sec["phrases"].append((f"HOLD:{dur}", c_sfx))
            continue
        m_bell = re.match(r"^\[(bell:[a-z_]+)\]$", line, flags=re.I)
        if m_bell:
            _flush_scr()
            b_key = m_bell.group(1).lower()
            if cur_sec is not None and cur_sec["phrases"]:
                cur_sec = {"bell": b_key, "phrases": []}
                sections.append(cur_sec)
            else:
                sec = _ensure_sec()
                sec["bell"] = b_key
            bell_cfg = bells_cfg.get(b_key, {})
            cur_style_key = bell_cfg.get("auto_style", default_key)
            if cur_style_key not in styles:
                cur_style_key = default_key
            continue
        m_style = re.match(r"^\[style:([a-z_]+)\]$", line, flags=re.I)
        if m_style:
            req = m_style.group(1).lower()
            if req in styles:
                cur_style_key = req
                if cur_screen is not None:
                    cur_screen["style"] = styles[cur_style_key]
            continue
        m_voice = re.match(r"^(\[voice:[^\]]+\])$", line, flags=re.I)
        if m_voice:
            cur_voice_tag = m_voice.group(1)
            if cur_screen is not None:
                cur_screen["voice_tag"] = cur_voice_tag
            continue
        if cur_screen is not None and ":" in line:
            k_raw, v_raw = line.split(":", 1)
            kl = k_raw.strip().lower()
            vl = v_raw.strip()
            if kl == "only":
                cur_screen["only"] = vl.lower()
            elif kl == "voice":
                cur_screen["voice_tag"] = f"[voice:{vl}]"
            elif kl == "say":
                cur_screen["say"].append(vl)
            elif kl in ("say_wide", "say_16x9"):
                cur_screen["say_wide"].append(vl)
            elif kl in ("say_short", "say_9x16"):
                cur_screen["say_short"].append(vl)
            continue
        m_say = re.match(r"^say\s*:\s*(.+)$", line, flags=re.I)
        if m_say:
            say_str = m_say.group(1).strip()
            if cur_voice_tag and "[voice:" not in say_str.lower():
                say_str = f"{cur_voice_tag} {say_str}"
            sec = _ensure_sec()
            sec["phrases"].append((styles[cur_style_key], say_str))
            if not in_non_core:
                cues, _ = extract_sfx_cues(say_str)
                sfx_str = " ".join(f"[sfx:{c}]" for c in cues) if cues else "[sfx:point]"
                core_sfx_phrases.append(sfx_str)

    _flush_scr()
    out: list[tuple[str | None, list[tuple[str, str]]]] = []
    for sec in sections:
        if sec["phrases"]:
            out.append((sec["bell"], sec["phrases"]))
    return out if out else None


def synthesize_script(
    script_text_or_path: str | Path,
    output_wav: Path | None = None,
    variant: str = "16x9",
    out_dir: Path | None = None,
    prefix: str | None = None,
) -> tuple[Path, Path]:
    """Synthesize a `.scr` / `.txt` script (or raw script string) into `<prefix>.wav` and `<prefix>.json`."""
    api_key = ensure_api_key()

    if isinstance(script_text_or_path, Path) or (
        isinstance(script_text_or_path, str)
        and "\n" not in script_text_or_path
        and Path(script_text_or_path).exists()
    ):
        scr_path = Path(script_text_or_path).resolve()
        if scr_path.is_dir():
            scr_candidate = scr_path / "script.scr"
            scr_path = scr_candidate if scr_candidate.exists() else (scr_path / "script.txt")
        script_text = scr_path.read_text(encoding="utf-8")
        target_dir = Path(out_dir).resolve() if out_dir else scr_path.parent
        pfx = prefix or (output_wav.stem if output_wav else scr_path.parent.name)
        if output_wav is None:
            output_wav = target_dir / f"{pfx}.wav"
    else:
        script_text = str(script_text_or_path)
        if output_wav is None:
            raise ValueError("output_wav is required when passing raw script text")
        output_wav = Path(output_wav).resolve()

    profile = load_profile()
    default_voice_id = profile["voice_id"]
    model = profile.get("model", "gemini-3.8-flash-tts")
    pronunciations = profile.get("pronunciations", {})
    bells_cfg = profile.get("bells", {})

    segments = parse_structured_scr_segments(
        script_text, profile, variant=variant
    ) or parse_script_segments(script_text, profile)
    cache_dir = output_wav.parent / ".tts_cache"
    if not segments:
        raise ValueError("No spoken text found in script")

    spoken_bak = output_wav.with_name(f"{output_wav.stem}_spoken.wav")
    if spoken_bak.exists():
        spoken_bak.unlink(missing_ok=True)

    pcm_chunks: list[bytes] = []
    timing_segments: list[dict[str, Any]] = []
    cursor_samples = 0

    for idx, (bell_key, phrases) in enumerate(segments):
        seg_start_samples = cursor_samples
        seg_chunks: list[bytes] = []
        if idx > 0:
            seg_chunks.append(silence_pcm(0.14))
        if bell_key and bell_key in bells_cfg:
            b = bells_cfg[bell_key]
            seg_chunks.append(
                make_harmonic_bell_pcm(
                    b["freqs"],
                    duration=b.get("duration", 0.45),
                    amp=b.get("amp", 0.15),
                )
            )
            seg_chunks.append(silence_pcm(0.08))

        intro_samples = sum(len(c) // 2 for c in seg_chunks)
        sub_cursor = seg_start_samples
        phrase_texts: list[str] = []
        phrase_styles: list[str] = []
        subsegments: list[dict[str, Any]] = []

        for p_idx, (style_str, raw_phrase) in enumerate(phrases):
            is_hold_only = style_str.startswith("HOLD:")
            hold_target_sec = float(style_str.split(":", 1)[1]) if is_hold_only else 3.2
            sub_start = sub_cursor
            if p_idx == 0 and intro_samples > 0:
                sub_cursor += intro_samples
            elif p_idx > 0 and not is_hold_only:
                sil = silence_pcm(0.42)
                seg_chunks.append(sil)
                sub_cursor += len(sil) // 2

            sfx_cues, phrase_no_sfx = extract_sfx_cues(raw_phrase)
            phrase_voice_id, eff_style, phrase_no_voice = extract_voice_cues(
                phrase_no_sfx, profile, default_voice_id, style_str
            )
            for cue in sfx_cues:
                sfx_bytes = make_sfx_pcm(cue)
                if sfx_bytes:
                    seg_chunks.append(sfx_bytes)
                    sub_cursor += len(sfx_bytes) // 2

            clean_text = apply_pronunciations(phrase_no_voice, pronunciations)
            if clean_text and not is_hold_only:
                pcm = synth_pcm_cached(
                    phrase_voice_id,
                    clean_text,
                    eff_style,
                    model,
                    api_key,
                    cache_dir=cache_dir,
                )
            else:
                elapsed_sub = (sub_cursor - sub_start) / SAMPLE_RATE
                rem_sec = max(0.20, hold_target_sec - elapsed_sub)
                pcm = silence_pcm(rem_sec)
            seg_chunks.append(pcm)
            sub_cursor += len(pcm) // 2

            sub_clean_caption = SINGLE_PAUSE_RE.sub(" ", phrase_no_voice)
            sub_clean_caption = re.sub(r"\s+", " ", sub_clean_caption).strip()
            subsegments.append(
                {
                    "index": p_idx,
                    "voice_id": phrase_voice_id,
                    "text": sub_clean_caption or " ".join(f"[sfx:{c}]" for c in sfx_cues),
                    "start_sec": round(sub_start / SAMPLE_RATE, 3),
                    "end_sec": round(sub_cursor / SAMPLE_RATE, 3),
                    "duration_sec": round((sub_cursor - sub_start) / SAMPLE_RATE, 3),
                }
            )
            phrase_texts.append(sub_clean_caption)
            phrase_styles.append("harmonic_review" if is_hold_only else eff_style)

        seg_bytes = b"".join(seg_chunks)
        seg_samples = len(seg_bytes) // 2
        cursor_samples += seg_samples
        pcm_chunks.append(seg_bytes)

        timing_segments.append(
            {
                "index": idx,
                "bell": bell_key,
                "style": " -> ".join(dict.fromkeys(phrase_styles)),
                "text": " ".join(phrase_texts),
                "start_sec": round(seg_start_samples / SAMPLE_RATE, 3),
                "end_sec": round(cursor_samples / SAMPLE_RATE, 3),
                "duration_sec": round(seg_samples / SAMPLE_RATE, 3),
                "subsegments": subsegments,
            }
        )

    output_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(pcm_chunks))

    manifest_path = output_wav.with_suffix(".json")
    manifest_path.write_text(
        json.dumps(
            {
                "voice_id": default_voice_id,
                "audio_wav": str(output_wav),
                "total_duration_sec": round(cursor_samples / SAMPLE_RATE, 3),
                "segments": timing_segments,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return output_wav, manifest_path


def play_wav(wav_path: Path) -> None:
    for player in ("pw-play", "paplay", "aplay"):
        exe = shutil.which(player)
        if exe:
            subprocess.run([exe, str(wav_path)], check=False)
            return


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synthesize and optionally play a PHOTON Studio script."
    )
    parser.add_argument(
        "text",
        nargs="?",
        help="Spoken script text with optional [bell:...], [style:...], and [voice:...] tags.",
    )
    parser.add_argument("-f", "--file", type=Path, help="Read script text from file.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Output WAV file path.",
    )
    parser.add_argument(
        "--variant",
        choices=["16x9", "9x16"],
        default="16x9",
        help="Target aspect variant when parsing structured .scr files.",
    )
    parser.add_argument(
        "--play",
        action="store_true",
        help="Play the synthesized WAV over system audio after rendering.",
    )
    args = parser.parse_args()

    src: str | Path | None = args.file if args.file else args.text
    if not src:
        raise SystemExit("Provide script text via positional arg or --file")

    out_wav, out_json = synthesize_script(src, args.output, variant=args.variant)
    print(f"Synthesized: {out_wav}")
    print(f"Manifest:    {out_json}")
    if args.play:
        play_wav(out_wav)


if __name__ == "__main__":
    main()
