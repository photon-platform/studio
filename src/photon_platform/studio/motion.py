"""Mode 2 (`motion`): Deterministic 30.00 fps Full-Motion WebGL/Canvas Movie Engine.

Zero-dependency, hardware-GPU accelerated (`--use-gl=angle --use-angle=gl`)
capture engine for Three.js / WebGL / HTML5 Canvas scenes:
  1. Serves target HTML/assets from a local HTTP server with an injected
     deterministic virtual clock (`performance.now()`, `Date.now()`,
     `requestAnimationFrame`), window-resize lock, and optional declarative
     5-coordinate-system monotonic cubic Hermite camera spline driver
     (`cylindrical`, `spherical`, `cartesian`, `booth_cylindrical`, `object_locked`).
  2. Renders each frame at native `2560x1440` (`16:9` QHD) or `1080x1920`
     (`9:16` Vertical Short) on the hardware GPU (with optional CSS `scale_factor`).
  3. Reads raw RGBA pixels via `gl.readPixels` and streams them via HTTP POST
     directly into `ffmpeg -f rawvideo -pix_fmt rgba -vf vflip -pix_fmt yuv420p -movflags +faststart`
     (zero artificial grain, color-range, or x264 AQ filter overrides).
  4. Also supports encoding pre-rendered PNG frame directories (`frames/frame_%04d.png`)
     and auditing/purging headless window-resize torn frames (`purge_torn_frames`).
"""

from __future__ import annotations

import functools
import http.server
import json
import math
import os
import re
import shutil
import socketserver
import struct
import subprocess
import threading
import time
import urllib.parse
import wave
import zlib
from pathlib import Path
from typing import Any

from .svg_utils import find_chrome


def find_chrome_binary() -> str:
    """Locate headless Chromium/Chrome binary (preferring Playwright's Chromium build)."""
    return str(find_chrome())


STUDIO_PRE_SCRIPT_JS = r"""
<script>
(() => {
  const params = new URLSearchParams(window.location.search);
  const TARGET_W = parseInt(params.get('__studio_w') || '2560', 10);
  const TARGET_H = parseInt(params.get('__studio_h') || '1440', 10);
  const CSS_W = parseInt(params.get('__studio_css_w') || String(TARGET_W), 10);
  const CSS_H = parseInt(params.get('__studio_css_h') || String(TARGET_H), 10);
  const DPR = parseFloat(params.get('__studio_dpr') || '1');

  // Lock window innerWidth / innerHeight & devicePixelRatio so WebGL never resizes or tears
  Object.defineProperty(window, 'innerWidth', { get: () => CSS_W, configurable: true });
  Object.defineProperty(window, 'innerHeight', { get: () => CSS_H, configurable: true });
  Object.defineProperty(window, 'devicePixelRatio', { get: () => DPR, configurable: true });

  const origAddEventListener = window.addEventListener.bind(window);
  window.addEventListener = function(type, listener, options) {
    if (type === 'resize') return;
    return origAddEventListener(type, listener, options);
  };

  window.__studioVirtualTimeMs = 0.0;
  window.__studioFrameIndex = 0;
  window.__studioRafQueue = [];
  window.__studioRafId = 0;

  performance.now = () => window.__studioVirtualTimeMs;
  Date.now = () => 1700000000000 + Math.floor(window.__studioVirtualTimeMs);

  window.requestAnimationFrame = (cb) => {
    const id = ++window.__studioRafId;
    window.__studioRafQueue.push({ id, cb });
    return id;
  };
  window.cancelAnimationFrame = (id) => {
    window.__studioRafQueue = window.__studioRafQueue.filter((item) => item.id !== id);
  };

  const origGetContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function(type, attrs) {
    if (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl') {
      attrs = Object.assign({}, attrs || {}, {
        preserveDrawingBuffer: true,
        antialias: true,
        powerPreference: 'high-performance'
      });
    }
    const ctx = origGetContext.call(this, type, attrs);
    if (ctx && (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl')) {
      window.__studioGlCanvas = this;
      window.__studioGlContext = ctx;
    }
    return ctx;
  };

  window.__studioAdvanceFrame = (dtMs) => {
    window.__studioVirtualTimeMs += dtMs;
    window.__studioFrameIndex += 1;
    const queue = window.__studioRafQueue.slice();
    window.__studioRafQueue = [];
    for (const item of queue) {
      try {
        item.cb(window.__studioVirtualTimeMs);
      } catch (err) {
        console.error('Studio RAF error:', err);
      }
    }
  };
})();
</script>
"""


