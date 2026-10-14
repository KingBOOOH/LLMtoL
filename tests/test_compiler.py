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


def test_cylinder_matches_primitive_reference_exactly():
    """L'op cylinder doit produire EXACTEMENT la reference qui sert a mesurer l'IoU.

    C'est tout l'interet de l'operation : le modele declare le volume et n'a plus a
    rasteriser de cercle. Si les deux rasteriseurs divergeaient, l'IoU deviendrait
    faux sans que rien ne le signale.
    """
    from src.primitives import build_reference, voxel_iou

    grid = compile_design(_design([8, 8, 10], [
        {"op": "cylinder", "at": [0, 0, 0], "diameter": 8, "height": 10, "color": "red"}
    ]))
    reference = build_reference({"shape": "cylinder", "params": {"diameter": 8, "height": 10}})
    assert voxel_iou(reference, grid) == 1.0
    # Un cercle, pas un carre : les coins du bbox restent vides.
    assert grid[0, 0, 0] == 0
    assert grid[4, 0, 0] == COLOR_INDEX["red"]


def test_sphere_matches_primitive_reference_exactly():
    """Le bbox doit etre AUSSI HAUT que la sphere est ronde : une sphere de 10 tenons
    de diametre occupe 25 plates en Z, pas 10. C'est ce que borne build_schema."""
    from src.primitives import build_reference, hauteur_sphere, voxel_iou

    hauteur = hauteur_sphere(10)
    assert hauteur == 25
    grid = compile_design(_design([10, 10, hauteur], [
        {"op": "sphere", "at": [0, 0, 0], "diameter": 10, "color": "blue"}
    ]))
    reference = build_reference({"shape": "sphere", "params": {"diameter": 10}})
    assert voxel_iou(reference, grid) == 1.0
    assert grid[0, 0, 0] == 0
    assert grid[5, 5, hauteur // 2] == COLOR_INDEX["blue"]


def test_cylinder_at_is_bbox_corner_not_centre():
    """`at` place le coin bas du volume : un cylindre pose en [2,2,1] ne touche pas z=0."""
    grid = compile_design(_design([10, 10, 6], [
        {"op": "cylinder", "at": [2, 2, 1], "diameter": 6, "height": 4, "color": "green"}
    ]))
    filled = np.argwhere(grid != 0)
    assert filled[:, 0].min() == 2 and filled[:, 1].min() == 2
    assert filled[:, 2].min() == 1 and filled[:, 2].max() == 4


def test_oversized_sphere_is_clipped_not_raised(capsys):
    """Une sphere trop grosse est rognee avec un avertissement, jamais une exception.

    Elle deborde desormais surtout par le HAUT : 10 tenons de diametre font 25 plates.
    """
    grid = compile_design(_design([6, 6, 6], [
        {"op": "sphere", "at": [0, 0, 0], "diameter": 10, "color": "red"}
    ]))
    assert np.count_nonzero(grid) > 0
    assert "hors grille" in capsys.readouterr().out
