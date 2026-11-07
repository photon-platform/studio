#!/usr/bin/env python3
"""GEOMETOR Dialogue Voice Synthesizer & Stage Timing Engine.

Synthesizes spoken dialogue turns using the registered Argon III-C voice
(voice_xdhacg1pn1cq) on Gemini 3.8 Flash TTS, weaving in harmonic crystal
chime indicators ([bell:...]), automatic pacing shifts ([style:...]), and
emitting a frame-accurate JSON timing manifest (<output>.json) for sequencing
1080p visual stage frames and animations.
"""

import argparse
import base64
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import urllib.error
import urllib.request
import wave

SKILL_DIR = Path(__file__).resolve().parent.parent
PROFILE_PATH = SKILL_DIR / "voice_profile.json"
SAMPLE_RATE = 24000


def load_profile() -> dict:
  return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


DOUBLE_PAUSE_RE = re.compile(
    r"(?:<\s*pause\s*>\s*){2,}|<\s*long\s+pause\s*>", flags=re.I
)
SINGLE_PAUSE_RE = re.compile(r"<\s*(?:short\s+)?pause\s*>", flags=re.I)


def apply_pronunciations(text: str, pronunciations: dict[str, str]) -> str:
  # Never send literal <pause> tags to the TTS model (it may read the word "pause" aloud).
  # Replace any mid-sentence <pause> with a comma/ellipsis or strip after punctuation.
  text = re.sub(r"([.!?,;:])\s*<\s*(?:short\s+|long\s+)?pause\s*>", r"\1 ", text, flags=re.I)
  text = SINGLE_PAUSE_RE.sub(", ", text)
  text = re.sub(r"\s+", " ", text).strip()
  for src, dst in pronunciations.items():
    text = re.sub(rf"\b{re.escape(src)}\b", dst, text)
  return text


def api_post(url: str, payload: dict, api_key: str) -> dict:
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
      "input": [{
          "type": "user_input",
          "content": [{
              "type": "text",
              "text": text,
              "annotations": [{
                  "type": "speech_metadata",
                  "style": style,
              }],
          }],
      }],
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
        sum(
            w * math.sin(2.0 * math.pi * f * t) for w, f in zip(weights, freqs)
        )
        / w_sum
    )
    s = int(max(-1.0, min(1.0, val * attack * decay * amp)) * 32767)
    samples.append(struct.pack("<h", s))
  return b"".join(samples)


def silence_pcm(duration: float = 0.10, sample_rate: int = SAMPLE_RATE) -> bytes:
  return b"\x00\x00" * int(sample_rate * duration)


BELL_SPLIT_RE = re.compile(r"\[(bell:[a-z_]+)\]", re.I)
STYLE_SPLIT_RE = re.compile(r"\[style:([a-z_]+)\]", re.I)


def _strip_screen_directives(script_text: str) -> str:
  """Extract only [bell:...] tags and spoken 'say:' lines when given a unified screen-directive script."""
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
    if s.lower().startswith("[bell:") or s.lower().startswith("[style:"):
      kept.append(s)
      continue
    m_say = re.match(r"^say\s*:\s*(.+)$", s, flags=re.I)
    if m_say:
      kept.append(m_say.group(1).strip())
  return "\n".join(kept)


def parse_script_segments(
    script_text: str, profile: dict
) -> list[tuple[str | None, list[tuple[str, str]]]]:
  """Splits script text on [bell:...] slide boundaries and inline [style:...] phrases.

  Each [bell:...] starts a new stage segment (resetting style to default 'brisk').
  Inline [style:resonant] applies only to the immediately following phrase/sentence
  within that segment.
  Returns a list of (bell_key, [(style_string, phrase_text), ...]).
  """
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
      # Only split into a separate TTS call if p_chunk ends a complete sentence or pause tag
      if p_chunk and not re.search(r"[.!?:;>]\s*$", p_chunk):
        # Mid-sentence [style:...] would cause an audio splice/skip; keep sentence unified
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
    for st_val, chunk_str in raw_style_phrases:
      for sub_p in DOUBLE_PAUSE_RE.split(chunk_str):
        sub_p = sub_p.strip()
        if sub_p:
          phrases.append((st_val, sub_p))

    if phrases:
      segments.append((bell_key, phrases))

  return segments


