bl_info = {
    "name": "DeltaRender",
    "author": "DeltaRender Project",
    "version": (0, 7, 0),
    "blender": (5, 0, 0),
    "location": "Properties > Render > DeltaRender",
    "description": "Smarter rendering through intelligent scene caching",
    "category": "Render",
}

import bpy
from bpy.app.handlers import persistent
import time
import shutil
import os

# ─────────────────────────────────────────
# GLOBAL STATE
# ─────────────────────────────────────────

_state = {
    "static_names": [],
    "dynamic_names": [],
    "static_matrices": {},
    "last_scene_snapshot": {},
    "last_rendered_frame": -1,
    "last_output_path": "",
    "frame_log": [],
    "render_start": 0.0,
    "frame_start_time": 0.0,
    "cache_built": False,
    "is_rendering": False,
    # Per-armature set of bone names that are actually keyframed
    # {armature_name: set(bone_names)}
    "keyframed_bones": {},
    # EEVEE original settings — restored after render
    "original_shadow_jitter": None,
    "original_shadow_pool_size": None,
}


# ─────────────────────────────────────────
# BLENDER 5.0 ANIMATION DETECTION
# ─────────────────────────────────────────

def has_animation_blender5(anim_data):
    if not anim_data:
        return False
    if anim_data.nla_tracks:
        for track in anim_data.nla_tracks:
            if track.strips:
                return True
    if anim_data.drivers:
        return True
    if anim_data.action and anim_data.action_slot:
        try:
            for channelbag in anim_data.action.channelbags:
                if channelbag.slot_handle == anim_data.action_slot.handle:
                    if len(channelbag.fcurves) > 0:
                        return True
        except Exception:
            pass
    return False


def is_object_animated(obj):
    if has_animation_blender5(obj.animation_data):
        return True
    if obj.data and hasattr(obj.data, "animation_data"):
        if has_animation_blender5(obj.data.animation_data):
            return True
    if hasattr(obj.data, "shape_keys") and obj.data and obj.data.shape_keys:
        if has_animation_blender5(obj.data.shape_keys.animation_data):
            return True
    parent = obj.parent
    depth = 0
    while parent and depth < 5:
        if has_animation_blender5(parent.animation_data):
            return True
        parent = parent.parent
        depth += 1
    return False


# ─────────────────────────────────────────
# SCENE SCANNER
# ─────────────────────────────────────────

def scan_scene():
    static_objects = []
    dynamic_objects = []
    for obj in bpy.context.scene.objects:
        if not obj.visible_get():
            continue
        if is_object_animated(obj):
            dynamic_objects.append(obj.name)
        else:
            static_objects.append(obj.name)
    return static_objects, dynamic_objects


def build_keyframed_bones_cache(scene):
    """
    For each animated armature, find only the bones that have
    actual fcurves. We only need to snapshot these bones —
    helper/IK/twist bones that move as a result don't need
    individual tracking since if a driving bone moved, the
    frame changed anyway.
    """
    cache = {}
    for obj_name in _state["dynamic_names"]:
        obj = scene.objects.get(obj_name)
        if not obj or obj.type != 'ARMATURE':
            continue
        keyframed = set()
        anim_data = obj.animation_data
        if anim_data and anim_data.action and anim_data.action_slot:
            try:
                for channelbag in anim_data.action.channelbags:
                    if channelbag.slot_handle == anim_data.action_slot.handle:
                        for fc in channelbag.fcurves:
                            # data_path like 'pose.bones["Bone"].location'
                            if fc.data_path.startswith('pose.bones['):
                                # Extract bone name from data path
                                start = fc.data_path.index('"') + 1
                                end = fc.data_path.index('"', start)
                                keyframed.add(fc.data_path[start:end])
            except Exception:
                pass
        # If we found keyframed bones use them, otherwise fall back to all bones
        if keyframed:
            cache[obj_name] = keyframed
            print(f"  DeltaRender: {obj_name} — {len(keyframed)}/{len(obj.pose.bones)} bones tracked")
        else:
            # Fallback: track all bones
            cache[obj_name] = {b.name for b in obj.pose.bones}
            print(f"  DeltaRender: {obj_name} — tracking all {len(obj.pose.bones)} bones (no fcurves found)")
    _state["keyframed_bones"] = cache