UNIVERSAL_CAMERA_PATH_JS = r"""
(() => {
  function unwrapAngleSeries(angles) {
    if (!angles || angles.length === 0) return [];
    const out = [angles[0]];
    for (let i = 1; i < angles.length; i++) {
      let d = angles[i] - angles[i - 1];
      while (d > Math.PI) d -= Math.PI * 2.0;
      while (d < -Math.PI) d += Math.PI * 2.0;
      out.push(out[i - 1] + d);
    }
    return out;
  }

  function evalMonotonicCubic(xs, ys, xQuery, isPeriodicSlope) {
    const n = xs.length;
    if (n === 0) return 0.0;
    if (n === 1) return ys[0];
    const dxs = [];
    const dys = [];
    const ms = [];
    for (let i = 0; i < n - 1; i++) {
      const dx = Math.max(1e-6, xs[i + 1] - xs[i]);
      const dy = ys[i + 1] - ys[i];
      dxs.push(dx);
      dys.push(dy);
      ms.push(dy / dx);
    }
    const c1s = new Array(n).fill(0);
    if (isPeriodicSlope) {
      const mAvg = 0.5 * (ms[0] + ms[n - 2]);
      c1s[0] = mAvg;
      c1s[n - 1] = mAvg;
    } else {
      c1s[0] = 0.0;
      c1s[n - 1] = 0.0;
    }
    for (let i = 1; i < n - 1; i++) {
      const m0 = ms[i - 1];
      const m1 = ms[i];
      if (m0 * m1 <= 0) {
        c1s[i] = 0.0;
      } else {
        const dx0 = dxs[i - 1];
        const dx1 = dxs[i];
        const common = dx0 + dx1;
        c1s[i] = (3.0 * common) / ((common + dx1) / m0 + (common + dx0) / m1);
      }
    }
    let seg = n - 2;
    for (let i = 0; i < n - 1; i++) {
      if (xQuery >= xs[i] && xQuery <= xs[i + 1]) {
        seg = i;
        break;
      }
    }
    const h = dxs[seg];
    const u = Math.max(0.0, Math.min(1.0, (xQuery - xs[seg]) / h));
    const u2 = u * u;
    const u3 = u2 * u;
    const h00 = 2.0 * u3 - 3.0 * u2 + 1.0;
    const h10 = u3 - 2.0 * u2 + u;
    const h01 = -2.0 * u3 + 3.0 * u2;
    const h11 = u3 - u2;
    return h00 * ys[seg] + h10 * h * c1s[seg] + h01 * ys[seg + 1] + h11 * h * c1s[seg + 1];
  }

  function evalCameraPathPose(cfg, t, duration) {
    const kfs = cfg.keyframes || [];
    const coordSys = cfg.coordinate_system || 'cylindrical';
    if (coordSys === 'object_locked' || coordSys === 'satellite_locked') {
      const targetName = cfg.target_object || 'ors351Sat';
      const scn = (typeof scene !== 'undefined') ? scene : window.scene;
      const obj = window[targetName] || (scn && typeof scn.getObjectByName === 'function' ? scn.getObjectByName(targetName) : null);
      const lp = cfg.local_pos || [0, 2, 8];
      const ll = cfg.local_look || [0, 0, 0];
      const fov = cfg.fov || 46.0;
      if (obj && typeof obj.updateMatrixWorld === 'function' && typeof THREE !== 'undefined') {
        obj.updateMatrixWorld(true);
        const pVec = new THREE.Vector3(lp[0], lp[1], lp[2]).applyMatrix4(obj.matrixWorld);
        const lVec = new THREE.Vector3(ll[0], ll[1], ll[2]).applyMatrix4(obj.matrixWorld);
        return {
          camX: pVec.x, camY: pVec.y, camZ: pVec.z,
          targetX: lVec.x, targetY: lVec.y, targetZ: lVec.z,
          fov, roll: 0
        };
      }
    }
    if (kfs.length === 0) {
      return { camX: 0, camY: 5, camZ: 15, targetX: 0, targetY: 0, targetZ: 0, fov: 45, roll: 0 };
    }
    const isLoop = !!cfg.is_loop;
    const pathDur = cfg.duration || cfg.duration_sec || duration;
    const tQuery = isLoop
      ? (((t % duration) + duration) % duration) * (pathDur / duration)
      : Math.max(0.0, Math.min(pathDur, (t / duration) * pathDur));
    const ts = kfs.map((k) => k.t);
    const angleScale = (cfg.angle_unit === 'deg' || cfg.angle_unit === 'degrees') ? (Math.PI / 180.0) : 1.0;
    const maybeUnwrap = (arr) => (cfg.unwrap_angles ? unwrapAngleSeries(arr) : arr);
    const origin = cfg.origin || [0.0, 0.0, 0.0];
    const ox = origin[0] || 0.0;
    const oy = origin[1] || 0.0;
    const oz = origin[2] || 0.0;

    if (coordSys === 'cylindrical' || coordSys === 'tower_cylindrical') {
      const camAz = evalMonotonicCubic(ts, maybeUnwrap(kfs.map((k) => (k.camAz ?? k.az ?? 0.0) * angleScale)), tQuery, true);
      const camR = evalMonotonicCubic(ts, kfs.map((k) => k.camR ?? k.distR ?? 50.0), tQuery, isLoop);
      const camY = evalMonotonicCubic(ts, kfs.map((k) => k.camY ?? 35.0), tQuery, isLoop);
      const lookAz = evalMonotonicCubic(ts, maybeUnwrap(kfs.map((k) => (k.lookAz ?? k.camAz ?? k.az ?? 0.0) * angleScale)), tQuery, true);
      const lookR = evalMonotonicCubic(ts, kfs.map((k) => k.lookR ?? 0.0), tQuery, isLoop);
      const lookY = evalMonotonicCubic(ts, kfs.map((k) => k.lookY ?? k.targetY ?? 42.0), tQuery, isLoop);
      const fov = evalMonotonicCubic(ts, kfs.map((k) => k.fov ?? 46.0), tQuery, isLoop);
      const roll = evalMonotonicCubic(ts, kfs.map((k) => (k.roll ?? 0.0) * angleScale), tQuery, isLoop);
      return {
        camX: ox + Math.cos(camAz) * camR,
        camY: oy + camY,
        camZ: oz + Math.sin(camAz) * camR,
        targetX: ox + Math.cos(lookAz) * lookR,
        targetY: oy + lookY,
        targetZ: oz + Math.sin(lookAz) * lookR,
        fov,
        roll,
      };
    } else if (coordSys === 'spherical') {
      const camAz = evalMonotonicCubic(ts, maybeUnwrap(kfs.map((k) => (k.camAz ?? k.az ?? 0.0) * angleScale)), tQuery, isLoop);
      const camEl = evalMonotonicCubic(ts, kfs.map((k) => (k.camEl ?? k.el ?? 0.25) * angleScale), tQuery, isLoop);
      const camR = evalMonotonicCubic(ts, kfs.map((k) => k.camR ?? k.distR ?? 10.0), tQuery, isLoop);
      const targetX = evalMonotonicCubic(ts, kfs.map((k) => k.lookX ?? k.targetX ?? ox), tQuery, isLoop);
      const targetY = evalMonotonicCubic(ts, kfs.map((k) => k.lookY ?? k.targetY ?? oy), tQuery, isLoop);
      const targetZ = evalMonotonicCubic(ts, kfs.map((k) => k.lookZ ?? k.targetZ ?? oz), tQuery, isLoop);
      const fov = evalMonotonicCubic(ts, kfs.map((k) => k.fov ?? 42.0), tQuery, isLoop);
      const roll = evalMonotonicCubic(ts, kfs.map((k) => (k.roll ?? 0.0) * angleScale), tQuery, isLoop);
      const cosEl = Math.cos(camEl);
      return {
        camX: targetX + cosEl * Math.cos(camAz) * camR,
        camY: targetY + Math.sin(camEl) * camR,
        camZ: targetZ + cosEl * Math.sin(camAz) * camR,
        targetX,
        targetY,
        targetZ,
        fov,
        roll,
      };
    } else if (coordSys === 'booth_cylindrical' || coordSys === 'interior_cylindrical') {
      const baseFloorY = cfg.floor_y ?? (typeof BOOTH_Y !== 'undefined' ? BOOTH_Y : oy);
      const camAng = evalMonotonicCubic(ts, maybeUnwrap(kfs.map((k) => (k.camAng ?? 0.0) * angleScale)), tQuery, isLoop);
      const camR = evalMonotonicCubic(ts, kfs.map((k) => k.camR ?? 2.5), tQuery, isLoop);
      const camYRel = evalMonotonicCubic(ts, kfs.map((k) => k.camYRel ?? 1.5), tQuery, isLoop);
      const lookAng = evalMonotonicCubic(ts, maybeUnwrap(kfs.map((k) => (k.lookAng ?? 0.0) * angleScale)), tQuery, isLoop);
      const lookR = evalMonotonicCubic(ts, kfs.map((k) => k.lookR ?? 4.74), tQuery, isLoop);
      const lookYRel = evalMonotonicCubic(ts, kfs.map((k) => k.lookYRel ?? 1.25), tQuery, isLoop);
      const fov = evalMonotonicCubic(ts, kfs.map((k) => k.fov ?? 50.0), tQuery, isLoop);
      const roll = evalMonotonicCubic(ts, kfs.map((k) => (k.roll ?? 0.0) * angleScale), tQuery, isLoop);
      const strides = cfg.head_bob_strides || 0;
      const loopPhase = (tQuery / pathDur) * Math.PI * 2.0;
      const stepBob = strides > 0 ? 0.010 * Math.sin(strides * loopPhase) : 0.0;
      return {
        camX: ox + camR * Math.sin(camAng),
        camY: baseFloorY + camYRel + stepBob,
        camZ: oz - camR * Math.cos(camAng),
        targetX: ox + lookR * Math.sin(lookAng),
        targetY: baseFloorY + lookYRel + stepBob * 0.5,
        targetZ: oz - lookR * Math.cos(lookAng),
        fov,
        roll,
      };
    } else {
      const camX = evalMonotonicCubic(ts, kfs.map((k) => k.camX ?? 0.0), tQuery, isLoop);
      const camY = evalMonotonicCubic(ts, kfs.map((k) => k.camY ?? 30.0), tQuery, isLoop);
      const camZ = evalMonotonicCubic(ts, kfs.map((k) => k.camZ ?? 50.0), tQuery, isLoop);
      const targetX = evalMonotonicCubic(ts, kfs.map((k) => k.lookX ?? k.targetX ?? 0.0), tQuery, isLoop);
      const targetY = evalMonotonicCubic(ts, kfs.map((k) => k.lookY ?? k.targetY ?? 30.0), tQuery, isLoop);
      const targetZ = evalMonotonicCubic(ts, kfs.map((k) => k.lookZ ?? k.targetZ ?? 0.0), tQuery, isLoop);
      const fov = evalMonotonicCubic(ts, kfs.map((k) => k.fov ?? 46.0), tQuery, isLoop);
      const roll = evalMonotonicCubic(ts, kfs.map((k) => (k.roll ?? 0.0) * angleScale), tQuery, isLoop);
      return { camX, camY, camZ, targetX, targetY, targetZ, fov, roll };
    }
  }

  function applyCameraPoseToThree(pose) {
    const cam = (typeof camera !== 'undefined') ? camera : window.camera;
    const ctrls = (typeof controls !== 'undefined') ? controls : window.controls;
    if (!cam) return;
    cam.fov = pose.fov;
    cam.updateProjectionMatrix();
    cam.position.set(pose.camX, pose.camY, pose.camZ);
    if (ctrls && ctrls.target) {
      ctrls.enableDamping = false;
      ctrls.target.set(pose.targetX, pose.targetY, pose.targetZ);
      ctrls.update();
    } else {
      cam.lookAt(pose.targetX, pose.targetY, pose.targetZ);
    }
    if (pose.roll && Math.abs(pose.roll) > 1e-5) {
      cam.rotateZ(pose.roll);
    }
  }

  window.__studioEvalMonotonicCubic = evalMonotonicCubic;
  window.__studioEvalCameraPathPose = evalCameraPathPose;
  window.__studioApplyCameraPoseToThree = applyCameraPoseToThree;

  const cfg = window.CAMERA_PATH_CONFIG || window.__STUDIO_CAMERA_PATH_CONFIG;
  if (cfg && (Array.isArray(cfg.keyframes) || cfg.coordinate_system) && typeof window.__studioStep !== 'function') {
    window.__studioStep = async (idx, tSec, duration, targetW, targetH) => {
      if (cfg.hide_ui !== false) {
        const defaultSelectors = ['.top-nav', '#hud-left', '#hud-right', '#hud-bottom', '.hud-overlay', '.ui-panel'];
        const customSelectors = cfg.hide_selectors || [];
        for (const sel of defaultSelectors.concat(customSelectors)) {
          document.querySelectorAll(sel).forEach((el) => {
            el.style.display = 'none';
          });
        }
      }
      window.__studioVirtualTimeMs = tSec * 1000.0;
      if (typeof window.setModelStateAtTime === 'function') {
        await window.setModelStateAtTime(tSec, duration, cfg);
      } else if (typeof window.setModelTime === 'function') {
        await window.setModelTime(tSec, duration, cfg);
      } else {
        window.__studioAdvanceFrame(idx === 0 ? 0.0 : (1000.0 / (cfg.fps || 30)));
      }
      const pose = evalCameraPathPose(cfg, tSec, duration);
      applyCameraPoseToThree(pose);

      const rdr = (typeof renderer !== 'undefined') ? renderer : window.renderer;
      const scn = (typeof scene !== 'undefined') ? scene : window.scene;
      const cam = (typeof camera !== 'undefined') ? camera : window.camera;
      if (rdr && scn && cam) {
        if (rdr.domElement.width !== targetW || rdr.domElement.height !== targetH) {
          rdr.setPixelRatio(1);
          rdr.setSize(targetW, targetH, false);
        }
        cam.aspect = targetW / targetH;
        cam.updateProjectionMatrix();
        rdr.render(scn, cam);
      }
    };
  }
})();
"""


