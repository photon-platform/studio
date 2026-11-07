---
name: studio
description: Unified 3-Phase Media Studio workflow (Creative Scripting -> Production Rendering -> Vault & YouTube Publishing) for GEOMETOR and PHOTON projects. Automatically loads project-specific conventions from <repo>/videos/STUDIO.md (or episodes/STUDIO.md).
---

# PHOTON Studio (`studio`)

Unified three-phase media production workflow for GEOMETOR, PHOTON Platform, and phi ARCHITECT repositories.

## Core Architecture: General Workflow vs. Project Context

Instead of separate skills for every video format or repository, **PHOTON Studio** separates **universal studio phases** from **project-specific conventions**:

1. **Always Read `<repo>/videos/STUDIO.md` First** (or `<repo>/episodes/STUDIO.md`):
   - [`elements/videos/STUDIO.md`](file:///home/phi/PROJECTS/geometor/elements/videos/STUDIO.md) — 3-act Euclidean proposition structure (`enunciation`, `construction`, `demonstration`), symbolic SVG selectors (`/ A B /`, `( C A D )`, `< A B C >`), equality ticks, and geometric `[sfx:...]` cues.
   - [`etc/videos/STUDIO.md`](file:///home/phi/PROJECTS/geometor/etc/videos/STUDIO.md) — 5-section triangle center structure (`title`, `construction`, `coordinates`, `lines_and_conjugates`, `properties`), 5-SVG visual pipeline, and ETC `[sfx:...]` cues.
   - [`GOR-9/episodes/STUDIO.md`](file:///home/phi/PROJECTS/phiarchitect/GOR-9/episodes/STUDIO.md) — Narrative episode structure, 3D console/telemetry cues, and private broadcast rules.

---

## The Three Studio Phases

### Phase 1: Creative (`script.txt` & Visual-Audio Choreography)

Develop or refine the canonical `script.txt` for an episode, proposition, or center:

1. **Modular Section Boundaries (`=== <section_id> ===`)**:
   - Break every script into named sections (e.g., `=== enunciation ===`, `=== construction ===`, `=== demonstration ===`) so individual sections can be updated and re-synthesized without regenerating the entire episode.
2. **Tight Screen-to-Speech Exchange (`[screen: ...]`)**:
   - Every visual transition has an explicit `[screen: ...]` block declaring what is shown (`show:`), highlighted (`highlight:`), ticked (`ticks:`), or badged (`badge:`), immediately followed by its spoken `say:` line.
   - **One Visual Idea per Sub-Slide**: Never crowd a construction locus and its resulting intersection, or multiple independent equalities, into a single sub-slide.
3. **Acoustic Vocabulary (`[bell:...]` & `[sfx:...]`)**:
   - Open every episode with the project's identifying entry bell triad and spoken identifier.
   - Pair visual highlights with project-specific `[sfx:...]` cues (defined in the project's `STUDIO.md`) and intentional `[pause:...]` breathing space between logical clauses.

---

### Phase 2: Production (TTS Synthesis, Dual-Format Render & Verification)

Compile `script.txt` into `scenes.json`, synthesize Argon III-C (`voice_xdhacg1pn1cq`) audio stems, and render dual-format (`16:9` 2560x1440 QHD + `9:16` 1080x1920 Short) MP4s:

1. **Synthesize Audio & Compile Scenes**:
   - Speech synthesis engine: `/home/phi/.gemini/config/skills/studio/scripts/speak.py`
   - Dual-format visual renderer: `/home/phi/.gemini/config/skills/studio/scripts/shorts.py`
   - Or run the project builder (which delegates to `photon_platform.studio.render`):
     - `elements`: `uv run --no-sync python scripts/build_prop_video.py videos/book_i/01 --tts`
     - `etc`: `uv run --no-sync python scripts/build_center_video.py --center 1 --from-script`
2. **Verify Outputs (`studio verify`)**:
   - Validates `ffprobe` streams, resolution (`2560x1440` and `1080x1920`), duration parity, non-silent audio, and compressed `< 2 MB` thumbnails:
     ```bash
     studio verify --project <elements|etc|phyllotaxis|gor9>
     ```

---

### Phase 3: Publishing (Vault Symlinking & YouTube Broadcast)

Manage storage tiers and publish verified packages through the `studio` CLI:

1. **Link Tier 2 Master Media to `/home/phi/MEDIA` Vault**:
   - Moves large binary files (`.mp4`, `.wav`, thumbnails) into `/home/phi/MEDIA/<org>/<repo>/...` (backed by 30 TB Google Drive via `photon-media-mount.service`) and replaces them with transparent symlinks in `/home/phi/PROJECTS/`:
     ```bash
     studio archive link --project <elements|etc|phyllotaxis|gor9> --execute
     ```
2. **Audit & Broadcast to YouTube (`@geometor`)**:
   - Check live status and quota:
     ```bash
     studio broadcast status --project <elements|etc>
     studio broadcast quota
     ```
   - Upload, replace (`--replace`), or sync missing metadata/thumbnails/playlists (`--sync-missing`):
     ```bash
     studio broadcast upload --project elements --props 1 --book i
     studio broadcast upload --project etc --centers 1 --collection c-00001-00019
     ```
