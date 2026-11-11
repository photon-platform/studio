---
name: studio
description: Unified 3-Phase Media Studio workflow (Creative Scripting -> Production Rendering -> Archive & YouTube Publishing) for GEOMETOR and PHOTON projects. Automatically loads project-specific conventions from <repo>/videos/STUDIO.md (or episodes/STUDIO.md).
---

# PHOTON Studio (`studio`)

Unified three-phase media production workflow for GEOMETOR, PHOTON Platform, and phi ARCHITECT repositories, powered by `photon-platform/studio` (`~/.local/bin/studio` CLI and `~/.local/bin/studio-mcp` MCP server).

## Core Architecture: Canonical Engine vs. Project Overlays

**PHOTON Studio** separates **universal studio phases & rendering engines** (`photon_platform.studio`) from **project-specific domain overlays**:

1. **Always Read `<repo>/videos/STUDIO.md` First** (or `<repo>/episodes/STUDIO.md`):
   - [`elements/videos/STUDIO.md`](file:///home/phi/PROJECTS/geometor/elements/videos/STUDIO.md) — `@geometor` channel; Mode 1 (`stage`) 3-act Euclidean proposition structure (`enunciation`, `construction`, `demonstration`), symbolic SVG selectors (`/ A B /`, `( A B )`, `< A B C >`), automatic defining-point expansion, equality ticks, and geometric `[sfx:...]` cues.
   - [`etc/videos/STUDIO.md`](file:///home/phi/PROJECTS/geometor/etc/videos/STUDIO.md) — `@geometor` channel; Mode 1 (`stage`) 5-section triangle center structure (`primary`, `coordinates`, `central_lines`, `relations`, `invariance`), 5-SVG visual pipeline, HTML subscript rendering (`X<sub>1</sub>`), and ETC `[sfx:...]` cues.
   - [`phyllotaxis/videos/STUDIO.md`](file:///home/phi/PROJECTS/geometor/phyllotaxis/videos/STUDIO.md) — `@geometor` channel; Mode 2 (`motion`) 30.00 fps native `2560x1440` WebGL/Canvas animations (`unfolding`).
   - [`GOR-9/videos/STUDIO.md`](file:///home/phi/PROJECTS/phiarchitect/GOR-9/videos/STUDIO.md) & [`GOR-9/episodes/STUDIO.md`](file:///home/phi/PROJECTS/phiarchitect/GOR-9/episodes/STUDIO.md) — `@GOR-9-station` channel (default `private` / `unlisted`, never auto-public); Mode 2 (`motion`) 30.00 fps WebGL spline tours (`camera_path.json`), console demos, Blender Cycles cold opens, and multi-character `[voice: ...]` episodes.
2. **Strict No-Symlinks Storage Policy**:
   - **Never create symlinks to `/home/phi/MEDIA` or mount Google Drive as a FUSE filesystem.**
   - All rendered `.mp4`, `.wav`, `.tts_cache/`, and thumbnail masters live directly inside the project's `videos/` (or `episodes/`) directory as real, git-ignored local files.
   - Use `studio archive sync` (`push` / `pull`) to back up or restore git-ignored Tier 2 media files directly between `/home/phi/PROJECTS/<org>/<repo>/` and `gdrive:PHOTON-MEDIA/<org>/<repo>/`.

---

## The Three Studio Phases

### Phase 1: Creative (`script.scr`, `motion.json` / `camera_path.json` & Multi-Voice Audio)

1. **Mode 1 Structured Scripts (`script.scr`)**:
   - Compiled directly in memory by `photon_platform.studio.script_compiler` without needing `scenes.json` on disk.
   - Modular `=== <section_id> ===` boundaries (`enunciation`, `construction`, `demonstration`, `review`, `outro`) enable per-segment caching in `segments/<NN>_<section_id>/` and phrase-level caching in `.tts_cache/`.
   - 1-to-1 `[screen: ...]` -> `say:` exchange synchronizes step duration directly from synthesized audio subsegments.
2. **Multi-Character & Harmonic Audio (`studio speak`)**:
   - Supports Argon III-C default narration plus inline multi-character casting directives (`[voice: Fenrir | style: authoritative]`, `[voice: Kore]`), harmonic bells (`[bell:intro]`, `[bell:qef]`), and procedural `[sfx:...]` cues:
     ```bash
     studio speak videos/book_i/01/script.scr
     ```
3. **Mode 2 Camera Choreography (`motion.json` / `camera_path.json`)**:
   - Declarative monotonic cubic Hermite camera splines across 5 coordinate systems (`cylindrical`, `spherical`, `cartesian`, `booth_cylindrical`, `object_locked`) or custom `injected_js_file` hooks (`window.__studioStep`).

---

### Phase 2: Production (Native `2560x1440` QHD + `1080x1920` Short Rendering)

Both production modes target native **`2560x1440` QHD (`16:9`)** (triggering YouTube's `vp09` pipeline) and **`1080x1920` (`9:16` Short)** at **`30.00 fps` CFR**, encoded cleanly with **`-pix_fmt yuv420p -movflags +faststart`** and **zero artificial `-vf` filters** (no synthetic grain `noise=...`, no `-color_range tv`, no x264 AQ overrides; only `-vf vflip` when reading upside-down WebGL `gl.readPixels` raw RGBA buffers):

1. **Mode 1: Interactive Stage & Per-Segment Renderer (`studio stage` / `studio render --mode slides`)**:
   - Live browser preview & editor:
     ```bash
     studio stage videos/book_i/01
     ```
   - Headless CDP single-browser capture + per-segment incremental clip build + stream-copy assembly:
     ```bash
     studio stage videos/book_i/01 --render
     studio stage videos/book_i/01 --section construction
     studio render videos/book_i/01 --mode slides
     ```
2. **Mode 2: 30 FPS Full-Motion WebGL / Frame-Sequence Engine (`studio motion` / `studio render --mode motion`)**:
   - Hardware-GPU (`--use-gl=angle --use-angle=gl`) deterministic virtual-clock capture directly from `motion.json` or `camera_path.json` (or `--encode-only` for pre-rendered Blender/compositor `frames/`):
     ```bash
     studio motion videos/drone_loop
     studio motion videos/drone_loop --test-frames
     studio motion episodes/ep01_box_that_hummed --encode-only
     ```
3. **Verify Outputs (`studio verify`)**:
   - Validates `ffprobe` streams, resolution (`2560x1440` / `1080x1920`), `h264` + `yuv420p` + `aac`, `< 2 MB` thumbnails, Mode 1 `segments_status`, and Mode 2 `torn_frames_count`:
     ```bash
     studio verify videos/book_i/01
     ```

---

### Phase 3: Publishing (`studio archive` & Multi-Channel `studio broadcast`)

1. **3-Tier Storage & Rclone Archive (`studio archive`)**:
   - Inspect Tier 1 (Git), Tier 2 (Master Vault), and Tier 3 (Scratch) usage, clean verified build scratch (`stage.html`, `segments/`, `slides_*/`, `frames/`), and sync Tier 2 masters with Google Drive:
     ```bash
     studio archive scan videos/
     studio archive clean videos/ --execute
     studio archive sync videos/ geometor/elements/videos --direction push
     studio archive sync-all --direction push --dry-run
     ```
2. **Multi-Channel YouTube Broadcast & Guardrails (`studio broadcast`)**:
   - Enforces strict channel-to-token verification (`channels.list?mine=true`) before uploading:
     - `@geometor` (`elements`, `etc`, `phyllotaxis`) -> `~/.config/geometor/youtube_token.json`
     - `@GOR-9-station` (`GOR-9`, defaults to `private` unless `--publish` / `studio broadcast publish` is explicitly invoked) -> `~/.config/geometor/youtube_token_gor9.json`
   - Commands:
     ```bash
     studio broadcast quota
     studio broadcast status --project GOR-9
     studio broadcast auth --channel @GOR-9-station
     studio broadcast upload videos/mk4_demo --sync-missing
     studio broadcast publish videos/mk4_demo --channel @GOR-9-station
     ```
