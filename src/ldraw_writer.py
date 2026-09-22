"""Export Placement -> fichier .ldr (Phase 1, tache 1.6). Deterministe, aucun appel LLM.

Constantes figees empiriquement (tache 1.1, tests/fixtures/handmade_two_bricks.ldr,
valide visuellement dans LeoCAD par l'utilisateur) :

- LDU_PER_STUD = 20 : 1 tenon (axes X et Z LDraw) = 20 LDU. Confirme par le decalage
  d'un tenon du test (20 LDU en X), visible comme un decalage d'exactement une largeur
  de tenon entre les deux briques.
- LDU_PER_PLATE = 8 : 1 plate (axe Y LDraw, vertical) = 8 LDU.
- Y_SIGN et origine verticale : l'axe Y LDraw pointe vers le bas, et l'origine d'une
  piece est a la BASE DE SES TENONS (donc au sommet du corps de la piece, pas a sa
  base) — confirme par la doc LDraw.org (voir recherche web, pas seulement suppose).
  Le test a 2 briques de MEME hauteur (h=3) ne pouvait pas reveler ce detail (l'ecart
  s'annule quand toutes les pieces ont la meme hauteur) ; le bug est apparu sur un
  premier jet de maison melangeant briques (h=3) et plates (h=1) — pieces "flottantes"
  signalees par l'utilisateur, corrige ensuite via la doc officielle. Formule correcte,
  utilisant le sommet interne de la piece (z + h), pas sa base (z) :
      y = -((z + h) * LDU_PER_PLATE)
  Verifie par calcul : deux pieces adjacentes (piece B empilee juste au-dessus de piece
  A) donnent alors Y0_B = Y0_A - hauteur_B pile, sans recouvrement ni espace, quelles
  que soient leurs hauteurs respectives.
- Origine des pieces : centree sur les axes X/Z (pas au coin de la piece).
- Convention w/d de src.catalog.Part : w = emprise le long de X LDraw (matrice
  identite), d = emprise le long de Z LDraw. Confirme par le test : la piece 3001
  ("brique 2x4", catalogue w=4 d=2) a bien son cote de 4 tenons le long de X.
- Rotation 90 degres autour de Y (Placement.rotated=True) : matrice
  "0 0 1 0 1 0 -1 0 0" (donnee dans le plan) — echange correctement les emprises
  X/Z d'apres la convention w=X, d=Z ci-dessus (verifie par calcul, pas seulement
  suppose : world_x = local_z, world_z = -local_x).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.catalog import PALETTE
from src.compiler import INDEX_COLOR

LDU_PER_STUD = 20
LDU_PER_PLATE = 8

IDENTITY = (1, 0, 0, 0, 1, 0, 0, 0, 1)
ROTATE_90_Y = (0, 0, 1, 0, 1, 0, -1, 0, 0)


def _placement_sort_key(p):
    return (p.z, p.y, p.x)


def _ldraw_line(placement):
    part = placement.part
    color_name = INDEX_COLOR[placement.color_index]
    ldraw_code = PALETTE[color_name]["ldraw_code"]

    w, d = (part.d, part.w) if placement.rotated else (part.w, part.d)
    ldraw_x = round((placement.x + w / 2) * LDU_PER_STUD)
    ldraw_y = -((placement.z + part.h) * LDU_PER_PLATE)
    ldraw_z = round((placement.y + d / 2) * LDU_PER_STUD)

    matrix = ROTATE_90_Y if placement.rotated else IDENTITY
    matrix_str = " ".join(str(v) for v in matrix)

    return f"1 {ldraw_code} {ldraw_x} {ldraw_y} {ldraw_z} {matrix_str} {part.ldraw_id}.dat"


def write_ldr(placements, path, name):
    """Ecrit `placements` (issus de src.tiler.tile) dans un fichier .ldr a `path`."""
    ordered = sorted(placements, key=_placement_sort_key)

    lines = [
        f"0 FILE {name}",
        f"0 Name: {name}",
        "0 Author: pipeline LEGO (genere automatiquement)",
        "0 !LDRAW_ORG Unofficial_Model",
        "",
    ]
    lines.extend(_ldraw_line(p) for p in ordered)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    from src.compiler import compile_design
    from src.tiler import tile

    if len(sys.argv) < 2:
        print("Usage: python -m src.ldraw_writer design.json [output.ldr]")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        design_data = json.load(f)

    voxels = compile_design(design_data)
    placements = tile(voxels)

    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(sys.argv[1])[0] + ".ldr"
    write_ldr(placements, out_path, design_data.get("name", "model"))
    print(f"{len(placements)} pieces ecrites dans {out_path}")