def synthesize_script(script_text: str, output_wav: Path) -> tuple[Path, Path]:
  api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
  if not api_key:
    raise RuntimeError("Neither GEMINI_API_KEY nor GOOGLE_API_KEY is set")

  profile = load_profile()
  voice_id = profile["voice_id"]
  model = profile.get("model", "gemini-3.8-flash-tts")
  pronunciations = profile.get("pronunciations", {})
  bells_cfg = profile.get("bells", {})

  segments = parse_script_segments(script_text, profile)
  if not segments:
    raise ValueError("No spoken text found in script")

  pcm_chunks = []
  timing_segments = []
  cursor_samples = 0

  for idx, (bell_key, phrases) in enumerate(segments):
    seg_start_samples = cursor_samples
    seg_chunks = []
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
    phrase_texts = []
    phrase_styles = []
    subsegments = []

    for p_idx, (style_str, raw_phrase) in enumerate(phrases):
      sub_start = sub_cursor
      p_chunks = []
      if p_idx == 0 and intro_samples > 0:
        # First subsegment includes the leading bell chime
        sub_cursor += intro_samples
      elif p_idx > 0:
        # Insert a clean 0.42s digital silence pause between <pause> <pause> steps
        sil = silence_pcm(0.42)
        seg_chunks.append(sil)
        p_chunks.append(sil)
        sub_cursor += len(sil) // 2

      clean_text = apply_pronunciations(raw_phrase, pronunciations)
      pcm = synth_pcm_bytes(voice_id, clean_text, style_str, model, api_key)
      seg_chunks.append(pcm)
      p_chunks.append(pcm)
      sub_cursor += len(pcm) // 2

      sub_clean_caption = SINGLE_PAUSE_RE.sub(" ", raw_phrase)
      sub_clean_caption = re.sub(r"\s+", " ", sub_clean_caption).strip()
      subsegments.append({
          "index": p_idx,
          "text": sub_clean_caption,
          "start_sec": round(sub_start / SAMPLE_RATE, 3),
          "end_sec": round(sub_cursor / SAMPLE_RATE, 3),
          "duration_sec": round((sub_cursor - sub_start) / SAMPLE_RATE, 3),
      })
      phrase_texts.append(sub_clean_caption)
      phrase_styles.append(style_str)

    seg_bytes = b"".join(seg_chunks)
    seg_samples = len(seg_bytes) // 2
    cursor_samples += seg_samples
    pcm_chunks.append(seg_bytes)

    timing_segments.append({
        "index": idx,
        "bell": bell_key,
        "style": " -> ".join(dict.fromkeys(phrase_styles)),
        "text": " ".join(phrase_texts),
        "start_sec": round(seg_start_samples / SAMPLE_RATE, 3),
        "end_sec": round(cursor_samples / SAMPLE_RATE, 3),
        "duration_sec": round(seg_samples / SAMPLE_RATE, 3),
        "subsegments": subsegments,
    })

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
              "voice_id": voice_id,
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
      description="Synthesize and optionally play a GEOMETOR dialogue turn."
  )
  parser.add_argument(
      "text",
      nargs="?",
      help="Spoken script text with optional [bell:...] and [style:...] tags.",
  )
  parser.add_argument(
      "-f", "--file", type=Path, help="Read script text from file."
  )
  parser.add_argument(
      "-o",
      "--output",
      type=Path,
      required=True,
      help="Output WAV file path.",
  )
  parser.add_argument(
      "--play",
      action="store_true",
      help="Play the synthesized WAV over system audio after rendering.",
  )
  args = parser.parse_args()

  script_text = args.file.read_text(encoding="utf-8") if args.file else args.text
  if not script_text:
    raise SystemExit("Provide script text via positional arg or --file")

  out_wav, out_json = synthesize_script(script_text, args.output)
  print(f"Synthesized: {out_wav}")
  print(f"Manifest:    {out_json}")
  if args.play:
    play_wav(out_wav)


if __name__ == "__main__":
  main()
