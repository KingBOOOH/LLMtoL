"""Pavage voxels -> vraies pieces LEGO (Phase 1, tache 1.5). Deterministe, aucun appel LLM.

Algorithme glouton, couche par couche (z croissant), decrit dans le plan section
"Tache 1.5" : a chaque voxel plein et non couvert, on cherche la piece du catalogue au
volume maximal (w*d*h) qui tient entierement dans des voxels pleins non couverts de la
meme couleur, avec une penalite pour les pieces dont un bord coincide exactement avec un
bord de la couche z-1 (regle de maconnerie : evite que le mur se fende verticalement).

La piece 3024 (plate 1x1) garantit toujours une couverture : l'algorithme se termine.
"""

import os
import sys
from collections import namedtuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.catalog import PARTS

Placement = namedtuple("Placement", ["part", "color_index", "x", "y", "z", "rotated"])

_SORTED_PARTS = sorted(PARTS, key=lambda p: -(p.w * p.d * p.h))


def _remaining_height(voxels, x, y, z, color, max_h=3):
    z_size = voxels.shape[2]
    h = 0
    while z + h < z_size and h < max_h and voxels[x, y, z + h] == color:
        h += 1
    return h


def _fits(voxels, covered, x, y, z, w, d, h, color):
    x_size, y_size, z_size = voxels.shape
    if x + w > x_size or y + d > y_size or z + h > z_size:
        return False
    region_voxels = voxels[x:x + w, y:y + d, z:z + h]
    region_covered = covered[x:x + w, y:y + d, z:z + h]
    if np.any(region_covered):
        return False
    return bool(np.all(region_voxels == color))


def _interior_edges(x, y, w, d, x_size, y_size):
    """Bords d'une piece, en excluant les bords qui coincident avec le contour exterieur
    de la grille (ce ne sont pas des joints internes, ils sont toujours "alignes" d'une
    couche a l'autre par construction et n'indiquent aucune fissure)."""
    xs = {v for v in (x, x + w) if 0 < v < x_size}
    ys = {v for v in (y, y + d) if 0 < v < y_size}
    return xs, ys


def _joint_penalty(prev_layer_edges, x, y, w, d, x_size, y_size):
    xs, ys = _interior_edges(x, y, w, d, x_size, y_size)
    penalty = len(xs & prev_layer_edges["x"]) + len(ys & prev_layer_edges["y"])
    return penalty


def _best_candidate(voxels, covered, x, y, z, color, remaining, prev_layer_edges, x_size, y_size):
    best = None
    for part in _SORTED_PARTS:
        if part.h > remaining:
            continue
        orientations = [(part.w, part.d, False)]
        if part.w != part.d:
            orientations.append((part.d, part.w, True))
        for w, d, rotated in orientations:
            if not _fits(voxels, covered, x, y, z, w, d, part.h, color):
                continue
            penalty = _joint_penalty(prev_layer_edges, x, y, w, d, x_size, y_size)
            # la penalite prime sur le volume : eviter une fissure de maconnerie vaut
            # mieux qu'une piece plus grande qui prolonge exactement le joint du dessous.
            key = (penalty, -(w * d * part.h))
            if best is None or key < best[0]:
                best = (key, part, w, d, rotated)
    return best


def tile(voxels):
    """Retourne la liste des Placement couvrant tous les voxels pleins de `voxels`.

    Assert (garanti par le fallback 1x1) qu'aucun voxel plein ne reste non couvert.
    """
    x_size, y_size, z_size = voxels.shape
    covered = np.zeros_like(voxels, dtype=bool)
    placements = []
    # edges_by_z[z] = bords interieurs des pieces qui OCCUPENT la couche z (pas
    # seulement celles qui y demarrent) — indispensable pour qu'une brique haute de 3
    # couches continue de "compter" comme bord pour la couche juste au-dessus d'elle.
    edges_by_z = [{"x": set(), "y": set()} for _ in range(z_size)]

    for z in range(z_size):
        prev_layer_edges = edges_by_z[z - 1] if z > 0 else {"x": set(), "y": set()}
        for y in range(y_size):
            for x in range(x_size):
                if voxels[x, y, z] == 0 or covered[x, y, z]:
                    continue
                color = voxels[x, y, z]
                remaining = _remaining_height(voxels, x, y, z, color)
                best = _best_candidate(voxels, covered, x, y, z, color, remaining,
                                        prev_layer_edges, x_size, y_size)
                if best is None:
                    raise RuntimeError(
                        f"Aucune piece ne peut couvrir le voxel ({x},{y},{z}) — "
                        "ne devrait jamais arriver (fallback plate 1x1 garanti)."
                    )
                _, part, w, d, rotated = best
                covered[x:x + w, y:y + d, z:z + part.h] = True
                placements.append(Placement(part, int(color), x, y, z, rotated))
                xs, ys = _interior_edges(x, y, w, d, x_size, y_size)
                for zz in range(z, min(z + part.h, z_size)):
                    edges_by_z[zz]["x"].update(xs)
                    edges_by_z[zz]["y"].update(ys)

    uncovered = int(np.count_nonzero((voxels != 0) & (~covered)))
    assert uncovered == 0, f"{uncovered} voxels non couverts apres tiling"

    return placements


def joint_alignment_rate(placements):
    """Fraction des bords INTERNES (le contour exterieur ne compte pas) des pieces
    d'une couche qui coincident exactement avec un bord de la couche immediatement
    inferieure. 0 = aucun alignement (bon), 1 = tout aligne (mur qui se fend)."""
    if not placements:
        return 0.0

    x_size = max(p.x + (p.part.d if p.rotated else p.part.w) for p in placements)
    y_size = max(p.y + (p.part.w if p.rotated else p.part.d) for p in placements)

    edges_by_z = {}
    for p in placements:
        w, d = (p.part.d, p.part.w) if p.rotated else (p.part.w, p.part.d)
        xs, ys = _interior_edges(p.x, p.y, w, d, x_size, y_size)
        e = edges_by_z.setdefault(p.z, {"x": set(), "y": set()})
        e["x"].update(xs)
        e["y"].update(ys)

    zs = sorted(edges_by_z)
    total = 0
    aligned = 0
    for i in range(1, len(zs)):
        cur, prev = edges_by_z[zs[i]], edges_by_z[zs[i - 1]]
        for axis in ("x", "y"):
            for e in cur[axis]:
                total += 1
                if e in prev[axis]:
                    aligned += 1
    return aligned / total if total else 0.0
