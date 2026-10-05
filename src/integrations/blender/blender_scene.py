"""
Runs INSIDE Blender (``blender --background --python blender_scene.py -- ...``).

Do not import this from regular Python: it needs Blender's bundled ``bpy``
module. BlenderRenderer (renderer.py) launches it as a subprocess.

Builds a real 3D scene for one shot from the renderer-neutral scene JSON that
SceneComposer writes next to each Godot .tscn, then renders a PNG sequence.

Arguments after the ``--`` separator:
    scene_json  out_dir  num_frames  fps  width  height  engine  samples

Scene JSON schema (version 1):
    {
      "version": 1,
      "shot_id": str,
      "camera_angle": str,        # "wide shot" | "medium shot" | "close-up" | "overhead" ...
      "lighting": str,            # free text, keyword-matched
      "modulate": [r, g, b, a],   # SceneComposer's lighting tint
      "characters": [{"name": str, "image": abs_path, "x": float, "scale": float}, ...]
    }
where ``x`` is the character's horizontal position normalised to [-1, 1]
across the frame. ``scale`` (SceneComposer's 2D framing factor) is carried for
other consumers but ignored here: camera distance handles framing in 3D.
"""

import json
import math
import os
import sys

import bpy
from mathutils import Vector

# camera_angle keyword -> (distance, height, pitch_degrees_down, fov_degrees)
_CAMERAS = {
    "wide": (15.0, 2.2, 6.0, 40.0),
    "medium": (9.0, 1.6, 4.0, 40.0),
    "close": (5.0, 1.5, 2.0, 40.0),
    "overhead": (13.0, 11.0, 58.0, 40.0),
}

# lighting keyword -> (sun_energy, fill_energy, background rgb)
_LIGHTING = {
    "night": (0.35, 1.2, (0.015, 0.02, 0.05)),
    "dark": (0.45, 1.4, (0.02, 0.02, 0.03)),
    "dim": (1.0, 2.0, (0.04, 0.04, 0.05)),
    "shadow": (1.1, 1.8, (0.05, 0.05, 0.06)),
    "harsh": (3.4, 0.8, (0.35, 0.33, 0.3)),
    "bright": (2.4, 1.5, (0.45, 0.5, 0.58)),
    "daylight": (2.0, 1.5, (0.40, 0.48, 0.60)),
}
_DEFAULT_LIGHTING = (1.9, 1.5, (0.30, 0.34, 0.40))

CHARACTER_HEIGHT = 3.0  # world units


def _pick(table, text, default_key=None, default=None):
    text = (text or "").lower()
    for key, value in table.items():
        if key in text:
            return value
    return table[default_key] if default_key else default


# ---------------------------------------------------------------------------
# Rigged characters ("character_style": "rigged")
# ---------------------------------------------------------------------------
#
# A procedural humanoid: an armature (hips, spine, chest, neck, head, jaw, two arms,
# two legs) with simple primitive meshes parented to its bones, coloured from the
# character's reference art. It is deliberately a stylised mannequin, not a sculpted
# model: what matters is that the same body, in the same colours, appears in every shot,
# that it really animates (idle / talk / walk) and that its jaw follows the dialogue.

SPREAD = 4.2  # world units from centre to the +/-1 position (matches the image planes)