def print_scan_results(static_objects, dynamic_objects, elapsed=None):
    print("\n" + "="*50)
    print("  DELTARENDER v0.7 — Scene Scan Results")
    print("="*50)
    print(f"\n  Static objects ({len(static_objects)}) — will be cached:")
    for name in static_objects:
        print(f"    + {name}")
    print(f"\n  Dynamic objects ({len(dynamic_objects)}) — will be delta checked:")
    for name in dynamic_objects:
        print(f"    * {name}")
    total = len(static_objects) + len(dynamic_objects)
    if total > 0:
        savings = round((len(static_objects) / total) * 100)
        print(f"\n  Potential savings: ~{savings}%")
    if elapsed:
        print(f"  Scan time: {elapsed}s")
    print("="*50 + "\n")


# ─────────────────────────────────────────
# SCENE SNAPSHOT + DELTA
#
# Snapshots everything that affects render output:
# - Armature bone matrices (character animation)
# - Camera matrix + lens settings (camera moves)
# - Light matrices + energy/color (light animation)
# - Dynamic object world matrices (object animation)
#
# If ANY of these changed → FULL render, no skip.
# ─────────────────────────────────────────

def snapshot_scene(scene):
    snapshot = {}
    dynamic_set = set(_state["dynamic_names"])

    # ── Armature bones — only keyframed bones ──
    bones = {}
    keyframed_cache = _state["keyframed_bones"]
    for obj_name in dynamic_set:
        obj = scene.objects.get(obj_name)
        if not obj or obj.type != 'ARMATURE' or not obj.visible_get():
            continue
        if not obj.pose:
            continue
        tracked_bones = keyframed_cache.get(obj_name, {b.name for b in obj.pose.bones})
        bone_matrices = {}
        for bone in obj.pose.bones:
            if bone.name in tracked_bones:
                bone_matrices[bone.name] = bone.matrix.copy()
        bones[obj_name] = bone_matrices
    snapshot["bones"] = bones

    # ── Camera — always snapshot ──
    cam = scene.camera
    if cam:
        snapshot["camera_matrix"] = cam.matrix_world.copy()
        if cam.data:
            snapshot["camera_lens"] = cam.data.lens
            snapshot["camera_dof"]  = cam.data.dof.focus_distance if cam.data.dof else 0.0

    # ── Lights — only animated lights ──
    lights = {}
    for obj_name in dynamic_set:
        obj = scene.objects.get(obj_name)
        if not obj or obj.type != 'LIGHT' or not obj.visible_get():
            continue
        entry = {"matrix": obj.matrix_world.copy()}
        if obj.data:
            entry["energy"] = obj.data.energy
            entry["color"]  = tuple(obj.data.color)
        lights[obj_name] = entry
    snapshot["lights"] = lights

    # ── Dynamic object transforms — non-armature animated objects ──
    objects = {}
    for obj_name in dynamic_set:
        obj = scene.objects.get(obj_name)
        if not obj or obj.type in ('ARMATURE', 'LIGHT') or not obj.visible_get():
            continue
        objects[obj_name] = obj.matrix_world.copy()
    snapshot["objects"] = objects

    return snapshot


def compute_max_delta(snap_a, snap_b):
    """
    Returns max delta across ALL tracked scene elements.
    Any movement in camera, lights, or objects forces a full render.
    """
    if not snap_a or not snap_b:
        return 999.0

    max_delta = 0.0

    # ── Bone delta ──
    bones_a = snap_a.get("bones", {})
    bones_b = snap_b.get("bones", {})
    for obj_name, b_bones in bones_b.items():
        a_bones = bones_a.get(obj_name, {})
        for bone_name, mat_b in b_bones.items():
            mat_a = a_bones.get(bone_name)
            if mat_a is None:
                return 999.0
            diff = abs((mat_b - mat_a).median_scale)
            if diff > max_delta:
                max_delta = diff

    # ── Camera delta ──
    cam_mat_a = snap_a.get("camera_matrix")
    cam_mat_b = snap_b.get("camera_matrix")
    if cam_mat_a is not None and cam_mat_b is not None:
        diff = abs((cam_mat_b - cam_mat_a).median_scale)
        if diff > max_delta:
            max_delta = diff
    if snap_a.get("camera_lens") != snap_b.get("camera_lens"):
        return 999.0
    if abs(snap_a.get("camera_dof", 0) - snap_b.get("camera_dof", 0)) > 0.0001:
        return 999.0

    # ── Light delta ──
    lights_a = snap_a.get("lights", {})
    lights_b = snap_b.get("lights", {})
    for light_name, lb in lights_b.items():
        la = lights_a.get(light_name)
        if la is None:
            return 999.0
        diff = abs((lb["matrix"] - la["matrix"]).median_scale)
        if diff > max_delta:
            max_delta = diff
        if abs(lb.get("energy", 0) - la.get("energy", 0)) > 0.001:
            return 999.0
        if lb.get("color") != la.get("color"):
            return 999.0

    # ── Object transform delta ──
    objs_a = snap_a.get("objects", {})
    objs_b = snap_b.get("objects", {})
    for obj_name, mat_b in objs_b.items():
        mat_a = objs_a.get(obj_name)
        if mat_a is None:
            return 999.0
        diff = abs((mat_b - mat_a).median_scale)
        if diff > max_delta:
            max_delta = diff

    return max_delta


