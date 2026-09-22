import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.compiler import COLOR_INDEX, compile_design, print_ascii_layers


def _design(bbox, ops):
    return {"name": "test", "bbox": bbox, "ops": ops}


def test_box_fills_region():
    design = _design([5, 5, 5], [
        {"op": "box", "from": [0, 0, 0], "to": [2, 2, 2], "color": "red"}
    ])
    grid = compile_design(design)
    assert np.count_nonzero(grid) == 27
    assert grid[0, 0, 0] == COLOR_INDEX["red"]
    assert grid[2, 2, 2] == COLOR_INDEX["red"]
    assert grid[3, 0, 0] == 0


def test_box_3x3x1_ascii_cross_section_has_9_filled_voxels(capsys):
    design = _design([5, 5, 5], [
        {"op": "box", "from": [0, 0, 0], "to": [2, 2, 0], "color": "blue"}
    ])
    grid = compile_design(design)
    assert np.count_nonzero(grid[:, :, 0]) == 9
    print_ascii_layers(grid)
    out = capsys.readouterr().out
    assert out.count("#") == 9


def test_carve_empties_region():
    design = _design([5, 5, 5], [
        {"op": "box", "from": [0, 0, 0], "to": [4, 4, 4], "color": "white"},
        {"op": "carve", "from": [1, 1, 1], "to": [3, 3, 3]},
    ])
    grid = compile_design(design)
    assert grid[2, 2, 2] == 0
    assert grid[0, 0, 0] == COLOR_INDEX["white"]


def test_column_creates_vertical_line():
    design = _design([5, 5, 10], [
        {"op": "column", "at": [1, 1, 0], "height": 5, "color": "green"}
    ])
    grid = compile_design(design)
    assert np.count_nonzero(grid) == 5
    assert list(grid[1, 1, :5]) == [COLOR_INDEX["green"]] * 5
    assert grid[1, 1, 5] == 0


def test_layer_ascii_grid():
    design = _design([6, 6, 3], [
        {"op": "layer", "z": 1, "origin": [1, 1], "grid": [".#.", "###", ".#."], "color": "yellow"}
    ])
    grid = compile_design(design)
    assert np.count_nonzero(grid) == 5
    assert grid[2, 1, 1] == COLOR_INDEX["yellow"]  # centre du '#' du haut
    assert grid[1, 2, 1] == COLOR_INDEX["yellow"]  # bras gauche


def test_mirror_duplicates_symmetric():
    design = _design([6, 6, 1], [
        {"op": "box", "from": [0, 0, 0], "to": [0, 0, 0], "color": "black"},
        {"op": "mirror", "axis": "x"},
    ])
    grid = compile_design(design)
    assert grid[0, 0, 0] == COLOR_INDEX["black"]
    assert grid[5, 0, 0] == COLOR_INDEX["black"]
    assert np.count_nonzero(grid) == 2


def test_translate_group_repeats():
    design = _design([10, 3, 3], [
        {
            "op": "translate_group",
            "ops": [{"op": "column", "at": [0, 0, 0], "height": 2, "color": "orange"}],
            "delta": [2, 0, 0],
            "count": 3,
        }
    ])
    grid = compile_design(design)
    for x in (0, 2, 4):
        assert list(grid[x, 0, :2]) == [COLOR_INDEX["orange"]] * 2
    assert np.count_nonzero(grid) == 6


def test_composite_box_then_carve_then_mirror():
    design = _design([8, 8, 3], [
        {"op": "box", "from": [0, 0, 0], "to": [3, 3, 0], "color": "tan"},
        {"op": "carve", "from": [1, 1, 0], "to": [1, 1, 0]},
        {"op": "mirror", "axis": "x"},
    ])
    grid = compile_design(design)
    assert grid[1, 1, 0] == 0  # carve a bien vide
    assert grid[0, 0, 0] == COLOR_INDEX["tan"]
    assert grid[7, 0, 0] == COLOR_INDEX["tan"]  # miroir de x=0 sur bbox x=8 -> 7


def test_out_of_bounds_box_is_clamped_not_raised(capsys):
    design = _design([4, 4, 4], [
        {"op": "box", "from": [0, 0, 0], "to": [3, 3, 3], "color": "red"}
    ])
    # 'to' volontairement hors bbox globale n'est pas possible via le schema (deja
    # borne a 48/48/60) ; ici on verifie le clamp reel : une box qui deborde de LA
    # grille de CE design (bbox [4,4,4]) mais reste dans les bornes globales du schema.
    design["ops"][0]["to"] = [3, 3, 3]
    design["bbox"] = [4, 4, 4]
    design["ops"].append({"op": "column", "at": [0, 0, 0], "height": 10, "color": "blue"})
    grid = compile_design(design)  # ne doit pas lever
    warnings_out = capsys.readouterr().out
    assert "clampee" in warnings_out
    assert grid[0, 0, 3] == COLOR_INDEX["blue"]  # colonne clampee au sommet de la grille