# name: (head xyz, tail xyz, parent)   -- character faces -Y (towards the camera), 3 units tall
_BONES = {
    "root": ((0, 0, 0.0), (0, 0, 0.3), None),
    "hips": ((0, 0, 1.5), (0, 0, 1.75), "root"),
    "spine": ((0, 0, 1.75), (0, 0, 2.05), "hips"),
    "chest": ((0, 0, 2.05), (0, 0, 2.35), "spine"),
    "neck": ((0, 0, 2.35), (0, 0, 2.5), "chest"),
    "head": ((0, 0, 2.5), (0, 0, 3.0), "neck"),
    "jaw": ((0, -0.05, 2.62), (0, -0.3, 2.62), "head"),
    "upper_arm.L": ((0.42, 0, 2.25), (0.42, 0, 1.65), "chest"),
    "lower_arm.L": ((0.42, 0, 1.65), (0.42, 0, 1.1), "upper_arm.L"),
    "upper_arm.R": ((-0.42, 0, 2.25), (-0.42, 0, 1.65), "chest"),
    "lower_arm.R": ((-0.42, 0, 1.65), (-0.42, 0, 1.1), "upper_arm.R"),
    "upper_leg.L": ((0.2, 0, 1.5), (0.2, 0, 0.75), "hips"),
    "lower_leg.L": ((0.2, 0, 0.75), (0.2, 0, 0.1), "upper_leg.L"),
    "upper_leg.R": ((-0.2, 0, 1.5), (-0.2, 0, 0.75), "hips"),
    "lower_leg.R": ((-0.2, 0, 0.75), (-0.2, 0, 0.1), "upper_leg.R"),
}


def _material(name, rgb, rough=0.85):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    color = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Roughness"].default_value = rough
    mat.diffuse_color = color  # what the Workbench engine shows
    return mat


def _build_armature(name, x_world):
    bpy.ops.object.armature_add(enter_editmode=True, location=(x_world, 0, 0))
    arm_obj = bpy.context.active_object
    arm_obj.name = f"rig_{name}"
    arm = arm_obj.data
    arm.name = f"rig_{name}_data"
    for b in list(arm.edit_bones):
        arm.edit_bones.remove(b)
    for bone_name, (head, tail, parent) in _BONES.items():
        eb = arm.edit_bones.new(bone_name)
        eb.head, eb.tail = head, tail
        if parent:
            eb.parent = arm.edit_bones[parent]
            eb.use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm_obj


def _attach(obj, arm_obj, bone_name, material):
    """Parent ``obj`` to a bone so it follows that bone's pose, without moving it at rest."""
    from mathutils import Matrix

    bone = arm_obj.data.bones[bone_name]
    obj.parent = arm_obj
    obj.parent_type = "BONE"
    obj.parent_bone = bone_name
    # Bone parenting measures from the bone's tail; cancel that out for the rest pose.
    obj.matrix_parent_inverse = (arm_obj.matrix_world @ bone.matrix_local
                                 @ Matrix.Translation((0, bone.length, 0))).inverted()
    obj.data.materials.append(material)


def _prim(kind, name, location, scale, rotation=(0, 0, 0)):
    if kind == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=1.0, location=location)
    elif kind == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(vertices=20, radius=1.0, depth=1.0, location=location)
    else:
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = scale
    obj.rotation_euler = rotation
    bpy.ops.object.shade_smooth()
    return obj


