---
name: euclid-shorts
description: >-
  Specialized 1080x1920 (9:16) vertical YouTube Shorts compiler for all 465
  propositions of Euclid's Elements (I.1 through XIII.18). Combines Heath's text
  and interactive SVGs from geometor/euclid with the G-Index signatures and
  pseudocode steps from geometor/elements, rendering progressive dark-mode SVG
  highlights, green equality tick marks (|), mobile-readable proof clauses, and
  prerequisite pills synced to Argon III-C narration.
---

# GEOMETOR Euclid Propositions Shorts Mode (All 465 Propositions • 9:16)

When this skill is active, you are producing a **`1080×1920` (`9:16`) vertical step-by-step YouTube Short** for a proposition from Euclid's *Elements* (`geometor/euclid` + `geometor/elements`).

Read and follow the complete specification in:
- `/home/phi/.gemini/config/skills/shorts/references/euclid_props.md`

## Quick Checklist for Every Euclid Proposition (`elements/videos/book_<b>/<nn>/`)

1. **Inspect Both Sources & Existing Package**:
   - Read the Heath RST and `.g1.svg` in `/home/phi/PROJECTS/geometor/euclid/resources/heath/volume_<V>/book_<b>/propositions/`.
   - Read the corresponding `G.<n>/index.rst` and `g1.py` in `/home/phi/PROJECTS/geometor/elements/docsrc/elements2/G.<n>/`.
   - Inspect the proposition package in `/home/phi/PROJECTS/geometor/elements/videos/book_<b>/<nn>/` (`script.txt`, `scenes.json`, `curated.json`, `youtube.json`).
2. **Script 3 `[bell:...]` Acts in 100% `brisk` Tempo with Inline `[screen: ...]` Directives**:
   - Use `<pause>` (and `<pause> <pause>` for longer beats—never `<short pause>`, never `[style:resonant]`).
   - Place inline `[screen: <step_num> · <STEP_KIND> | <symbolic> | just: <just_id>]` cues in `script.txt` directly above each spoken paragraph so `script.txt` serves as the single human-editable source of truth for both narration and screen steps (`scenes.json`):
     - `[bell:proposition]` — Enunciation & Given (*Protasis* & *Ekthesis*).
     - `[bell:construction]` — Step-by-step compass & straightedge construction (*Kataskeuē*) with `:just:` postulates/propositions.
     - `[bell:insight]` — Equality assertions (*Apodeixis*, **one equality statement per sub-slide**) with green tick marks (`|`) and `Q.E.F.` / `Q.E.D.` (*Symperasma*).
3. **Synthesize, Render Dual `9:16` + `16:9` (`2560×1440` QHD VP9), Verify & Symlink to `/home/phi/MEDIA`**:
   - Run `python3 scripts/build_prop_video.py videos/book_<b>/<nn> --tts` (or `studio render videos/book_<b>/<nn> --tts`) which:
     1. Synthesizes `<prefix>.wav` + `<prefix>.json` via Argon III-C (`speak.py`).
     2. Renders `<prefix>_short.mp4` (`1080×1920` `9:16`) and `<prefix>_1080p.mp4` (`2560×1440` `1440p QHD` so YouTube encodes `vp09`).
     3. Verifies stream/thumbnail compliance via `studio verify` and purges Tier 3 scratch (`*_stage.html`, `slides_*/`).
   - Run `studio archive link videos/book_<b>/<nn> geometor/elements/videos/book_<b>/<nn> --execute` to move `.mp4`/`.wav`/thumbnails to `/home/phi/MEDIA/` and leave symlinks in `elements/videos/`.
4. **Seamless In-Conversation Skill Switching**: From the main agent, you can switch freely between `producer`, `euclid-shorts`, `shorts`, `presenter`, `podcaster`, and `dialogue` in the same conversation thread.