STUDIO_POST_SCRIPT_BODY = r"""
(async () => {
  const params = new URLSearchParams(window.location.search);
  const width = parseInt(params.get('__studio_w') || '2560', 10);
  const height = parseInt(params.get('__studio_h') || '1440', 10);
  const fps = parseFloat(params.get('__studio_fps') || '30');
  const duration = parseFloat(params.get('__studio_dur') || '30.0');
  const warmup = parseInt(params.get('__studio_warmup') || '15', 10);
  const totalFrames = Math.round(duration * fps);
  const dtMs = 1000.0 / fps;

  const log = async (msg) => {
    try {
      await fetch('/__studio_log', { method: 'POST', body: String(msg) });
    } catch (e) {}
  };

  try {
    const threeObj = (typeof THREE !== 'undefined') ? THREE : window.THREE;
    if (threeObj && threeObj.DefaultLoadingManager) {
      await new Promise((resolve) => {
        let settled = false;
        const done = () => {
          if (!settled) {
            settled = true;
            setTimeout(resolve, 400);
          }
        };
        threeObj.DefaultLoadingManager.onLoad = done;
        setTimeout(done, 1800);
      });
    } else {
      await new Promise((r) => setTimeout(r, 400));
    }

    const rdr = (typeof renderer !== 'undefined') ? renderer : window.renderer;
    const cam = (typeof camera !== 'undefined') ? camera : window.camera;
    if (rdr && typeof rdr.setSize === 'function') {
      rdr.setPixelRatio(1);
      rdr.setSize(width, height, false);
    }
    if (cam && typeof cam.updateProjectionMatrix === 'function') {
      cam.aspect = width / height;
      cam.updateProjectionMatrix();
    }

    if (typeof window.__studioSetup === 'function') {
      await window.__studioSetup(width, height, duration, fps);
    }

    const canvas = (rdr && rdr.domElement) || window.__studioGlCanvas || document.querySelector('canvas');
    if (!canvas) {
      throw new Error('No <canvas> found in document for Mode 2 motion capture.');
    }
    const gl = (rdr && typeof rdr.getContext === 'function' && rdr.getContext()) || window.__studioGlContext || canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (!gl) {
      throw new Error('No WebGL context found on <canvas>.');
    }

    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    const gpuName = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
    await log(`WebGL initialized (${width}x${height} @ ${fps}fps, ${totalFrames} frames) on GPU: ${gpuName}`);

    if (typeof window.__studioStep !== 'function') {
      for (let w = 0; w < warmup; w++) {
        window.__studioAdvanceFrame(dtMs);
      }
      window.__studioVirtualTimeMs = 0.0;
      window.__studioFrameIndex = 0;
    }

    const testFramesRaw = params.get('__studio_test_frames') || '';
    const frameList = testFramesRaw
      ? testFramesRaw.split(',').map((s) => parseInt(s, 10)).filter((n) => !Number.isNaN(n))
      : null;
    const totalToRun = frameList ? frameList.length : totalFrames;

    const pixelBuf = new Uint8Array(width * height * 4);

    for (let stepIdx = 0; stepIdx < totalToRun; stepIdx++) {
      const idx = frameList ? frameList[stepIdx] : stepIdx;
      const tSec = idx / fps;
      if (typeof window.__studioStep === 'function') {
        await window.__studioStep(idx, tSec, duration, width, height);
      } else {
        window.__studioAdvanceFrame(dtMs);
      }

      if (canvas.width !== width || canvas.height !== height) {
        if (rdr && typeof rdr.setSize === 'function') {
          rdr.setPixelRatio(1);
          rdr.setSize(width, height, false);
        } else {
          canvas.width = width;
          canvas.height = height;
        }
      }

      gl.finish();
      gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixelBuf);

      for (let attempt = 0; attempt < 5; attempt++) {
        try {
          const resp = await fetch(`/__studio_rgba/${idx}`, {
            method: 'POST',
            body: pixelBuf
          });
          if (resp.ok) break;
        } catch (err) {
          await new Promise((r) => setTimeout(r, 50 * (attempt + 1)));
        }
      }
    }

    await fetch('/__studio_done', { method: 'POST', body: 'DONE' });
  } catch (err) {
    await log('STUDIO_MOTION_ERR: ' + String((err && err.stack) || err));
    await fetch('/__studio_done', { method: 'POST', body: 'ERR' });
  }
})();
"""