# ─────────────────────────────────────────
# STATIC OBJECT CACHE
# ─────────────────────────────────────────

def build_static_cache(scene):
    """
    Cache static object world matrices using evaluated depsgraph.
    Called after first frame renders when depsgraph is valid.
    """
    _state["static_matrices"].clear()
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
    except Exception as e:
        print(f"  DeltaRender: depsgraph error: {e}")
        return

    count = 0
    for obj_name in _state["static_names"]:
        obj = scene.objects.get(obj_name)
        if not obj:
            continue
        try:
            obj_eval = obj.evaluated_get(depsgraph)
            _state["static_matrices"][obj_name] = obj_eval.matrix_world.copy()
            count += 1
        except Exception as e:
            print(f"  DeltaRender: Could not cache {obj_name}: {e}")

    _state["cache_built"] = True
    print(f"  DeltaRender: + Cached {count}/{len(_state['static_names'])} static objects into RAM")


# ─────────────────────────────────────────
# EEVEE SETTINGS MANAGER
#
# Saves original EEVEE settings, applies
# DeltaRender optimizations before render,
# and restores everything after.
#
# Based on Blender 5.0 SceneEEVEE API:
# scene.eevee.use_shadow_jitter
# scene.eevee.shadow_pool_size
# ─────────────────────────────────────────

def apply_eevee_optimizations(scene):
    """
    Apply DeltaRender EEVEE optimizations before rendering.
    Saves original values so they can be restored after.
    """
    eevee = scene.eevee

    # Save originals
    try:
        _state["original_shadow_jitter"] = eevee.use_shadow_jitter
        _state["original_shadow_pool_size"] = eevee.shadow_pool_size
    except Exception as e:
        print(f"  DeltaRender: Could not read EEVEE settings: {e}")
        return

    # Disable shadow jitter — biggest performance win
    # Per Blender 5.0 API: use_shadow_jitter has high performance
    # impact and cannot be cached — rebuilds every render sample
    try:
        eevee.use_shadow_jitter = False
        print(f"  DeltaRender: + Shadow jitter disabled (was: {_state['original_shadow_jitter']})")
    except Exception as e:
        print(f"  DeltaRender: Could not disable shadow jitter: {e}")

    # Increase shadow pool if it's at default (512)
    # Larger pool means fewer shadow map evictions per frame
    try:
        if eevee.shadow_pool_size == '512':
            eevee.shadow_pool_size = '1024'
            print(f"  DeltaRender: + Shadow pool increased 512 → 1024")
    except Exception as e:
        print(f"  DeltaRender: Could not increase shadow pool: {e}")


def restore_eevee_settings(scene):
    """
    Restore original EEVEE settings after render completes.
    DeltaRender never permanently changes user settings.
    """
    eevee = scene.eevee

    try:
        if _state["original_shadow_jitter"] is not None:
            eevee.use_shadow_jitter = _state["original_shadow_jitter"]

        if _state["original_shadow_pool_size"] is not None:
            eevee.shadow_pool_size = _state["original_shadow_pool_size"]

        print(f"  DeltaRender: + EEVEE settings restored")
    except Exception as e:
        print(f"  DeltaRender: Could not restore EEVEE settings: {e}")

    _state["original_shadow_jitter"] = None
    _state["original_shadow_pool_size"] = None




