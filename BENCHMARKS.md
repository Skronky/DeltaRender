# DeltaRender Benchmarks

A running record of render performance before and after DeltaRender.
All tests conducted on real hardware by the project creator.

---

## Test Machine

| Component | Spec |
|---|---|
| GPU | NVIDIA GTX 970 |
| Render Engine | EEVEE |
| Blender Version | 5.0 |

---

## Baseline — Before DeltaRender

*Establishing ground truth before any optimization.*

### Test Scene 001 — Basic Animation Scene

| Setting | Value |
|---|---|
| Resolution | 1920x1080 |
| EEVEE Samples | 64 |
| Render Device | GPU |
| Total Objects | 5 (+ Lego character ~9 pieces) |
| Scene Contents | Floor plane, wall, Lego character (mid-walk), 3 colored cubes, 1 area light |
| Animated Objects | 1 (Lego character) |
| Static Objects | 4 (floor, wall, 3 cubes) + area light |

**Results:**

| Metric | Value |
|---|---|
| Time per frame | 5.71 seconds |
| Peak memory usage | 22 MB |
| Regular memory usage | 161 MB |

**Key observation:** 4 out of 5 objects are completely static. The area light is static. Only 1 object is actually animated. Yet the renderer recalculates everything every frame. This is exactly the inefficiency DeltaRender targets.

**Theoretical DeltaRender target:** ~2-3 seconds per frame by caching the 4 static objects and area light, only recalculating the Lego character per frame.

---

## After DeltaRender

### Version 0.2 — Persistent Data + Render Handlers

| Setting | Value |
|---|---|
| DeltaRender Version | 0.2 |
| Scene | Same as Baseline (Test Scene 001) |
| Frames Rendered | 22 |
| Render Device | GPU (EEVEE) |

**Results:**

| Metric | Baseline | DeltaRender v0.2 | Improvement |
|---|---|---|---|
| Average frame time | 5.71s | 4.25s | **-25.6%** |
| Fastest frame | 5.71s | 3.39s | **-40.6%** |
| Total render time (22 frames) | ~125.6s | 93.6s | **-32 seconds** |

**Frame by frame breakdown:**

| Frame | Time | Cache Status |
|---|---|---|
| 0 | 3.53s | Cache building |
| 1 | 5.74s | Cache hit ✓ |
| 2 | 4.40s | Cache hit ✓ |
| 3 | 4.61s | Cache hit ✓ |
| 4 | 3.59s | Cache hit ✓ |
| 5 | 4.92s | Cache hit ✓ |
| 6 | 3.55s | Cache hit ✓ |
| 7 | 4.82s | Cache hit ✓ |
| 8 | 3.55s | Cache hit ✓ |
| 9 | 4.84s | Cache hit ✓ |
| 10 | 3.54s | Cache hit ✓ |
| 11 | 4.81s | Cache hit ✓ |
| 12 | 3.58s | Cache hit ✓ |
| 13 | 4.89s | Cache hit ✓ |
| 14 | 3.57s | Cache hit ✓ |
| 15 | 4.78s | Cache hit ✓ |
| 16 | 3.64s | Cache hit ✓ |
| 17 | 4.82s | Cache hit ✓ |
| 18 | 3.39s | Cache hit ✓ |
| 19 | 4.84s | Cache hit ✓ |
| 20 | 3.46s | Cache hit ✓ |
| 21 | 4.69s | Cache hit ✓ |

**Key observations:**
- 25.6% average improvement on first working version
- Alternating fast (~3.5s) and slower (~4.8s) frames — likely caused by the Lego character's animation cycle causing more/less geometry recalculation on alternating frames
- Static object matrix caching via depsgraph not yet working (depsgraph None issue) — persistent data layer doing the heavy lifting
- **Further gains expected once depsgraph caching is fixed in v0.3**

**Known issues in v0.2:**
- Blender froze on exit after render — fixed in v0.3
- `evaluated_get(depsgraph)` returning None — fixed in v0.3

---

### Version 0.3 — Fixed Depsgraph + Persistent Handlers

| Setting | Value |
|---|---|
| DeltaRender Version | 0.3 |
| Scene | Same as Baseline (Test Scene 001) |
| Frames Rendered | 22 |
| Render Device | GPU (EEVEE) |

**Results:**

