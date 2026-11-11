---
name: studio
description: Unified 3-Phase Media Studio workflow (Creative Scripting -> Production Rendering -> Archive & YouTube Publishing) for GEOMETOR and PHOTON projects. Automatically loads project-specific conventions from <repo>/videos/STUDIO.md (or episodes/STUDIO.md).
---

# PHOTON Studio (`studio`)

Unified three-phase media production workflow for GEOMETOR, PHOTON Platform, and phi ARCHITECT repositories.

## Core Architecture: General Workflow vs. Project Context

**PHOTON Studio** separates **universal studio phases & rendering engines** from **project-specific conventions**:

1. **Always Read `<repo>/videos/STUDIO.md` First** (or `<repo>/episodes/STUDIO.md`):
   - [`elements/videos/STUDIO.md`](file:///home/phi/PROJECTS/geometor/elements/videos/STUDIO.md) — 3-act Euclidean proposition structure (`enunciation`, `construction`, `demonstration`), symbolic SVG selectors (`/ A B /`, `( A B )`, `< A B C >`), automatic defining-point expansion, equality ticks, and geometric `[sfx:...]` cues.
   - [`etc/videos/STUDIO.md`](file:///home/phi/PROJECTS/geometor/etc/videos/STUDIO.md) — 5-section triangle center structure (`primary`, `coordinates`, `central_lines`, `relations`, `invariance`), 5-SVG visual pipeline, and ETC `[sfx:...]` cues.
   - [`GOR-9/episodes/STUDIO.md`](file:///home/phi/PROJECTS/phiarchitect/GOR-9/episodes/STUDIO.md) — Narrative episode & 3D tour structure, Mode 2 (`motion`) 30 fps WebGL/Blender capture, and private broadcast rules.
2. **Strict No-Symlinks Storage Policy**:
   - **Never create symlinks to `/home/phi/MEDIA` or mount Google Drive as a FUSE filesystem.**
   - All rendered `.mp4`, `.wav`, and thumbnail masters live directly inside the project's `videos/` (or `episodes/`) directory as real, git-ignored local files.
   - Use `studio archive sync` (`push` / `pull`) to back up or restore git-ignored media files directly between `/home/phi/PROJECTS/<org>/<repo>/` and `gdrive:PHOTON-MEDIA/<org>/<repo>/`.

---

## The Three Studio Phases

### Phase 1: Creative (`script.scr` & Visual-Audio Choreography)

Author the canonical `script.scr` as the **single source of truth** (compiled directly in memory by `photon_platform.studio.script_compiler` without needing `scenes.json` on disk):

1. **Modular Section Boundaries (`=== <section_id> ===`)**:
   - Break every script into named sections (e.g., `=== enunciation ===`, `=== construction ===`, `=== demonstration ===`) so individual sections or lines can be updated and re-synthesized using `.tts_cache/`.
2. **1-to-1 Screen-to-Speech Exchange (`[screen: ...]`)**:
   - Every visual step has its own `[screen: ...]` block and its own `say:` line, so its visual duration is measured directly from its synthesized audio subsegment.
   - **Automatic Defining-Point Rule (`elements`)**: Stating a segment `/ A B /`, line `[ A B ]`, circle `( A B )`, or polygon `< A B C >` in `show:`, `active:`, or `equal:` automatically includes its defining points (`A, B`) and strikes the element.
3. **Acoustic Vocabulary (`[bell:...]` & `[sfx:...]`)**:
   - Pair visual highlights with project-specific `[sfx:...]` harmonic cues (`point`, `segment`, `line`, `circle`, `intersect`, `concur`, `equal`, `ratio`, `conjugate`, `invariant`, `qed`).

---

### Phase 2: Production (Two Distinct Native `2560x1440` QHD Rendering Modes)

`photon_platform.studio.render` codifies two distinct production pipelines, both targeting true native **`2560x1440` QHD (`16:9`)** and **`1080x1920` (`9:16` Short)** at **`30.00 fps` CFR**:

1. **Mode 1: Slide Sequence (`mode="slides"` — `elements`, `etc`, 2D Console Plates)**:
   - **Capture**: Vector HTML/SVG layouts (`1920x1080` CSS) are rasterized natively at **`2560x1440`** via Chromium `--force-device-scale-factor=1.3333333333` (`4/3`).
   - **Encode**: Clean FFmpeg `-f concat` demuxer with per-slide audio durations from `<prefix>.json`:
     `ffmpeg -y -f concat -safe 0 -i concat.txt -i <prefix>.wav -vf fps=30 -shortest <prefix>_1080p.mp4`
2. **Mode 2: 30 FPS Full-Motion Animation (`mode="motion"` — `GOR-9` 3D Tours, Cold Opens, Episodes, `phyllotaxis`)**:
   - **Capture**: Deterministic `dt = 1/30s` (`30.00 fps` exact) frame capture at native **`2560x1440`** on hardware GPU (`--use-gl=angle --use-angle=gl`). For WebGL (`Three.js`), pad window height (`--window-size=2560,1560`), disable `window.addEventListener('resize')`, and set `renderer.setSize(2560, 1440, false)`.
   - **Encode**: Use clean, direct FFmpeg defaults without synthetic noise (`noise=alls=2`), `-color_range tv` crushing, or `yuv420p` chroma downsampling:
     `ffmpeg -y -framerate 30 -i frames/frame_%04d.png -i <prefix>.wav <prefix>_1080p.mp4`
3. **Verify Outputs (`studio verify`)**:
   - Validates `ffprobe` streams, resolution (`2560x1440` and `1080x1920`), duration parity, audio presence, and `< 2 MB` thumbnails.

---

### Phase 3: Publishing (Direct Rclone Archive & YouTube Broadcast)

1. **Archive Git-Ignored Master Media (`studio archive`)**:
   - Push finished git-ignored `.mp4`, `.wav`, and thumbnail files from the project folder to `gdrive:PHOTON-MEDIA`:
     ```bash
     studio archive sync videos/ geometor/elements/videos --direction push
     ```
   - Restore git-ignored media files after cloning a repository on another machine:
     ```bash
     studio archive sync videos/ geometor/elements/videos --direction pull
     ```
2. **Audit & Broadcast to YouTube (`studio broadcast`)**:
   - Check live status and quota:
     ```bash
     studio broadcast status --project <elements|etc>
     studio broadcast quota
     ```
   - Upload, replace (`--replace`), or sync missing metadata/thumbnails/playlists (`--sync-missing`):
     ```bash
     studio broadcast upload --book i --props 1
     studio broadcast upload --collection c-00001-00019 --centers 1
     ```