def build_procedural_character(name, x_world, palette):
    """Returns a dict describing the rig: {"armature", "bones", "meshes", "eyes"}."""
    arm_obj = _build_armature(name, x_world)
    mats = {k: _material(f"{name}_{k}", palette[k]) for k in ("skin", "hair", "shirt", "trousers")}
    dark = _material(f"{name}_dark", (0.03, 0.03, 0.04))
    mouth_in = _material(f"{name}_mouth", (0.25, 0.02, 0.03))
    shoes = _material(f"{name}_shoes", (0.06, 0.05, 0.05))
    X = x_world
    meshes = []

    def add(kind, mname, loc, scale, bone, mat, rot=(0, 0, 0)):
        o = _prim(kind, f"{name}_{mname}", (X + loc[0], loc[1], loc[2]), scale, rot)
        _attach(o, arm_obj, bone, mat)
        meshes.append(o.name)
        return o

    # torso & pelvis
    add("cube", "pelvis", (0, 0, 1.62), (0.62, 0.34, 0.28), "hips", mats["trousers"])
    add("cube", "torso", (0, 0, 2.02), (0.8, 0.4, 0.62), "chest", mats["shirt"])
    add("cylinder", "neck", (0, 0, 2.44), (0.11, 0.11, 0.2), "neck", mats["skin"])
    # head, hair, face
    add("sphere", "head", (0, 0, 2.75), (0.3, 0.3, 0.3), "head", mats["skin"])
    add("sphere", "hair", (0, 0.03, 2.87), (0.32, 0.32, 0.26), "head", mats["hair"])
    for sx in (-1, 1):
        add("sphere", f"eye{'L' if sx > 0 else 'R'}", (0.1 * sx, -0.26, 2.8), (0.04, 0.03, 0.05), "head", dark)
    add("sphere", "mouth_inside", (0, -0.235, 2.585), (0.1, 0.05, 0.03), "head", mouth_in)
    # lower lip/chin rides on the jaw bone: rotating the jaw uncovers the mouth
    add("sphere", "chin", (0, -0.2, 2.56), (0.16, 0.12, 0.09), "jaw", mats["skin"])
    # arms
    for side, sx in (("L", 1), ("R", -1)):
        add("cylinder", f"upper_arm_{side}", (0.42 * sx, 0, 1.95), (0.1, 0.1, 0.6), f"upper_arm.{side}", mats["shirt"])
        add("cylinder", f"lower_arm_{side}", (0.42 * sx, 0, 1.37), (0.085, 0.085, 0.55), f"lower_arm.{side}", mats["skin"])
        add("sphere", f"hand_{side}", (0.42 * sx, 0, 1.08), (0.09, 0.08, 0.1), f"lower_arm.{side}", mats["skin"])
    # legs
    for side, sx in (("L", 1), ("R", -1)):
        add("cylinder", f"upper_leg_{side}", (0.2 * sx, 0, 1.12), (0.15, 0.15, 0.75), f"upper_leg.{side}", mats["trousers"])
        add("cylinder", f"lower_leg_{side}", (0.2 * sx, 0, 0.42), (0.12, 0.12, 0.65), f"lower_leg.{side}", mats["trousers"])
        add("cube", f"shoe_{side}", (0.2 * sx, -0.06, 0.06), (0.2, 0.36, 0.12), f"lower_leg.{side}", shoes)
    return {"armature": arm_obj, "bones": list(_BONES), "meshes": meshes, "name": name}


def _face_direction(name):
    """Which way the figure is looking in world space: (x, y) from the head centre to its eyes."""
    head, eye = bpy.data.objects[f"{name}_head"], bpy.data.objects[f"{name}_eyeL"]
    d = eye.matrix_world.translation - head.matrix_world.translation
    n = math.hypot(d.x, d.y) or 1.0
    return [round(d.x / n, 3), round(d.y / n, 3)]