def _rgba_bottom_up_to_png_bytes(data: bytes, width: int, height: int) -> bytes:
    """Convert bottom-up WebGL RGBA bytes into a lossless PNG using stdlib `zlib`."""
    stride = width * 4
    raw_rows = bytearray((stride + 1) * height)
    pos = 0
    for y in range(height - 1, -1, -1):
        raw_rows[pos] = 0
        pos += 1
        row_start = y * stride
        raw_rows[pos : pos + stride] = data[row_start : row_start + stride]
        pos += stride

    def make_chunk(chunk_type: bytes, chunk_data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        return struct.pack(">I", len(chunk_data)) + chunk_type + chunk_data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    compressed = zlib.compress(bytes(raw_rows), level=1)
    return (
        b"\x89PNG\r\n\x1a\n"
        + make_chunk(b"IHDR", ihdr)
        + make_chunk(b"IDAT", compressed)
        + make_chunk(b"IEND", b"")
    )


def purge_torn_frames(
    frames_dir: Path,
    mode: str = "motion",
    width: int = 2560,
    audit_only: bool = False,
) -> list[int]:
    """Detect and optionally delete PNG frames affected by headless window-resize tears (duplicate top-32-row CRC groups)."""
    frames_dir = Path(frames_dir)
    if not frames_dir.exists():
        return []
    row_stride = 1 + width * 4
    top32_len = 32 * row_stride
    crc_to_frames: dict[int, list[tuple[int, Path]]] = {}
    torn_indices: list[int] = []

    for png_path in sorted(frames_dir.glob("frame_*.png")):
        try:
            frame_num = int(png_path.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        data = png_path.read_bytes()
        if len(data) < 64:
            torn_indices.append(frame_num)
            if not audit_only:
                png_path.unlink(missing_ok=True)
            continue
        try:
            raw = zlib.decompress(data[41:-16])
        except Exception:
            # Non-single-IDAT PNGs (e.g. Blender Cycles multi-chunk PNGs) are valid if non-empty
            continue

        if len(raw) < top32_len:
            continue
        top_crc = zlib.crc32(raw[:top32_len])
        crc_to_frames.setdefault(top_crc, []).append((frame_num, png_path))

    for _top_crc, items in crc_to_frames.items():
        if len(items) > 1:
            for frame_num, png_path in items:
                torn_indices.append(frame_num)
                if not audit_only:
                    png_path.unlink(missing_ok=True)

    torn_indices.sort()
    return torn_indices


def generate_ambient_drone_wav(
    wav_path: Path,
    duration_sec: float,
    base_freq: float = 55.0,
    sample_rate: int = 48000,
) -> Path:
    """Synthesize a subtle stereo harmonic ambient space bed (48 kHz 16-bit PCM WAV)."""
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(duration_sec * sample_rate)
    phi = (1.0 + math.sqrt(5.0)) / 2.0

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)

        chunk = bytearray()
        for i in range(n_samples):
            t = i / sample_rate
            fade_in = min(1.0, t / 1.5)
            fade_out = min(1.0, max(0.0, (duration_sec - t) / 2.0))
            env = fade_in * fade_out * 0.14

            sig_l = (
                0.55 * math.sin(2.0 * math.pi * base_freq * t)
                + 0.28 * math.sin(2.0 * math.pi * (base_freq * 1.5) * t + 0.3)
                + 0.17 * math.sin(2.0 * math.pi * (base_freq * phi) * t + 0.1 * math.sin(0.25 * t))
            )
            sig_r = (
                0.55 * math.sin(2.0 * math.pi * base_freq * t + 0.12)
                + 0.28 * math.sin(2.0 * math.pi * (base_freq * 1.5) * t + 0.45)
                + 0.17 * math.sin(2.0 * math.pi * (base_freq * phi) * t + 0.9)
            )
            val_l = max(-32767, min(32767, int(sig_l * env * 32767)))
            val_r = max(-32767, min(32767, int(sig_r * env * 32767)))
            chunk.extend(struct.pack("<hh", val_l, val_r))

            if len(chunk) >= 65536:
                wf.writeframesraw(chunk)
                chunk.clear()
        if chunk:
            wf.writeframesraw(chunk)

    return wav_path


def build_motion_ffmpeg_cmd(
    output_mp4: Path,
    width: int,
    height: int,
    fps: int = 30,
    input_format: str = "rawvideo_vflip",
    audio_wav: Path | None = None,
    duration_sec: float | None = None,
    crf: int = 16,
    preset: str = "slow",
) -> list[str]:
    """Construct the clean Mode 2 (`motion`) FFmpeg encoding command.

    Zero `-vf` filters are applied except `-vf vflip` when reading upside-down
    WebGL `gl.readPixels` raw RGBA buffers. Always outputs `yuv420p` + `+faststart`.
    """
    cmd = ["/usr/bin/ffmpeg", "-y"]
    if input_format in ("rawvideo", "rawvideo_vflip"):
        cmd.extend([
            "-f", "rawvideo",
            "-pix_fmt", "rgba",
            "-s", f"{width}x{height}",
            "-framerate", str(fps),
            "-i", "pipe:0",
        ])
    else:
        cmd.extend([
            "-f", "image2pipe",
            "-framerate", str(fps),
            "-i", "pipe:0",
        ])

    if audio_wav and Path(audio_wav).exists():
        cmd.extend(["-i", str(audio_wav), "-shortest"])

    if duration_sec is not None:
        cmd.extend(["-t", f"{duration_sec:.3f}"])

    if input_format == "rawvideo_vflip":
        cmd.extend(["-vf", "vflip"])

    cmd.extend([
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", str(preset),
        "-pix_fmt", "yuv420p",
    ])
    if audio_wav and Path(audio_wav).exists():
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    cmd.extend(["-movflags", "+faststart", str(output_mp4)])
    return cmd


def encode_frames_dir_to_mp4(
    frames_dir: Path,
    output_mp4: Path,
    audio_wav: Path | None = None,
    fps: int = 30,
    duration_sec: float | None = None,
    crf: int = 16,
    preset: str = "slow",
    thumb_png: Path | None = None,
    thumb_frame_idx: int | None = None,
    thumb_sec: float | None = None,
) -> dict[str, Any]:
    """Encode an existing PNG frame sequence (`frame_%04d.png` or `frame_%05d.png`) into a clean MP4."""
    frames_dir = Path(frames_dir).resolve()
    output_mp4 = Path(output_mp4).resolve()
    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    sample_pngs = sorted(frames_dir.glob("frame_*.png"))
    if not sample_pngs:
        raise FileNotFoundError(f"No frame_*.png files found in {frames_dir}")

    m = re.match(r"^frame_(\d+)\.png$", sample_pngs[0].name)
    digits = len(m.group(1)) if m else 4
    start_number = int(m.group(1)) if m else 1
    pattern = str(frames_dir / f"frame_%0{digits}d.png")

    cmd = [
        "/usr/bin/ffmpeg",
        "-y",
        "-framerate",
        str(fps),
        "-start_number",
        str(start_number),
        "-i",
        pattern,
    ]
    if audio_wav and Path(audio_wav).exists():
        cmd.extend(["-i", str(audio_wav), "-shortest"])
    if duration_sec is not None:
        cmd.extend(["-t", f"{duration_sec:.3f}"])
    cmd.extend([
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-preset",
        str(preset),
        "-pix_fmt",
        "yuv420p",
    ])
    if audio_wav and Path(audio_wav).exists():
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    cmd.extend(["-movflags", "+faststart", str(output_mp4)])

    t0 = time.monotonic()
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    if thumb_png:
        thumb_png = Path(thumb_png).resolve()
        thumb_png.parent.mkdir(parents=True, exist_ok=True)
        if thumb_frame_idx is not None:
            cand = frames_dir / f"frame_{thumb_frame_idx:0{digits}d}.png"
            if cand.exists():
                shutil.copy2(cand, thumb_png)
            else:
                extract_thumbnail_from_mp4(output_mp4, thumb_png, timestamp_sec=thumb_frame_idx / float(fps))
        else:
            est_dur = duration_sec or (len(sample_pngs) / float(fps))
            hero_t = thumb_sec if thumb_sec is not None else round(est_dur * 0.618, 2)
            extract_thumbnail_from_mp4(output_mp4, thumb_png, timestamp_sec=hero_t)
        from .render import ensure_compressed_thumbnail

        if thumb_png.exists():
            ensure_compressed_thumbnail(thumb_png)

    return {
        "ok": True,
        "mode": "motion_frames_encode",
        "frames_dir": str(frames_dir),
        "frames_count": len(sample_pngs),
        "output_mp4": str(output_mp4),
        "thumb_png": str(thumb_png) if thumb_png else None,
        "elapsed_sec": round(time.monotonic() - t0, 2),
    }


def extract_thumbnail_from_mp4(
    mp4_path: Path,
    thumb_png: Path,
    timestamp_sec: float,
) -> Path:
    """Extract an exact PNG/JPG frame from a rendered MP4 at `timestamp_sec`."""
    thumb_png = Path(thumb_png)
    thumb_png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "/usr/bin/ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, timestamp_sec):.3f}",
            "-i",
            str(mp4_path),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(thumb_png),
        ],
        check=True,
        capture_output=True,
    )
    from .render import ensure_compressed_thumbnail

    if thumb_png.suffix.lower() == ".png" and thumb_png.exists():
        ensure_compressed_thumbnail(thumb_png)
    return thumb_png


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _StudioMotionHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args: Any,
        directory: str,
        entry_html_rel: str,
        injected_js: str,
        ffmpeg_proc: subprocess.Popen[bytes] | None,
        test_frames_dir: Path | None,
        width: int,
        height: int,
        done_event: threading.Event,
        state: dict[str, Any],
        total_frames: int,
        **kwargs: Any,
    ) -> None:
        self._entry_html_rel = entry_html_rel
        self._injected_js = injected_js
        self._ffmpeg_proc = ffmpeg_proc
        self._test_frames_dir = test_frames_dir
        self._width = width
        self._height = height
        self._done_event = done_event
        self._state = state
        self._total_frames = total_frames
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.endswith("/__studio_motion.html"):
            orig_html = (Path(self.directory) / self._entry_html_rel).read_text(encoding="utf-8")
            if "<head>" in orig_html:
                patched = orig_html.replace("<head>", "<head>\n" + STUDIO_PRE_SCRIPT_JS, 1)
            else:
                patched = STUDIO_PRE_SCRIPT_JS + "\n" + orig_html

            combined_driver = f"{self._injected_js}\n{UNIVERSAL_CAMERA_PATH_JS}"
            has_step_hook = (
                "__studioStep" in self._injected_js
                or "__STUDIO_CAMERA_PATH_CONFIG" in self._injected_js
                or "CAMERA_PATH_CONFIG" in self._injected_js
            )
            if "    animate();\n  </script>" in patched:
                keep_animate = "" if has_step_hook else "    animate();\n"
                combined_in_module = f"{keep_animate}\n{combined_driver}\n{STUDIO_POST_SCRIPT_BODY}\n  </script>"
                patched = patched.replace("    animate();\n  </script>", combined_in_module, 1)
            else:
                custom_block = f"<script>\n{combined_driver}\n{STUDIO_POST_SCRIPT_BODY}\n</script>\n"
                if "</body>" in patched:
                    patched = patched.replace("</body>", custom_block + "\n</body>", 1)
                else:
                    patched = patched + "\n" + custom_block

            body = patched.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = self.rfile.read(length) if length > 0 else b""

            if self.path.startswith("/__studio_log"):
                msg = data.decode("utf-8", errors="replace")
                print(f"  [Studio Motion] {msg}", flush=True)
                self._state["logs"].append(msg)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"OK")
                return

            if self.path.startswith("/__studio_rgba/"):
                idx = int(self.path.split("/")[-1])
                if self._test_frames_dir is not None:
                    png_bytes = _rgba_bottom_up_to_png_bytes(data, self._width, self._height)
                    (self._test_frames_dir / f"test_frame_{idx:04d}.png").write_bytes(png_bytes)
                elif self._ffmpeg_proc and self._ffmpeg_proc.stdin:
                    self._ffmpeg_proc.stdin.write(data)
                self._state["captured"] += 1
                self._state["last_ts"] = time.monotonic()
                c = self._state["captured"]
                if c == 1 or c % 30 == 0 or c == self._total_frames:
                    elapsed = time.monotonic() - self._state["start_ts"]
                    eff_fps = c / max(0.001, elapsed)
                    print(
                        f"  [Studio Motion] Streamed frame {c}/{self._total_frames} "
                        f"({eff_fps:.1f} fps)",
                        flush=True,
                    )
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"OK")
                if c >= self._total_frames:
                    self._done_event.set()
                return

            if self.path.startswith("/__studio_done"):
                status_str = data.decode("utf-8", errors="replace").strip()
                self._state["done_status"] = status_str
                self.send_response(200)
                self.send_header("Content-Length", "4")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"DONE")
                self._done_event.set()
                return

            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass


