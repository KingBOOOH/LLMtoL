import bpy
import json
import math
import os

WORK_DIR = os.environ.get("LEGO_WORK_DIR", os.getcwd())

# =========================
# 1. LOAD JSON
# =========================

PATH = os.path.join(WORK_DIR, "model.json")

if not os.path.exists(PATH):
    raise Exception(f"❌ model.json introuvable : {PATH}")

with open(PATH, "r") as f:
    try:
        data = json.load(f)
    except Exception as e:
        raise Exception(f"❌ JSON invalide: {e}")

# =========================
# 2. VALIDATION
# =========================

def validate_scene(data):
    if not isinstance(data, dict) or "bricks" not in data:
        raise Exception("❌ JSON must have a 'bricks' key")

    bricks = data["bricks"]
    if not isinstance(bricks, list) or len(bricks) == 0:
        raise Exception("❌ 'bricks' must be a non-empty list")

    allowed_types = ["cube"]

    for i, obj in enumerate(bricks):
        if "type" not in obj:
            raise Exception(f"❌ Missing 'type' at brick {i}")
        if "position" not in obj:
            raise Exception(f"❌ Missing 'position' at brick {i}")
        if obj["type"] not in allowed_types:
            raise Exception(f"❌ Invalid type '{obj['type']}' at brick {i}")

        pos = obj["position"]
        if (
            not isinstance(pos, list)
            or len(pos) != 3
            or not all(isinstance(v, (int, float)) for v in pos)
        ):
            raise Exception(f"❌ Invalid position at brick {i}")

    return True


validate_scene(data)
print("\n✅ JSON validé")

# =========================
# 3. CLEAN SCENE
# =========================

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# =========================
# 4. COLOR MAP
# =========================

COLOR_MAP = {
    "red":    (1.0, 0.0, 0.0, 1.0),
    "green":  (0.0, 0.8, 0.0, 1.0),
    "blue":   (0.2, 0.4, 1.0, 1.0),
    "yellow": (1.0, 1.0, 0.0, 1.0),
    "orange": (1.0, 0.5, 0.0, 1.0),
    "white":  (1.0, 1.0, 1.0, 1.0),
    "black":  (0.05, 0.05, 0.05, 1.0),
    "gray":   (0.5, 0.5, 0.5, 1.0),
    "grey":   (0.5, 0.5, 0.5, 1.0),
    "purple": (0.5, 0.0, 0.5, 1.0),
    "cyan":   (0.0, 0.8, 0.8, 1.0),
    "brown":  (0.4, 0.2, 0.05, 1.0),
    "pink":   (1.0, 0.4, 0.7, 1.0),
}
DEFAULT_COLOR = (0.8, 0.8, 0.8, 1.0)

# =========================
# 5. MATERIAL SYSTEM
# =========================

def apply_material(obj, rgba, name):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = rgba
    obj.data.materials.append(mat)

# =========================
# 6. OBJECT CREATION
# =========================

def create_object(obj_type, position, size, color_name):
    x, y, z = position
    w, h, d = size

    rgba = COLOR_MAP.get(color_name.lower())
    if rgba is None:
        print(f"⚠️ Couleur inconnue '{color_name}' — utilisation de la couleur par défaut (gris)")
        rgba = DEFAULT_COLOR

    if obj_type == "cube":
        bpy.ops.mesh.primitive_cube_add(size=1, location=(x, y, z))
        obj = bpy.context.object
        obj.scale = (w, h, d)
        apply_material(obj, rgba, f"Mat_{color_name}")

# =========================
# 7. EXECUTION
# =========================

for obj in data["bricks"]:
    create_object(obj["type"], obj["position"], obj["size"], obj["color"])

# =========================
# 8. CAMERA SETUP (ORTHO)
# =========================

cam_data = bpy.data.cameras.new("Camera")
cam_obj = bpy.data.objects.new("Camera", cam_data)
bpy.context.collection.objects.link(cam_obj)

cam_data.type = 'ORTHO'
cam_data.ortho_scale = 20

bpy.context.scene.camera = cam_obj

def set_camera_view(cam_obj, view):
    if view == "top":
        cam_obj.location = (0, 0, 10)
        cam_obj.rotation_euler = (0, 0, 0)

    elif view == "bottom":
        cam_obj.location = (0, 0, -10)
        cam_obj.rotation_euler = (math.radians(180), 0, 0)

    elif view == "left":
        cam_obj.location = (-10, 0, 0)
        cam_obj.rotation_euler = (math.radians(90), 0, math.radians(-90))

    elif view == "right":
        cam_obj.location = (10, 0, 0)
        cam_obj.rotation_euler = (math.radians(90), 0, math.radians(90))

# =========================
# 9. LIGHT
# =========================

light_data = bpy.data.lights.new(name="Light", type='AREA')
light_obj = bpy.data.objects.new(name="Light", object_data=light_data)
bpy.context.collection.objects.link(light_obj)

light_obj.location = (5, -5, 10)
light_data.energy = 800

# =========================
# 10. MULTI RENDER
# =========================

views = ["top", "bottom", "left", "right"]

for v in views:
    set_camera_view(cam_obj, v)
    path = os.path.join(WORK_DIR, f"render_{v}.png")
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)

print("✅ Rendus multiples OK")
