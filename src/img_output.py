"""Assemble les quatre rendus en une planche unique (chaine v0).

Lit render_{top,bottom,left,right}.png dans LEGO_WORK_DIR et ecrit final.png : grille
2x2, separateurs blancs, nom de la vue en surimpression. Echoue bruyamment si un rendu
manque — mieux vaut une erreur nette qu'une planche silencieusement incomplete.
"""

import os
from PIL import Image, ImageDraw, ImageFont

WORK_DIR = os.environ.get("LEGO_WORK_DIR", os.getcwd())

# =========================
# INPUT IMAGES
# =========================

images_path = {
    "top":    os.path.join(WORK_DIR, "render_top.png"),
    "bottom": os.path.join(WORK_DIR, "render_bottom.png"),
    "left":   os.path.join(WORK_DIR, "render_left.png"),
    "right":  os.path.join(WORK_DIR, "render_right.png"),
}

images = {k: Image.open(v) for k, v in images_path.items()}

w, h = images["top"].size

final_img = Image.new("RGB", (w * 2, h * 2), (20, 20, 20))
draw = ImageDraw.Draw(final_img)

positions = {
    "top":    (0, 0),
    "right":  (w, 0),
    "left":   (0, h),
    "bottom": (w, h),
}

for key, pos in positions.items():
    final_img.paste(images[key], pos)

# =========================
# LIGNES CENTRALES
# =========================

draw.line([(w, 0), (w, h * 2)], fill=(255, 255, 255), width=3)
draw.line([(0, h), (w * 2, h)], fill=(255, 255, 255), width=3)

# =========================
# LABELS
# =========================

try:
    font = ImageFont.truetype("arial.ttf", 24)
except:
    font = ImageFont.load_default()

labels = {
    "top":    "TOP VIEW",
    "right":  "RIGHT VIEW",
    "left":   "LEFT VIEW",
    "bottom": "BOTTOM VIEW",
}

offset = 10

for key, pos in positions.items():
    x, y = pos
    draw.text(
        (x + offset, y + h - 30),
        labels[key],
        fill=(255, 255, 255),
        font=font
    )

# =========================
# SAVE
# =========================

output_path = os.path.join(WORK_DIR, "final.png")
final_img.save(output_path)

print(f"\n🧱 Image finale générée : {output_path}")
print("=========================")
