"""Compilateur DSL -> grille de voxels (Phase 1, tache 1.4). Deterministe, aucun appel LLM.

Ne devine jamais si une operation depasse le bbox : elle est clampee dans la grille et un
avertissement est journalise (jamais d'exception a ce stade — la validation stricte est
la responsabilite de src/validator.py, Phase 2).
"""

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.dsl_schema import PALETTE_NAMES, validate_design

COLOR_INDEX = {name: i + 1 for i, name in enumerate(PALETTE_NAMES)}  # 0 = vide
INDEX_COLOR = {v: k for k, v in COLOR_INDEX.items()}


def _clamp(point, maxima):
    clamped = [max(0, min(v, m)) for v, m in zip(point, maxima)]
    return clamped, clamped != list(point)


def _apply_box(grid, op, warnings, op_idx):
    shape = grid.shape
    (x0, y0, z0), c0 = _clamp(op["from"], [s - 1 for s in shape])
    (x1, y1, z1), c1 = _clamp(op["to"], [s - 1 for s in shape])
    if c0 or c1:
        warnings.append(f"op #{op_idx} (box) : coordonnees hors grille, clampees")
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    za, zb = sorted((z0, z1))
    grid[xa:xb + 1, ya:yb + 1, za:zb + 1] = COLOR_INDEX[op["color"]]


def _apply_carve(grid, op, warnings, op_idx):
    shape = grid.shape
    (x0, y0, z0), c0 = _clamp(op["from"], [s - 1 for s in shape])
    (x1, y1, z1), c1 = _clamp(op["to"], [s - 1 for s in shape])
    if c0 or c1:
        warnings.append(f"op #{op_idx} (carve) : coordonnees hors grille, clampees")
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    za, zb = sorted((z0, z1))
    grid[xa:xb + 1, ya:yb + 1, za:zb + 1] = 0


def _apply_column(grid, op, warnings, op_idx):
    shape = grid.shape
    (x, y, z0), clamped_at = _clamp(op["at"], [s - 1 for s in shape])
    z1 = z0 + op["height"] - 1
    clamped_height = z1 > shape[2] - 1
    z1 = min(z1, shape[2] - 1)
    if clamped_at or clamped_height:
        warnings.append(f"op #{op_idx} (column) : hors grille, clampee")
    grid[x, y, z0:z1 + 1] = COLOR_INDEX[op["color"]]


def _apply_layer(grid, op, warnings, op_idx):
    shape = grid.shape
    ox, oy = op["origin"]
    z = op["z"]
    if not (0 <= z <= shape[2] - 1):
        warnings.append(f"op #{op_idx} (layer) : z={z} hors grille, operation ignoree")
        return
    color_idx = COLOR_INDEX[op["color"]]
    any_clamped = False
    for row_i, row in enumerate(op["grid"]):
        for col_i, ch in enumerate(row):
            if ch != "#":
                continue
            x, y = ox + col_i, oy + row_i
            if not (0 <= x <= shape[0] - 1 and 0 <= y <= shape[1] - 1):
                any_clamped = True
                continue
            grid[x, y, z] = color_idx
    if any_clamped:
        warnings.append(f"op #{op_idx} (layer) : certains voxels hors grille, ignores")


def _apply_mirror(grid, op, warnings, op_idx):
    axis_idx = {"x": 0, "y": 1, "z": 2}[op["axis"]]
    source = grid.copy()
    xs, ys, zs = np.nonzero(source)
    limit = grid.shape[axis_idx] - 1
    coords = [xs, ys, zs]
    coords[axis_idx] = limit - coords[axis_idx]
    grid[tuple(coords)] = source[xs, ys, zs]


def _shift_point(p, delta):
    return [p[i] + delta[i] for i in range(3)]


def _shift_op(op, delta):
    shifted = dict(op)
    if op["op"] in ("box", "carve"):
        shifted["from"] = _shift_point(op["from"], delta)
        shifted["to"] = _shift_point(op["to"], delta)
    elif op["op"] == "column":
        shifted["at"] = _shift_point(op["at"], delta)
    elif op["op"] == "layer":
        shifted["origin"] = [op["origin"][0] + delta[0], op["origin"][1] + delta[1]]
        shifted["z"] = op["z"] + delta[2]
    return shifted


def _apply_translate_group(grid, op, warnings, op_idx):
    delta = op["delta"]
    for i in range(op["count"]):
        cur_delta = [delta[k] * i for k in range(3)]
        for nested in op["ops"]:
            _dispatch(grid, _shift_op(nested, cur_delta), warnings, f"{op_idx}.t{i}")


def _dispatch(grid, op, warnings, op_idx):
    kind = op["op"]
    if kind == "box":
        _apply_box(grid, op, warnings, op_idx)
    elif kind == "carve":
        _apply_carve(grid, op, warnings, op_idx)
    elif kind == "column":
        _apply_column(grid, op, warnings, op_idx)
    elif kind == "layer":
        _apply_layer(grid, op, warnings, op_idx)
    elif kind == "mirror":
        _apply_mirror(grid, op, warnings, op_idx)
    elif kind == "translate_group":
        _apply_translate_group(grid, op, warnings, op_idx)
    else:
        raise ValueError(f"op #{op_idx} : operation inconnue '{kind}'")


def compile_design(design):
    """Retourne une grille (X, Y, Z) int16, 0 = vide, n = index couleur (COLOR_INDEX)."""
    validate_design(design)
    grid = np.zeros(tuple(design["bbox"]), dtype=np.int16)
    warnings = []
    for i, op in enumerate(design["ops"]):
        _dispatch(grid, op, warnings, i)
    for w in warnings:
        print(f"WARNING: {w}")
    return grid


def print_ascii_layers(grid):
    x_size, y_size, z_size = grid.shape
    for z in range(z_size):
        layer = grid[:, :, z]
        if not layer.any():
            continue
        print(f"--- z={z} ---")
        for y in range(y_size):
            print("".join("#" if layer[x, y] else "." for x in range(x_size)))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.compiler design.json")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        design_data = json.load(f)

    voxels = compile_design(design_data)
    out_path = os.path.splitext(sys.argv[1])[0] + "_voxels.npy"
    np.save(out_path, voxels)
    print(f"Voxels sauvegardes : {out_path} (shape={voxels.shape})")
    print_ascii_layers(voxels)
