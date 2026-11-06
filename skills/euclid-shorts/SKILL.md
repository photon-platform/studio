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

## Quick Checklist for Every Euclid Proposition Short

1. **Inspect Both Sources**:
   - Read the Heath RST and `.g1.svg` in `/home/phi/PROJECTS/geometor/euclid/resources/heath/volume_<V>/book_<b>/propositions/`.
   - Read the corresponding `G.<n>/index.rst` and `g1.py` in `/home/phi/PROJECTS/geometor/elements/docsrc/elements2/G.<n>/`.
2. **Script 3 `[bell:...]` Acts in 100% `brisk` Tempo (No `[style:resonant]`, Use `<pause>` Only)**:
   - Use `<pause>` (and `<pause> <pause>` for longer beats—never `<short pause>`).
   - `[bell:proposition]` — Enunciation & Given (*Protasis* & *Ekthesis*).
   - `[bell:construction]` — Step-by-step compass & straightedge construction (*Kataskeuē*) with `:just:` postulates/propositions.
   - `[bell:insight]` — Equality assertions (*Apodeixis*) with green tick marks (`|`) and `Q.E.F.` / `Q.E.D.` (*Symperasma*).
3. **Synthesize & Render Dual `9:16` Short + `16:9` (`1440p QHD` VP9-Trigger) Widescreen**:
   - Run `/home/phi/.gemini/config/skills/dialogue/scripts/speak.py` (`BypassSandbox: true`).
   - Run `/home/phi/.gemini/config/skills/shorts/scripts/shorts.py` (`BypassSandbox: false`) with `"type": "euclid_step"` sub-slides (emits both `<short_name>_short.mp4` in `9:16` and `<short_name>_1080p.mp4` upscaled to `2560×1440` `1440p QHD` so YouTube encodes VP9 and defaults to HD).
   - Embed `<short_name>_stage.html` via `<agent-embed>` and show the rendered slides in a carousel.
   - **Always Put Media Links at the Very End of the Response**: Place a clean line of direct clickable `file://` links to `.mp4`, `.wav`, and `_stage.html` at the very bottom of your response (after all text, `<agent-embed>`, and carousel):
     ```markdown
     **Media**: [<short_name>_short.mp4](file://<artifact_dir>/<short_name>_short.mp4) • [<short_name>_1080p.mp4](file://<artifact_dir>/<short_name>_1080p.mp4) • [<short_name>_stage.html](file://<artifact_dir>/<short_name>_stage.html)
     ```
4. **Seamless In-Conversation Skill Switching**: From the main agent, you can switch freely between `producer`, `euclid-shorts`, `shorts`, `presenter`, `podcaster`, and `dialogue` in the same conversation thread.