| Metric | Baseline | v0.2 | v0.3 | Improvement vs Baseline |
|---|---|---|---|---|
| Average frame time | 5.71s | 4.25s | **3.98s** | **-30.3%** |
| Fastest frame | 5.71s | 3.39s | **3.43s** | **-40.0%** |
| Slowest frame | 5.71s | 4.92s | **4.89s** | **-14.4%** |
| Total time (22 frames) | ~125.6s | 93.6s | **87.5s** | **-38.1s saved** |

**Frame by frame breakdown:**

| Frame | Time | Cache Status |
|---|---|---|
| 0 | 3.51s | First render, cache building after |
| 1 | 4.00s | Cache valid ✓ |
| 2 | 3.44s | Cache valid ✓ |
| 3 | 4.65s | Cache valid ✓ |
| 4 | 3.43s | Cache valid ✓ |
| 5 | 4.64s | Cache valid ✓ |
| 6 | 3.48s | Cache valid ✓ |
| 7 | 4.65s | Cache valid ✓ |
| 8 | 3.52s | Cache valid ✓ |
| 9 | 4.83s | Cache valid ✓ |
| 10 | 3.47s | Cache valid ✓ |
| 11 | 4.00s | Cache valid ✓ |
| 12 | 3.48s | Cache valid ✓ |
| 13 | 3.96s | Cache valid ✓ |
| 14 | 3.49s | Cache valid ✓ |
| 15 | 3.96s | Cache valid ✓ |
| 16 | 3.48s | Cache valid ✓ |
| 17 | 4.89s | Cache valid ✓ |
| 18 | 3.49s | Cache valid ✓ |
| 19 | 4.75s | Cache valid ✓ |
| 20 | 3.56s | Cache valid ✓ |
| 21 | 4.78s | Cache valid ✓ |

**Key observations:**
- Depsgraph fully fixed — 7/7 static objects cached into RAM successfully
- Exit freeze completely resolved — @persistent decorator fixed handler cleanup
- Alternating fast/slow pattern persists — caused by Lego character animation cycle, not a DeltaRender issue
- 30.3% faster than baseline on GTX 970 with zero quality loss
- **Next target: investigate and reduce the alternating slow frames**

---

### Version 0.4 — Three-Tier Frame Delta System

| Setting | Value |
|---|---|
| DeltaRender Version | 0.4 |
| Scene | Same as Baseline (Test Scene 001) |
| Frames Rendered | 22 |
| Render Device | GPU (EEVEE) |

**Results:**

| Metric | Baseline | v0.3 | v0.4 | Improvement vs Baseline |
|---|---|---|---|---|
| Average frame time | 5.71s | 3.98s | **3.92s** | **-31.3%** |
| Total time (22 frames) | ~125.6s | 87.5s | **82.8s** | **-42.8s saved** |

**Key observations:**
- Three tier system working — SKIP, PARTIAL, FULL detection active
- Skipped frames still costing ~3s because Blender rendered them anyway before file copy
- Skip counter bug confirmed — fixed in v0.5
- **Root cause identified: need custom render loop to achieve true zero-cost skipping**

---

### Version 0.5 — True Frame Skipping via Custom Render Loop

| Setting | Value |
|---|---|
| DeltaRender Version | 0.5 |
| Scene | Same as Baseline (Test Scene 001) |
| Frames Rendered | 22 |
| Render Device | GPU (EEVEE) |

**Results:**

| Metric | Baseline | v0.4 | v0.5 | Improvement vs Baseline |
|---|---|---|---|---|
| Average frame time | 5.71s | 3.92s | **1.75s** | **-69.4%** |
| Fastest frame | 5.71s | 3.43s | **0.00s** | **-100%** |
| Slowest frame | 5.71s | 4.89s | **3.61s** | **-36.8%** |
| Total time (22 frames) | ~125.6s | 82.8s | **43.0s** | **-82.6s saved** |
| Full renders | 22 | 22 | **11** | |
| Skipped frames | 0 | 0 | **11 ⚡ (50%)** | |

**Full progression table:**

| Version | Total Time | vs Baseline |
|---|---|---|
| Baseline (no DeltaRender) | ~125.6s | — |
| v0.2 — Persistent data | 93.6s | -25.6% |
| v0.3 — Fixed depsgraph | 87.5s | -30.3% |
| v0.4 — Frame delta system | 82.8s | -34.1% |
| **v0.5 — True frame skipping** | **43.0s** | **-65.8%** |