def get_output_path(scene, frame_number):
    """
    Get the exact resolved output file path for a given frame.
    Uses os.path.normpath + bpy.path.abspath for correct
    cross-platform path resolution including Windows drive letters.
    """
    import re

    raw_path = scene.render.filepath
    ext = scene.render.file_extension

    # bpy.path.abspath resolves // relative to blend file location
    # os.path.normpath then normalizes slashes for the current OS
    base = os.path.normpath(bpy.path.abspath(raw_path))

    # If filepath contains # characters, replace with padded frame number
    if '#' in base:
        def replace_hashes(m):
            pad = len(m.group(0))
            return str(frame_number).zfill(pad)
        return re.sub(r'#+', replace_hashes, base) + ext

    # If base is an existing directory or has no extension, treat as folder
    if os.path.isdir(base) or not os.path.splitext(base)[1]:
        return os.path.join(base, f"{str(frame_number).zfill(4)}{ext}")

    # Base already contains a filename prefix — append frame number + ext
    return f"{base}{str(frame_number).zfill(4)}{ext}"


# ─────────────────────────────────────────
# RENDER HANDLERS
# Only used to capture timing and build
# cache — the actual frame loop is driven
# by our custom operator below
# ─────────────────────────────────────────

@persistent
def on_render_post(scene, depsgraph):
    """After each frame renders — build cache and snapshot pose."""
    if not _state["is_rendering"]:
        return

    frame_time = time.time() - _state["frame_start_time"]
    current_frame = scene.frame_current

    # Build static cache after first frame
    if not _state["cache_built"]:
        build_static_cache(scene)

    # Snapshot pose for next frame delta comparison
    _state["last_scene_snapshot"] = snapshot_scene(scene)
    _state["last_rendered_frame"] = current_frame

    # Use render.frame_path() — Blender's own internal method
    # for resolving the exact saved file path for a given frame.
    # This is the most reliable approach and avoids all path issues.
    try:
        actual_path = scene.render.frame_path(frame=current_frame)
        _state["last_output_path"] = actual_path
    except Exception:
        _state["last_output_path"] = get_output_path(scene, current_frame)

    _state["frame_log"].append({
        "frame": current_frame,
        "time": frame_time,
        "tier": "FULL"
    })

    print(f"  DeltaRender: Frame {current_frame} rendered in {frame_time:.2f}s [FULL]")



@persistent
def on_render_cancel(scene):
    _state["is_rendering"] = False
    try:
        scene.render.use_persistent_data = False
    except Exception:
        pass
    print("  DeltaRender: Render cancelled")


# ─────────────────────────────────────────
# CUSTOM RENDER LOOP OPERATOR
#
# This is the core of v0.5.
# Instead of using Blender's animation render,
# we drive our own frame-by-frame loop using
# bpy.ops.render.render(frame_start, frame_end)
# per the Blender 5.0 API.
#
# For SKIP frames: we never call render at all.
# We just copy the previous file. True zero cost.
# For FULL frames: we call render normally.
# ─────────────────────────────────────────