def capture_motion_viewport(
    url_or_html: str,
    output_mp4: Path,
    width: int = 2560,
    height: int = 1440,
    duration_sec: float = 30.0,
    fps: int = 30,
    warmup_frames: int = 15,
    injected_js: str | None = None,
    camera_path_cfg: dict[str, Any] | None = None,
    query_params: dict[str, str] | None = None,
    audio_wav: Path | None = None,
    ambient_drone: bool = True,
    thumb_png: Path | None = None,
    thumb_sec: float | None = None,
    scale_factor: float = 1.0,
    serve_root: Path | None = None,
    test_frame_indices: list[int] | None = None,
    test_frames_dir: Path | None = None,
    crf: int = 16,
    preset: str = "slow",
) -> dict[str, Any]:
    """Capture a deterministic 30.00 fps full-motion WebGL movie via hardware GPU directly to FFmpeg."""
    output_mp4 = Path(output_mp4).resolve()
    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    html_path = Path(url_or_html.replace("file://", "")).resolve()
    if not html_path.exists():
        raise FileNotFoundError(f"HTML file not found for Mode 2 motion capture: {html_path}")

    if serve_root is not None and html_path.is_relative_to(Path(serve_root).resolve()):
        serve_dir = Path(serve_root).resolve()
    elif html_path.parent.name in ("docs", "demos", "videos") and (html_path.parent.parent / "docs").exists():
        serve_dir = html_path.parent.parent
    else:
        serve_dir = html_path.parent
    entry_html_rel = html_path.relative_to(serve_dir).as_posix()
    url_prefix = "/" + "/".join(entry_html_rel.split("/")[:-1]) if "/" in entry_html_rel else ""

    combined_js_parts: list[str] = []
    if camera_path_cfg:
        combined_js_parts.append(f"window.CAMERA_PATH_CONFIG = {json.dumps(camera_path_cfg)};")
        combined_js_parts.append("window.__STUDIO_CAMERA_PATH_CONFIG = window.CAMERA_PATH_CONFIG;")
    if injected_js:
        combined_js_parts.append(injected_js)
    final_injected_js = "\n".join(combined_js_parts)

    is_test_only = bool(test_frame_indices and test_frames_dir is not None)
    temp_wav: Path | None = None
    if not is_test_only and audio_wav is None and ambient_drone:
        temp_wav = output_mp4.with_suffix(".ambient_tmp.wav")
        generate_ambient_drone_wav(temp_wav, duration_sec=duration_sec)
        audio_wav = temp_wav

    total_frames = len(test_frame_indices) if is_test_only and test_frame_indices else int(round(duration_sec * fps))

    t0 = time.monotonic()
    ffmpeg_proc: subprocess.Popen[bytes] | None = None
    if is_test_only and test_frames_dir is not None:
        Path(test_frames_dir).mkdir(parents=True, exist_ok=True)
    else:
        ffmpeg_cmd = build_motion_ffmpeg_cmd(
            output_mp4=output_mp4,
            width=width,
            height=height,
            fps=fps,
            input_format="rawvideo_vflip",
            audio_wav=audio_wav,
            duration_sec=duration_sec,
            crf=crf,
            preset=preset,
        )
        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    done_event = threading.Event()
    state: dict[str, Any] = {
        "captured": 0,
        "start_ts": t0,
        "last_ts": t0,
        "done_status": "",
        "logs": [],
    }

    handler = functools.partial(
        _StudioMotionHTTPHandler,
        directory=str(serve_dir),
        entry_html_rel=entry_html_rel,
        injected_js=final_injected_js,
        ffmpeg_proc=ffmpeg_proc,
        test_frames_dir=Path(test_frames_dir) if test_frames_dir else None,
        width=width,
        height=height,
        done_event=done_event,
        state=state,
        total_frames=total_frames,
    )
    server = _ThreadedTCPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
    srv_thread.start()

    chrome_bin = find_chrome_binary()
    tmp_profile = Path(f"/tmp/studio_motion_profile_{os.getpid()}_{int(t0 * 1000)}")
    tmp_profile.mkdir(parents=True, exist_ok=True)

    css_w = int(round(width / scale_factor)) if scale_factor > 0 else width
    css_h = int(round(height / scale_factor)) if scale_factor > 0 else height

    q_dict = {
        "__studio_w": str(width),
        "__studio_h": str(height),
        "__studio_css_w": str(css_w),
        "__studio_css_h": str(css_h),
        "__studio_dpr": f"{scale_factor:.6g}",
        "__studio_fps": str(fps),
        "__studio_dur": f"{duration_sec:.4f}",
        "__studio_warmup": str(warmup_frames),
    }
    if is_test_only and test_frame_indices:
        q_dict["__studio_test_frames"] = ",".join(str(i) for i in test_frame_indices)
    if query_params:
        q_dict.update(query_params)
    query_str = urllib.parse.urlencode(q_dict)
    target_url = f"http://127.0.0.1:{port}{url_prefix}/__studio_motion.html?{query_str}"

    chrome_cmd = [
        chrome_bin,
        "--headless=new",
        f"--user-data-dir={tmp_profile}",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--enable-webgl",
        "--ignore-gpu-blocklist",
        "--use-gl=angle",
        "--use-angle=gl",
        "--enable-gpu-rasterization",
        "--hide-scrollbars",
        f"--force-device-scale-factor={scale_factor:.6g}",
        f"--window-size={css_w},{css_h + 160}",
        target_url,
    ]

    chrome_proc = subprocess.Popen(
        chrome_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    rc = 0
    stderr_bytes = b""
    try:
        while not done_event.wait(timeout=0.5):
            now = time.monotonic()
            if chrome_proc.poll() is not None:
                break
            if now - state["last_ts"] > 120.0:
                raise RuntimeError("Studio Motion WebGL worker timed out waiting for next frame.")
    finally:
        chrome_proc.terminate()
        try:
            chrome_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            chrome_proc.kill()
        server.shutdown()
        server.server_close()
        shutil.rmtree(tmp_profile, ignore_errors=True)

        if ffmpeg_proc is not None:
            if ffmpeg_proc.stdin:
                ffmpeg_proc.stdin.close()
            stderr_bytes = ffmpeg_proc.stderr.read() if ffmpeg_proc.stderr else b""
            rc = ffmpeg_proc.wait(timeout=120)
        if temp_wav and temp_wav.exists():
            temp_wav.unlink(missing_ok=True)

    if state["done_status"] == "ERR" or state["captured"] < total_frames or rc != 0:
        raise RuntimeError(
            f"Mode 2 motion capture failed (captured={state['captured']}/{total_frames}, "
            f"ffmpeg_rc={rc}, logs={state['logs'][-3:]}): "
            f"{stderr_bytes.decode('utf-8', errors='replace')[-500:]}"
        )

    if not is_test_only and thumb_png:
        hero_t = thumb_sec if thumb_sec is not None else round(duration_sec * 0.618, 2)
        extract_thumbnail_from_mp4(output_mp4, Path(thumb_png), timestamp_sec=hero_t)

    elapsed = round(time.monotonic() - t0, 2)
    return {
        "ok": True,
        "mode": "motion_test_frames" if is_test_only else "motion",
        "output_mp4": None if is_test_only else str(output_mp4),
        "test_frames_dir": str(test_frames_dir) if is_test_only else None,
        "width": width,
        "height": height,
        "fps": fps,
        "frames": state["captured"],
        "duration_sec": duration_sec,
        "thumb_png": str(thumb_png) if (thumb_png and not is_test_only) else None,
        "elapsed_sec": elapsed,
        "effective_fps": round(state["captured"] / max(0.001, elapsed), 2),
        "logs": state["logs"],
    }


def resolve_motion_spec(target_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    """Resolve `(package_dir, spec_file, spec_dict)` from a package directory or a JSON spec file (`motion.json` or `camera_path.json`)."""
    p = Path(target_path).resolve()
    if p.is_file():
        return p.parent, p, json.loads(p.read_text(encoding="utf-8"))
    for name in ("motion.json", "camera_path.json"):
        cand = p / name
        if cand.exists():
            return p, cand, json.loads(cand.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"Neither motion.json nor camera_path.json found in {p}")


def _resolve_model_html_from_spec(package_dir: Path, spec: dict[str, Any]) -> Path:
    """Resolve `html` or `model` path relative to `package_dir` or project root."""
    raw_html = spec.get("html") or spec.get("model")
    if not raw_html:
        # Check parent project docs/tower-3d.html fallback
        for anc in (package_dir.parent, package_dir.parent.parent):
            cand = anc / "docs" / "tower-3d.html"
            if cand.exists():
                return cand.resolve()
        raise KeyError(f"Motion spec in {package_dir} must specify 'html' or 'model'.")
    p = Path(raw_html)
    if p.is_absolute():
        return p
    if (package_dir / p).exists():
        return (package_dir / p).resolve()
    for anc in (package_dir.parent, package_dir.parent.parent):
        if (anc / p).exists():
            return (anc / p).resolve()
    return (package_dir / p).resolve()


def render_motion_package(
    package_dir: Path,
    prefix: str | None = None,
    *,
    encode_only: bool = False,
    test_frames_only: bool = False,
    clean: bool = False,
) -> dict[str, Any]:
    """Render a Mode 2 (`motion`) package from `motion.json` or `camera_path.json` (or `--encode-only` on `frames/`)."""
    target = Path(package_dir).resolve()
    spec: dict[str, Any] = {}
    if target.is_dir() and encode_only and not (target / "motion.json").exists() and not (target / "camera_path.json").exists():
        pkg_dir = target
    else:
        pkg_dir, _spec_path, spec = resolve_motion_spec(target)

    pfx = prefix or spec.get("prefix") or spec.get("mode") or pkg_dir.name
    duration_sec = float(spec.get("duration_sec") or spec.get("duration") or 30.0)
    fps = int(spec.get("fps", 30))
    warmup_frames = int(spec.get("warmup_frames", 15))
    scale_factor = float(spec.get("scale_factor", 1.0))

    if spec.get("thumb_frame") is not None:
        thumb_sec = float(spec["thumb_frame"]) / float(fps)
    else:
        thumb_sec = float(spec.get("thumb_sec", round(duration_sec * 0.618, 2)))

    wav_rel = spec.get("audio_wav") or spec.get("wav_name")
    audio_wav: Path | None = None
    if wav_rel and (pkg_dir / wav_rel).exists():
        audio_wav = pkg_dir / wav_rel
    else:
        for cand_wav in (pkg_dir / f"{pfx}.wav", pkg_dir / f"gor9_{pfx}_audio.wav"):
            if cand_wav.exists():
                audio_wav = cand_wav
                break

    wide_mp4_name = spec.get("wide_mp4") or spec.get("mp4_name") or f"{pfx}_1080p.mp4"
    wide_thumb_name = spec.get("wide_thumb") or spec.get("thumb_name") or f"{pfx}_thumb.png"
    wide_mp4 = pkg_dir / wide_mp4_name
    wide_thumb = pkg_dir / wide_thumb_name

    frames_dir = pkg_dir / "frames"
    if clean and frames_dir.exists():
        shutil.rmtree(frames_dir)

    if encode_only:
        torn = purge_torn_frames(frames_dir, mode=pfx, width=int(spec.get("width", 2560)), audit_only=False)
        enc_res = encode_frames_dir_to_mp4(
            frames_dir=frames_dir,
            output_mp4=wide_mp4,
            audio_wav=audio_wav,
            fps=fps,
            duration_sec=duration_sec if spec else None,
            thumb_png=wide_thumb,
            thumb_frame_idx=int(spec["thumb_frame"]) if spec.get("thumb_frame") is not None else None,
            thumb_sec=thumb_sec,
        )
        enc_res["purged_torn_frames"] = len(torn)
        return {
            "ok": True,
            "mode": "motion",
            "package_dir": str(pkg_dir),
            "prefix": pfx,
            "wide": enc_res,
            "short": None,
        }

    html_path = _resolve_model_html_from_spec(pkg_dir, spec)
    injected_js = spec.get("injected_js", "")
    if spec.get("injected_js_file"):
        js_file = (pkg_dir / spec["injected_js_file"]).resolve()
        injected_js = js_file.read_text(encoding="utf-8")

    has_camera_path = bool(spec.get("keyframes") or spec.get("coordinate_system"))
    camera_path_cfg = spec if has_camera_path else None
    query_params = spec.get("query_params") or {}

    if test_frames_only:
        total_f = int(round(duration_sec * fps))
        test_indices = sorted({0, total_f // 4, total_f // 2, (3 * total_f) // 4, max(0, total_f - 1)})
        test_out_dir = pkg_dir / "test_frames"
        wide_res = capture_motion_viewport(
            url_or_html=str(html_path),
            output_mp4=wide_mp4,
            width=int(spec.get("width", 2560)),
            height=int(spec.get("height", 1440)),
            duration_sec=duration_sec,
            fps=fps,
            warmup_frames=warmup_frames,
            injected_js=injected_js,
            camera_path_cfg=camera_path_cfg,
            query_params=query_params,
            audio_wav=None,
            ambient_drone=False,
            scale_factor=scale_factor,
            test_frame_indices=test_indices,
            test_frames_dir=test_out_dir,
        )
        return {
            "ok": True,
            "mode": "motion_test_frames",
            "package_dir": str(pkg_dir),
            "prefix": pfx,
            "wide": wide_res,
            "short": None,
        }

    wide_res = capture_motion_viewport(
        url_or_html=str(html_path),
        output_mp4=wide_mp4,
        width=int(spec.get("width", 2560)),
        height=int(spec.get("height", 1440)),
        duration_sec=duration_sec,
        fps=fps,
        warmup_frames=warmup_frames,
        injected_js=injected_js,
        camera_path_cfg=camera_path_cfg,
        query_params=query_params,
        audio_wav=audio_wav,
        ambient_drone=bool(spec.get("ambient_drone", audio_wav is None)),
        thumb_png=wide_thumb,
        thumb_sec=thumb_sec,
        scale_factor=scale_factor,
    )

    short_res = None
    if spec.get("render_short", False):
        short_mp4 = pkg_dir / (spec.get("short_mp4") or f"{pfx}_short.mp4")
        short_thumb = pkg_dir / (spec.get("short_thumb") or f"{pfx}_short_thumb.png")
        short_duration = float(spec.get("short_duration_sec", min(duration_sec, 58.0)))
        short_res = capture_motion_viewport(
            url_or_html=str(html_path),
            output_mp4=short_mp4,
            width=1080,
            height=1920,
            duration_sec=short_duration,
            fps=fps,
            warmup_frames=warmup_frames,
            injected_js=injected_js,
            camera_path_cfg=camera_path_cfg,
            query_params=query_params,
            audio_wav=audio_wav,
            ambient_drone=bool(spec.get("ambient_drone", audio_wav is None)),
            thumb_png=short_thumb,
            thumb_sec=min(thumb_sec, short_duration * 0.618),
            scale_factor=scale_factor,
        )

    return {
        "ok": True,
        "mode": "motion",
        "package_dir": str(pkg_dir),
        "prefix": pfx,
        "wide": wide_res,
        "short": short_res,
    }