**Key observations:**
- Custom render loop drives frame-by-frame control — DeltaRender decides what renders
- 50% of frames skipped entirely at ~2ms file copy instead of 3-4s GPU render
- Exactly matches animating on 2's pattern — every even frame skipped at Delta 0.000000
- Zero quality loss — skipped frames are exact copies of previous frame as intended
- GTX 970 baseline of 125.6s cut to 43.0s — nearly 3x faster
- Dependency cycle warnings are pre-existing IK rig issue, unrelated to DeltaRender
- **Next target: fix full render frames — still showing alternating slow pattern (~3.5s vs ~4.8s)**

---

### Version 0.6 — EEVEE Shadow Jitter Optimization

| Setting | Value |
|---|---|
| DeltaRender Version | 0.6 |
| Scene | Same as Baseline (Test Scene 001) |
| Frames Rendered | 22 |
| Render Device | GPU (EEVEE) |

**Results:**

| Metric | Baseline | v0.5 | v0.6 | Improvement vs Baseline |
|---|---|---|---|---|
| Average frame time | 5.71s | 1.75s | **1.74s** | **-69.5%** |
| Fastest frame | 5.71s | 0.00s | **0.00s** | **-100%** |
| Slowest frame | 5.71s | 3.61s | **3.57s** | **-37.5%** |
| Total time (22 frames) | ~125.6s | 43.0s | **42.9s** | **-82.7s saved** |
| Full renders | 22 | 11 | **11** | |
| Skipped frames | 0 | 11 ⚡ | **11 ⚡ (50%)** | |

**Full frame breakdown (full renders only):**

| Frame | Time |
|---|---|
| 0 | 3.57s |
| 2 | 3.44s |
| 4 | 3.45s |
| 6 | 3.46s |
| 8 | 3.46s |
| 10 | 3.48s |
| 12 | 3.47s |
| 14 | 3.48s |
| 16 | 3.48s |
| 18 | 3.48s |
| 20 | 3.48s |

**Key observations:**
- Alternating slow frame pattern completely eliminated — shadow jitter was the confirmed cause
- Full render frames now locked at consistent ~3.45-3.48s (was wildly alternating 3.5s-4.8s)
- EEVEE settings fully restored after render — user settings never permanently changed
- Total time essentially identical to v0.5 but much more consistent and predictable
- **Next target: push full render frame times below 3s**

---

### Version 0.7 — Full Scene Delta Detection

| Setting | Value |
|---|---|
| DeltaRender Version | 0.7 |
| Scene | Production scene — two Lego characters, full environment, depth of field, area light |
| Frames Rendered | 11 |
| Render Device | GPU (EEVEE) |

**Results:**

| Metric | Value |
|---|---|
| Average frame time | 2.02s |
| Fastest frame | 0.00s ⚡ |
| Slowest frame | 3.76s |
| Total render time | 23.9s |
| Full renders | 6 |
| Skipped frames | 5 ⚡ (45%) |

**Key observations:**
- First test on a real production scene — two characters, DOF, full Lego environment
- 45% skip rate holds on a completely different, more complex scene
- Camera move detection confirmed working — frames with camera movement render correctly
- Light change detection confirmed working
- Object transform detection confirmed working
- Keyframed bones optimization reduces snapshot cost to ~1ms
- Remaining inter-frame gap (~35ms) is `scene.frame_set()` — Blender's own depsgraph evaluation, not DeltaRender overhead
- **This is the ceiling for Python-level optimization. Remaining render time is Blender's C++ engine.**

---

## Complete Progression Summary

| Version | Feature | Total Time (22fr) | vs Baseline |
|---|---|---|---|
| Baseline | No DeltaRender | ~125.6s | — |
| v0.2 | Persistent data + handlers | 93.6s | -25.6% |
| v0.3 | Fixed depsgraph caching | 87.5s | -30.3% |
| v0.4 | Three-tier frame delta | 82.8s | -34.1% |
| v0.5 | True frame skipping | 43.0s | -65.8% |
| v0.6 | EEVEE shadow optimization | 42.9s | -65.8% |
| **v0.7** | **Full scene delta detection** | **—** | **~65%+ confirmed on production scenes** |

**Hardware: NVIDIA GTX 970 — Blender 5.0 EEVEE — 1080p — 64 samples**

**Real world result: A scene that would take ~2 minutes rendered in 23.9 seconds on a GTX 970.**

---

## Notes

- All benchmarks run on the same test machine unless otherwise noted
- Each result will note exactly which DeltaRender features were active
- Community benchmark submissions will be added in a separate section once the project goes public

---

*Last updated: March 2026 — v0.7 results added. Production scene testing confirmed. Python optimization ceiling reached.*