class DELTARENDER_OT_render_animation(bpy.types.Operator):
    bl_idname = "deltarender.render_animation"
    bl_label = "DeltaRender Animation"
    bl_description = "Render animation with intelligent frame skipping"

    _timer = None
    _frame_iterator = None
    _current_frame = 0
    _frames_to_render = []
    _scene = None

    def invoke(self, context, event):
        scene = context.scene

        if not scene.dr_enabled:
            # Fall back to normal render if DeltaRender is disabled
            bpy.ops.render.render("INVOKE_DEFAULT", animation=True)
            return {"FINISHED"}

        # Initialize state
        _state["is_rendering"] = True
        _state["cache_built"] = False
        _state["last_scene_snapshot"] = {}
        _state["last_rendered_frame"] = -1
        _state["last_output_path"] = ""
        _state["frame_log"] = []
        _state["render_start"] = time.time()
        _state["keyframed_bones"] = {}

        # Scan scene
        static_objects, dynamic_objects = scan_scene()
        _state["static_names"] = static_objects
        _state["dynamic_names"] = dynamic_objects
        build_keyframed_bones_cache(scene)

        # Enable persistent data as foundation
        scene.render.use_persistent_data = True

        # Apply EEVEE optimizations — disable shadow jitter, increase pool
        apply_eevee_optimizations(scene)

        # Build frame list
        self._frames_to_render = list(range(
            scene.frame_start,
            scene.frame_end + 1,
            scene.frame_step
        ))
        self._scene = scene

        total = len(static_objects) + len(dynamic_objects)
        savings = round((len(static_objects) / total) * 100) if total > 0 else 0

        print(f"\n{'='*50}")
        print(f"  DeltaRender v0.7 — Custom Render Loop")
        print(f"  Frames to process: {len(self._frames_to_render)}")
        print(f"  Static cached:     {len(static_objects)} objects")
        print(f"  Dynamic delta:     {len(dynamic_objects)} objects")
        print(f"  Static savings:    ~{savings}%")
        print(f"  Skip threshold:    {scene.dr_skip_threshold}")
        print(f"{'='*50}\n")

        # Start modal loop
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.01, window=context.window)
        self._frame_index = 0
        self._waiting_for_render = False

        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        scene = self._scene

        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        # If we're waiting for a render to finish, check if it's done
        if self._waiting_for_render:
            if not bpy.app.is_job_running("RENDER"):
                self._waiting_for_render = False
                self._frame_index += 1
            return {"RUNNING_MODAL"}

        if self._frame_index >= len(self._frames_to_render):
            return self.finish(context)

        frame = self._frames_to_render[self._frame_index]
        scene.frame_set(frame)

        # ── DECISION LOGIC ──
        if self._frame_index == 0:
            # Always fully render first frame
            tier = "FULL"
        else:
            current_snapshot = snapshot_scene(scene)
            max_delta = compute_max_delta(
                _state["last_scene_snapshot"],
                current_snapshot
            )
            skip_threshold = scene.dr_skip_threshold

            if max_delta <= skip_threshold:
                tier = "SKIP"
            else:
                tier = "FULL"
                if max_delta == 999.0:
                    print(f"  DeltaRender: Frame {frame} — scene change detected [FULL]")

        if tier == "SKIP":
            # True skip — copy previous file, never touch GPU
            t0 = time.time()
            prev_path = _state["last_output_path"]
            curr_path = scene.render.frame_path(frame=frame)

            skipped = False
            if prev_path and os.path.exists(prev_path):
                try:
                    shutil.copy2(prev_path, curr_path)
                    skipped = True
                except Exception as e:
                    print(f"  DeltaRender: Copy failed for frame {frame}: {e}")

            elapsed = time.time() - t0
            _state["frame_log"].append({
                "frame": frame,
                "time": elapsed,
                "tier": "SKIP"
            })

            if skipped:
                print(f"  DeltaRender: Frame {frame} — SKIPPED ({elapsed*1000:.0f}ms file copy)")
            else:
                print(f"  DeltaRender: Frame {frame} — Skip failed, treating as FULL")
                tier = "FULL"  # fallback to full render

            if tier == "SKIP":
                # Update last output path to this frame
                # so the next skip copies from here
                _state["last_output_path"] = curr_path
                self._frame_index += 1
                return {"RUNNING_MODAL"}

        # FULL render — call Blender's render for this single frame
        _state["frame_start_time"] = time.time()
        print(f"  DeltaRender: Frame {frame} — Rendering [FULL]...")

        # Use Blender 5.0 frame_start/frame_end API to render exactly one frame
        bpy.ops.render.render(
            "INVOKE_DEFAULT",
            animation=True,
            frame_start=frame,
            frame_end=frame
        )

        self._waiting_for_render = True
        return {"RUNNING_MODAL"}

    def finish(self, context):
        wm = context.window_manager
        if self._timer:
            wm.event_timer_remove(self._timer)

        total_time = time.time() - _state["render_start"]
        log = _state["frame_log"]

        skipped = [f for f in log if f["tier"] == "SKIP"]
        full = [f for f in log if f["tier"] == "FULL"]
        all_times = [f["time"] for f in log]

        print(f"\n{'='*50}")
        print(f"  DeltaRender v0.7 COMPLETE")
        print(f"  Total frames:      {len(log)}")
        print(f"  Full renders:      {len(full)}")
        print(f"  Skipped frames:    {len(skipped)} ")
        if all_times:
            print(f"  Average time:      {sum(all_times)/len(all_times):.2f}s")
            print(f"  Fastest:           {min(all_times):.2f}s")
            print(f"  Slowest:           {max(all_times):.2f}s")
        print(f"  Total render time: {total_time:.1f}s")
        if skipped:
            skip_pct = round(len(skipped) / len(log) * 100)
            print(f"  Frames skipped:    {skip_pct}% of animation ")
        print(f"{'='*50}\n")

        _state["is_rendering"] = False
        try:
            self._scene.render.use_persistent_data = False
            restore_eevee_settings(self._scene)
        except Exception:
            pass

        return {"FINISHED"}

    def cancel(self, context):
        wm = context.window_manager
        if self._timer:
            wm.event_timer_remove(self._timer)
        _state["is_rendering"] = False
        print("  DeltaRender: Cancelled")
        return {"CANCELLED"}


# ─────────────────────────────────────────
# UI PANEL
# ─────────────────────────────────────────

