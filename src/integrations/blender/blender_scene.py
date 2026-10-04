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


def _camera(angle_text, num_frames, fov_scale=1.0):
    dist, height, pitch, fov = _pick(_CAMERAS, angle_text, default_key="medium")
    bpy.ops.object.camera_add()
    cam = bpy.context.active_object
    cam.data.angle = math.radians(fov)
    cam.data.clip_end = 500.0
    bpy.context.scene.camera = cam

    target = Vector((0.0, 0.0, 1.4))

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
    spread = 4.2  # world units from centre to the +/-1 position
    for c in chars:
        # SceneComposer's "scale" is a 2D-sprite framing factor; in 3D the
        # camera distance does that job, so every character is full-size here.
        _image_plane(c["name"], c["image"], float(c["x"]) * spread, 1.0)

    _camera(spec.get("camera_angle", "medium shot"), frames)

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

    bpy.ops.render.render(animation=True)
    print(f"BLAZENG_RENDER_OK frames={frames}")


main()
