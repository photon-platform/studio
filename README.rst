======================
photon-platform-studio
======================

Media production, 1440p QHD (VP9) encoding, 3-tier Google Drive archiving, and multi-channel YouTube broadcast automation for PHOTON & GEOMETOR.

Action Tools (CLI & MCP Server)
-------------------------------

- ``studio speak`` / ``studio_speak``: Parse unified ``script.txt`` cues (``[bell:...]``, ``[sfx:...]``, ``[style:...]``, ``<pause>``, ``[screen:...]``), synthesize Argon III-C narration, and emit timing manifests (``.json``, ``.srt``, ``.vtt``).
- ``studio capture`` / ``studio_capture``: Capture HTML/SVG slides, live web consoles, and deterministic 2D/3D WebGL animations in an isolated Chromium viewport enforcing YouTube safe zones.
- ``studio render`` / ``studio_render``: Compile ``2560x1440`` QHD (VP9-trigger) ``16:9`` masters and ``1080x1920`` ``9:16`` Shorts with ``30fps`` CFR, closed 3.0s GOPs (``-g 90``), ``bt709`` color signaling, ``48 kHz`` stereo AAC, and ``< 2 MB`` JPEG thumbnails.
- ``studio verify`` / ``studio_verify``: Gatekeeper validation via ``ffprobe`` before archiving or uploading.
- ``studio archive`` / ``studio_archive``: Purge Tier 3 build scratch (``*_stage.html``, ``slides_*/``, ``frames_*/``) and sync Tier 2 master media with ``/home/phi/MEDIA`` and Google Drive (``gdrive:PHOTON-MEDIA``) via ``rclone``.
- ``studio broadcast`` / ``studio_broadcast``: Coordinate the shared ``10,000`` unit/day Pacific-Time YouTube Data API v3 quota ledger, cross-project release schedule, and atomic chunked ``curl`` uploads across ``@geometor`` and ``@GOR-9-station``.