class DELTARENDER_PT_main_panel(bpy.types.Panel):
    bl_label = "DeltaRender"
    bl_idname = "DELTARENDER_PT_main_panel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "render"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.label(text="Smarter Rendering for Blender", icon="LIGHT")
        layout.separator()

        row = layout.row()
        row.scale_y = 1.5
        row.prop(scene, "dr_enabled", text="Enable DeltaRender", toggle=True)
        layout.separator()

        layout.operator(
            "deltarender.scan_scene",
            text="Scan Scene",
            icon="VIEWZOOM"
        )
        layout.separator()

        # Render button — replaces standard animation render
        row = layout.row()
        row.scale_y = 2.0
        row.operator(
            "deltarender.render_animation",
            text="Render Animation",
            icon="RENDER_ANIMATION"
        )
        layout.separator()

        if scene.dr_static_count > 0 or scene.dr_dynamic_count > 0:
            box = layout.box()
            box.label(text="Last Scan:", icon="INFO")
            box.label(text=f"  Static (cached):  {scene.dr_static_count}")
            box.label(text=f"  Dynamic (delta):  {scene.dr_dynamic_count}")
            box.label(text=f"  Est. savings:     ~{scene.dr_savings}%")

        layout.separator()
        box = layout.box()
        box.label(text="Thresholds:", icon="DRIVER")
        box.prop(scene, "dr_skip_threshold", text="Skip threshold")

        layout.separator()
        layout.label(text="v0.7 — Full Scene Delta Detection", icon="MEMORY")


# ─────────────────────────────────────────
# OPERATORS
# ─────────────────────────────────────────

class DELTARENDER_OT_scan_scene(bpy.types.Operator):
    bl_idname = "deltarender.scan_scene"
    bl_label = "Scan Scene"
    bl_description = "Identify static vs dynamic objects"

    def execute(self, context):
        t = time.time()
        static_objects, dynamic_objects = scan_scene()
        elapsed = round(time.time() - t, 4)
        print_scan_results(static_objects, dynamic_objects, elapsed)

        scene = context.scene
        scene.dr_static_count = len(static_objects)
        scene.dr_dynamic_count = len(dynamic_objects)
        total = len(static_objects) + len(dynamic_objects)
        scene.dr_savings = round((len(static_objects) / total) * 100) if total > 0 else 0

        self.report({"INFO"},
            f"DeltaRender: {len(static_objects)} static, "
            f"{len(dynamic_objects)} dynamic — ~{scene.dr_savings}% savings."
        )
        return {"FINISHED"}


# ─────────────────────────────────────────
# REGISTRATION
# ─────────────────────────────────────────

classes = [
    DELTARENDER_PT_main_panel,
    DELTARENDER_OT_scan_scene,
    DELTARENDER_OT_render_animation,
]

_handlers_registered = False


def register_handlers():
    global _handlers_registered
    if _handlers_registered:
        return
    bpy.app.handlers.render_post.append(on_render_post)
    bpy.app.handlers.render_cancel.append(on_render_cancel)
    _handlers_registered = True


def unregister_handlers():
    global _handlers_registered
    for handler_list, fn in [
        (bpy.app.handlers.render_post, on_render_post),
        (bpy.app.handlers.render_cancel, on_render_cancel),
    ]:
        try:
            if fn in handler_list:
                handler_list.remove(fn)
        except Exception:
            pass
    _handlers_registered = False


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.dr_enabled = bpy.props.BoolProperty(
        name="Enable DeltaRender",
        description="Enable intelligent frame skipping",
        default=False
    )
    bpy.types.Scene.dr_static_count = bpy.props.IntProperty(default=0)
    bpy.types.Scene.dr_dynamic_count = bpy.props.IntProperty(default=0)
    bpy.types.Scene.dr_savings = bpy.props.IntProperty(default=0)
    bpy.types.Scene.dr_skip_threshold = bpy.props.FloatProperty(
        name="Skip Threshold",
        description="Max pose delta to skip frame entirely",
        default=0.0001,
        min=0.0,
        max=0.01,
        precision=6
    )

    register_handlers()
    print("DeltaRender v0.7 loaded — Full Scene Delta Detection Active")


def unregister():
    unregister_handlers()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    props = [
        "dr_enabled", "dr_static_count", "dr_dynamic_count",
        "dr_savings", "dr_skip_threshold"
    ]
    for prop in props:
        try:
            delattr(bpy.types.Scene, prop)
        except Exception:
            pass


if __name__ == "__main__":
    register()
