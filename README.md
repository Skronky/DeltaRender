# DeltaRender
### Smarter Rendering for Blender. Open Source. Free Forever.

---

## What It Does

DeltaRender is a Blender 5.0 addon that cuts render times by skipping frames that don't need rendering.

If you animate on 2's — using stepped interpolation so every other frame is a held pose — DeltaRender detects the held frames and skips them entirely. Instead of rendering a duplicate frame, it copies the previous one in ~2ms. No GPU work. No quality loss.

**Real result on a GTX 970:**
- 22 frame scene: 125 seconds → 43 seconds
- Production scene (two characters, full environment, DOF): ~2 minutes → 24 seconds
- Consistent ~50% render time reduction on animation using stepped interpolation

---

## Who It's For

DeltaRender works best if you:

- Use **stepped interpolation** (animating on 2's) for a stop motion or stylized aesthetic
- Animate in **EEVEE**
- Have scenes with a **static camera and static background** while characters move
- Are on **budget hardware** and every minute of render time counts

If your entire animation is a camera move or every frame is unique on 1's, DeltaRender won't help much — those frames all need to render.

---

## How It Works

1. **Scene scan** — DeltaRender identifies every static and dynamic object in your scene
2. **Pose snapshot** — before each frame, it snapshots all bone matrices, camera position, light positions, and object transforms
3. **Delta check** — if nothing changed since the last frame, skip it entirely (2ms file copy)
4. **Smart rendering** — only frames where something actually changed go to the GPU

It also automatically disables EEVEE shadow jitter during render (a confirmed performance drain) and restores your settings when done. Your scene is never permanently changed.

---

## How To Use It

1. Install `deltarender.py` as a Blender addon
2. Go to **Properties → Render → DeltaRender**
3. Enable DeltaRender with the toggle
4. Click **Scan Scene** to identify static vs dynamic objects
5. Click **▶ Render Animation** — use this instead of Ctrl+F12

That's it. The console will show you what's being skipped in real time.

---

## Requirements

- Blender 5.0
- EEVEE renderer
- Animation using stepped interpolation (animating on 2's) for best results

---

## Benchmark Results

**Hardware:** NVIDIA GTX 970 — Blender 5.0 EEVEE — 1920×1080 — 64 samples

| Version | What Changed | Total Time (22 frames) | vs No DeltaRender |
|---|---|---|---|
| None | Baseline | ~125.6s | — |
| v0.2 | Persistent data | 93.6s | -25.6% |
| v0.3 | Depsgraph caching | 87.5s | -30.3% |
| v0.4 | Frame delta system | 82.8s | -34.1% |
| v0.5 | True frame skipping | 43.0s | -65.8% |
| v0.6 | EEVEE shadow fix | 42.9s | -65.8% |
| **v0.7** | **Full scene detection** | **43s equivalent** | **~65% faster** |

Full benchmark data in [BENCHMARKS.md](BENCHMARKS.md).

---

## What It Detects

DeltaRender tracks all of these between frames. If any of them change, the frame renders fully:

- All animated armature bone matrices
- Camera position, rotation, lens, and depth of field
- Light positions, energy, and color
- All animated object transforms

Static objects, static lights, and static cameras are never re-evaluated — they're cached on frame 1.

---

## Limitations

- **EEVEE only** — Cycles support not yet implemented
- **Animation on 2's** — maximum benefit with stepped interpolation. Smooth 1's animation gets little benefit since every frame is unique
- **Camera moves** — frames with camera movement always render fully
- **Blender 5.0** — uses the new channelbag API, not compatible with older versions

---

## Principles

- **Invisible** — works in the background, you render like normal
- **Non-destructive** — never permanently changes your scene or settings
- **Open source forever** — MIT license, no paywalls, no subscriptions

---

## License

MIT — free to use, modify, and distribute. Forever.