def _smooth(values, window):
    out = []
    for i in range(len(values)):
        lo, hi = max(0, i - window), min(len(values), i + window + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def animate_character(rig, motion, mouth, frames, fps, x_world, walk_to_world):
    """Keyframe the pose bones for ``frames`` frames. Returns what was keyed, for the report."""
    arm_obj = rig["armature"]
    pb = arm_obj.pose.bones
    for b in pb:
        b.rotation_mode = "XYZ"
    mouth = list(mouth or [])
    mouth += [0.0] * (frames - len(mouth))
    talk = _smooth(mouth, max(2, fps // 6))   # slow envelope of "is speaking": drives gestures
    jaw_values, root_x = [], []
    stride = 1.7  # steps per second while walking
    direction = 0.0
    if motion == "walk" and walk_to_world is not None and abs(walk_to_world - x_world) > 1e-3:
        direction = 1.0 if walk_to_world > x_world else -1.0

    def key(bone, frame, x=0.0, yaw=0.0, roll=0.0):
        # Every bone lies along the vertical (or the face direction), so in bone space
        # local X = pitch (nod / swing), local Y = twist about the bone = turning left/right,
        # local Z = rolling sideways (arms away from the body).
        b = pb[bone]
        b.rotation_euler = (x, yaw, roll)
        b.keyframe_insert("rotation_euler", frame=frame)

    for f in range(1, frames + 1):
        t = (f - 1) / float(fps)
        frac = (f - 1) / float(max(1, frames - 1))
        breathe = math.sin(2 * math.pi * 0.28 * t)
        sway = math.sin(2 * math.pi * 0.17 * t)
        g = talk[f - 1]
        phase = 2 * math.pi * stride * t
        walking = direction != 0.0

        # root: travel + facing
        root = pb["root"]
        root.rotation_mode = "XYZ"
        x_off = (walk_to_world - x_world) * frac if walking else 0.0
        root.location = (x_off, 0, 0)
        root.rotation_euler = (0, (direction * math.pi / 2) if walking else 0.05 * sway, 0)  # +90deg turns the face (-Y) towards +X
        root.keyframe_insert("location", frame=f)
        root.keyframe_insert("rotation_euler", frame=f)
        root_x.append(round(x_world + x_off, 4))

        bob = abs(math.sin(phase)) * 0.06 if walking else breathe * 0.012
        pb["hips"].location = (0, 0, bob)
        pb["hips"].keyframe_insert("location", frame=f)
        key("hips", f, yaw=(0.08 * math.sin(phase) if walking else 0.0))
        key("spine", f, x=0.02 * breathe + 0.03 * g)
        key("chest", f, x=0.03 * breathe, yaw=0.05 * g * math.sin(2 * math.pi * 0.9 * t))
        key("neck", f, x=0.04 * g)
        # head: idle drift, nods while speaking, small look-around
        key("head", f, x=-0.05 * g * (0.5 + 0.5 * math.sin(2 * math.pi * 1.7 * t)) + 0.01 * breathe,
            yaw=0.12 * sway + 0.06 * g * math.sin(2 * math.pi * 0.6 * t))

        jaw = max(0.0, min(1.0, mouth[f - 1])) * 0.55
        key("jaw", f, x=-jaw)
        jaw_values.append(round(jaw, 4))

        swing = 0.7 * math.sin(phase) if walking else 0.0
        gesture_r = 0.9 * g * (0.55 + 0.45 * math.sin(2 * math.pi * 0.8 * t))
        key("upper_arm.L", f, x=-swing, roll=-0.08 + 0.02 * breathe)
        key("upper_arm.R", f, x=swing - gesture_r, roll=0.08 - 0.02 * breathe)
        key("lower_arm.L", f, x=-0.15 - (0.3 * max(0.0, -swing)))
        key("lower_arm.R", f, x=-0.15 - 0.9 * g - (0.3 * max(0.0, swing)))
        key("upper_leg.L", f, x=swing * 0.85)
        key("upper_leg.R", f, x=-swing * 0.85)
        key("lower_leg.L", f, x=max(0.0, -swing) * 0.9)
        key("lower_leg.R", f, x=max(0.0, swing) * 0.9)
    # Measure the posed result (not just what was keyed): where the head ends up in world space.
    scene = bpy.context.scene
    heights, facing = [], []
    for f in sorted({1, max(1, frames // 2), frames}):
        scene.frame_set(f)
        bpy.context.view_layer.update()
        heights.append(round((arm_obj.matrix_world @ arm_obj.pose.bones["head"].tail).z, 3))
        facing.append(_face_direction(rig["name"]))
    scene.frame_set(1)
    return {"jaw": jaw_values, "root_x": root_x, "head_height": heights, "facing": facing}


# Shape-key names that mean "mouth open" in glTF/VRM/ARKit-style character models.
_MOUTH_KEYS = ("jawopen", "mouthopen", "mouth_open", "viseme_aa", "vrc.v_aa", "aa", "a", "mouthopen_1", "jaw_open")
_ACTION_WORDS = {"walk": ("walk", "run"), "talk": ("talk", "speak", "gesture"), "idle": ("idle", "stand", "breath")}


def build_file_character(name, path, x_world):
    """Import a user's .glb/.gltf character, scaled to the standard height with feet on the floor."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    if not new:
        raise SystemExit(f"Model for {name!r} contained no objects: {path}")
    # The glTF importer adds helper meshes (bone display shapes, e.g. an "Icosphere") that
    # are not part of the character: keep them out of the measurements and out of the render.
    helpers = {pb.custom_shape for o in new if o.type == "ARMATURE" for pb in o.pose.bones if pb.custom_shape}
    for h in helpers:
        h.hide_render = True
        h.hide_viewport = True
    new = [o for o in new if o not in helpers]
    meshes = [o for o in new if o.type == "MESH"]
    if not meshes:
        raise SystemExit(f"Model for {name!r} has no meshes: {path}")

    bpy.ops.object.empty_add(type="PLAIN_AXES", location=(0, 0, 0))
    holder = bpy.context.active_object
    holder.name = f"model_{name}"
    for o in new:
        if o.parent is None:
            o.parent = holder
    bpy.context.view_layer.update()

    corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    lo_z, hi_z = min(c.z for c in corners), max(c.z for c in corners)
    lo_x, hi_x = min(c.x for c in corners), max(c.x for c in corners)
    height = max(1e-4, hi_z - lo_z)
    k = CHARACTER_HEIGHT / height
    holder.scale = (k, k, k)
    holder.location = (x_world - k * (lo_x + hi_x) / 2.0, 0.0, -k * lo_z)
    bpy.context.view_layer.update()

    mouth_keys = []
    for o in meshes:
        keys = o.data.shape_keys.key_blocks if o.data.shape_keys else []
        for kb in keys:
            if kb.name.lower() in _MOUTH_KEYS:
                mouth_keys.append(kb)
    armatures = [o for o in new if o.type == "ARMATURE"]
    return {"holder": holder, "meshes": [o.name for o in meshes], "mouth_keys": mouth_keys,
            "armatures": armatures, "base_x": holder.location.x, "scale": k}


def animate_file_character(rig, motion, mouth, frames, fps, x_world, walk_to_world):
    """Drive a loaded model: mouth shape keys from the speech curve, its own animation clips, travel."""
    mouth = list(mouth or [])
    mouth += [0.0] * (frames - len(mouth))
    keyed = 0
    for kb in rig["mouth_keys"]:
        for f in range(1, frames + 1):
            kb.value = max(0.0, min(1.0, mouth[f - 1]))
            kb.keyframe_insert("value", frame=f)
            keyed += 1
        kb.value = 0.0

    used_action = None
    for arm in rig["armatures"]:
        actions = [a for a in bpy.data.actions]
        pick = None
        for word in _ACTION_WORDS.get(motion, ()) + _ACTION_WORDS["idle"]:
            pick = next((a for a in actions if word in a.name.lower()), None)
            if pick:
                break
        pick = pick or (actions[0] if actions else None)
        if pick is not None:
            arm.animation_data_create()
            arm.animation_data.action = pick
            for fc in pick.fcurves:
                if not any(m.type == "CYCLES" for m in fc.modifiers):
                    fc.modifiers.new("CYCLES")
            used_action = pick.name

    holder = rig["holder"]
    root_x = []
    walking = motion == "walk" and walk_to_world is not None and abs(walk_to_world - x_world) > 1e-3
    direction = (1.0 if walk_to_world > x_world else -1.0) if walking else 0.0
    for f in range(1, frames + 1):
        frac = (f - 1) / float(max(1, frames - 1))
        dx = (walk_to_world - x_world) * frac if walking else 0.0
        holder.location.x = rig["base_x"] + dx
        holder.rotation_euler = (0, 0, (direction * math.pi / 2) if walking else 0.0)  # +90deg turns the face (-Y) towards +X
        holder.keyframe_insert("location", index=0, frame=f)
        holder.keyframe_insert("rotation_euler", index=2, frame=f)
        root_x.append(round(holder.location.x, 4))
    scene = bpy.context.scene
    facing = []
    for f in sorted({1, max(1, frames // 2), frames}):
        scene.frame_set(f)
        bpy.context.view_layer.update()
        fwd = holder.matrix_world.to_3x3() @ Vector((0.0, -1.0, 0.0))
        n = math.hypot(fwd.x, fwd.y) or 1.0
        facing.append([round(fwd.x / n, 3), round(fwd.y / n, 3)])
    scene.frame_set(1)
    return {"jaw": [round(max(0.0, min(1.0, v)), 4) for v in mouth[:frames]], "root_x": root_x, "facing": facing,
            "mouth_shape_keys": [kb.name for kb in rig["mouth_keys"]], "keyed_mouth_values": keyed,
            "action": used_action}


def _clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _image_plane(name, image_path, x_world, scale):
    img = bpy.data.images.load(image_path)
    w, h = img.size
    aspect = (w / h) if h else 1.0
    height = CHARACTER_HEIGHT * scale
    width = height * aspect

    bpy.ops.mesh.primitive_plane_add(size=1.0)
    plane = bpy.context.active_object
    plane.name = f"char_{name}"
    plane.scale = (width, height, 1.0)
    plane.rotation_euler = (math.radians(90), 0.0, 0.0)
    plane.location = (x_world, 0.0, height / 2.0)

    mat = bpy.data.materials.new(f"mat_{name}")
    mat.use_nodes = True
    # Alpha cut-out for both the surface and its shadow (EEVEE legacy in 4.0;
    # harmless no-op attributes are guarded for other Blender versions).
    if hasattr(mat, "blend_method"):
        mat.blend_method = "HASHED"
    if hasattr(mat, "shadow_method"):
        mat.shadow_method = "HASHED"
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Linear"
    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
    bsdf.inputs["Roughness"].default_value = 0.9
    plane.data.materials.append(mat)
    return plane


def _backdrop(bg_rgb):
    """A floor and a back wall so the shot has a sense of place, not a void."""
    bpy.ops.mesh.primitive_plane_add(size=80.0, location=(0, 0, 0))
    floor = bpy.context.active_object
    floor.name = "floor"
    bpy.ops.mesh.primitive_plane_add(size=80.0, location=(0, 12, 0))
    wall = bpy.context.active_object
    wall.name = "backwall"
    wall.rotation_euler = (math.radians(90), 0, 0)
    wall.location = (0, 12, 20)
    for obj, scale in ((floor, 0.55), (wall, 0.8)):
        mat = bpy.data.materials.new(f"mat_{obj.name}")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = (
            bg_rgb[0] * scale + 0.1, bg_rgb[1] * scale + 0.1, bg_rgb[2] * scale + 0.1, 1.0,
        )
        bsdf.inputs["Roughness"].default_value = 1.0
        obj.data.materials.append(mat)


def _lights(sun_energy, fill_energy, tint):
    bpy.ops.object.light_add(type="SUN", location=(4, -6, 10))
    sun = bpy.context.active_object
    sun.data.energy = sun_energy
    sun.data.color = tint
    sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(25))

    bpy.ops.object.light_add(type="AREA", location=(-5, -7, 5))
    fill = bpy.context.active_object
    fill.data.energy = fill_energy * 400.0
    fill.data.size = 6.0
    fill.data.color = tint
    fill.rotation_euler = (math.radians(70), 0, math.radians(-30))


def _camera(angle_text, num_frames, fov_scale=1.0, rigged=False):
    dist, height, pitch, fov = _pick(_CAMERAS, angle_text, default_key="medium")
    bpy.ops.object.camera_add()
    cam = bpy.context.active_object
    cam.data.angle = math.radians(fov)
    cam.data.clip_end = 500.0
    bpy.context.scene.camera = cam

    target = Vector((0.0, 0.0, 1.4))
    if rigged and "close" in (angle_text or "").lower():
        height, target = 2.5, Vector((0.0, 0.0, 2.45))  # frame the head and shoulders, not the waist

    def place(d):
        if pitch > 30:  # overhead: look down from above/behind
            cam.location = (0.0, -d * 0.35, height * (d / dist) + 1.0)
        else:
            cam.location = (0.0, -d, height)
        direction = target - cam.location
        cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    # Slow push-in over the shot (~6%) so the result reads as a camera move
    # rather than a still image repeated for N frames.
    last = max(1, num_frames)
    place(dist)
    cam.keyframe_insert("location", frame=1)
    cam.keyframe_insert("rotation_euler", frame=1)
    place(dist * 0.94)
    cam.keyframe_insert("location", frame=last)
    cam.keyframe_insert("rotation_euler", frame=last)
    return cam


def main():
    argv = sys.argv
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    if len(args) < 8:
        raise SystemExit("usage: blender_scene.py -- scene_json out_dir frames fps width height engine samples")
    scene_json, out_dir, frames, fps, width, height, engine, samples = args[:8]
    frames, fps, width, height, samples = int(frames), int(fps), int(width), int(height), int(samples)

    with open(scene_json, "r", encoding="utf-8") as f:
        spec = json.load(f)
    if spec.get("version") != 1:
        raise SystemExit(f"Unsupported scene JSON version: {spec.get('version')!r}")

    _clear_scene()
    scene = bpy.context.scene

    sun_e, fill_e, bg = _pick(_LIGHTING, spec.get("lighting"), default=_DEFAULT_LIGHTING)
    mod = spec.get("modulate") or [1, 1, 1, 1]
    tint = (min(1.0, mod[0]), min(1.0, mod[1]), min(1.0, mod[2]))

    world = bpy.data.worlds.new("world")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (*bg, 1.0)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0
    scene.world = world

    _backdrop(bg)
    _lights(sun_e, fill_e, tint)

    chars = spec.get("characters", [])
    spread = SPREAD
    rigged = spec.get("character_style") == "rigged"
    report = {"character_style": "rigged" if rigged else "planes", "frames": frames, "fps": fps, "characters": {}}
    mouth_curves = spec.get("mouth") or {}
    for c in chars:
        x_world = float(c["x"]) * spread
        if not rigged:
            # SceneComposer's "scale" is a 2D-sprite framing factor; in 3D the
            # camera distance does that job, so every character is full-size here.
            _image_plane(c["name"], c["image"], x_world, 1.0)
            continue
        model = c.get("model") or {"type": "procedural"}
        walk_to = float(c["walk_to"]) * spread if c.get("walk_to") is not None else None
        motion = c.get("motion", "idle")
        if model.get("type") == "file":
            rig = build_file_character(c["name"], model["path"], x_world)
            keyed = animate_file_character(rig, motion, mouth_curves.get(c["name"]), frames, fps, x_world, walk_to)
            info = {"model": "file", "path": model["path"], "meshes": rig["meshes"], "scale": round(rig["scale"], 4)}
        else:
            rig = build_procedural_character(c["name"], x_world, c["palette"])
            keyed = animate_character(rig, motion, mouth_curves.get(c["name"]), frames, fps, x_world, walk_to)
            info = {"model": "procedural", "bones": rig["bones"], "meshes": rig["meshes"]}
        report["characters"][c["name"]] = {"motion": motion, **info, **keyed}

    _camera(spec.get("camera_angle", "medium shot"), frames, rigged=rigged)

    scene.frame_start = 1
    scene.frame_end = frames
    scene.render.fps = fps
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    # Blender 4.x defaults to the AgX view transform, which flattens contrast
    # and washes out flat-coloured cut-outs; "Standard" keeps colours faithful
    # to the source art (important for character-consistency validation).
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = out_dir.rstrip("/") + "/frame"

    if engine.upper() == "CYCLES":
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = False
    elif engine.upper() == "EEVEE":
        scene.render.engine = "BLENDER_EEVEE"
        scene.eevee.taa_render_samples = samples
        scene.eevee.use_soft_shadows = True
    else:
        scene.render.engine = "BLENDER_WORKBENCH"

    if rigged:
        with open(os.path.join(out_dir, "rig_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f)
    bpy.ops.render.render(animation=True)
    print(f"BLAZENG_RENDER_OK frames={frames}")


main()
