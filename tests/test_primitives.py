import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.compiler import compile_design
from src.primitives import (
    build_reference,
    cross,
    cube,
    cylinder,
    pyramid,
    ring,
    sphere,
    stairs,
    voxel_iou,
)

# ---------- generateurs ----------

def test_cube_is_fully_filled():
    grid = cube(5)
    assert grid.shape == (5, 5, 5)
    assert np.count_nonzero(grid) == 125


def test_cylinder_has_constant_circular_section():
    grid = cylinder(9, 4)
    assert grid.shape == (9, 9, 4)
    sections = [np.count_nonzero(grid[:, :, z]) for z in range(4)]
    assert len(set(sections)) == 1, "toutes les couches doivent etre identiques"
    # un disque de diametre 9 remplit moins que le carre circonscrit
    assert sections[0] < 81


def test_sphere_is_widest_in_the_middle():
    grid = sphere(9)
    nz = grid.shape[2]
    per_layer = [np.count_nonzero(grid[:, :, z]) for z in range(nz)]
    assert per_layer[nz // 2] == max(per_layer)
    assert per_layer[0] < per_layer[nz // 2]
    assert per_layer[0] == per_layer[-1], "symetrique haut/bas"


def test_sphere_is_round_on_screen_not_in_the_grid():
    """Le defaut que ce test aurait attrape : une grille cubique donne une GALETTE.

    Un tenon fait 20 LDU de large, une plate 8 LDU de haut. Une sphere voxelisee dans
    une grille cubique est donc ecrasee 2,5x a l'ecran — ce qu'aucun test ne verifiait,
    et qu'un juge visuel a fini par signaler en notant 0/10 trois spheres a IoU 1,000.
    Ce qui compte est la longueur REELLE des demi-axes, pas le compte de cellules.
    """
    from src.primitives import LDU_PAR_PLATE, LDU_PAR_TENON

    for diametre in (4, 7, 9, 10):
        grid = sphere(diametre)
        pleins = np.argwhere(grid != 0)
        largeur = (pleins[:, 0].max() - pleins[:, 0].min() + 1) * LDU_PAR_TENON
        hauteur = (pleins[:, 2].max() - pleins[:, 2].min() + 1) * LDU_PAR_PLATE
        assert abs(largeur / hauteur - 1.0) < 0.05, (
            f"diametre {diametre} : {largeur} x {hauteur} LDU, "
            f"rapport {largeur / hauteur:.2f} au lieu de ~1")
        assert largeur == diametre * LDU_PAR_TENON, "la largeur reste le diametre demande"


def test_pyramid_shrinks_with_height():
    grid = pyramid(9, 5)
    per_layer = [np.count_nonzero(grid[:, :, z]) for z in range(5)]
    assert per_layer == sorted(per_layer, reverse=True)
    assert per_layer[0] > per_layer[-1]


def test_stairs_each_step_is_higher():
    grid = stairs(width=3, steps=4)
    heights = []
    for y in range(grid.shape[1]):
        column = grid[0, y, :]
        heights.append(int(np.count_nonzero(column)))
    assert heights == sorted(heights), "la hauteur doit croitre marche apres marche"
    assert heights[-1] == 4


def test_cross_arms_meet_at_center():
    grid = cross(arm=3, thickness=2, height=2)
    size = 2 * 3 + 2
    assert grid.shape == (size, size, 2)
    assert grid[size // 2, 0, 0] == 1   # bras vertical atteint le bord
    assert grid[0, size // 2, 0] == 1   # bras horizontal atteint le bord
    assert grid[0, 0, 0] == 0           # les coins restent vides


def test_ring_is_hollow_in_the_center():
    grid = ring(outer_diameter=11, inner_diameter=5, height=2)
    center = 11 // 2
    assert grid[center, center, 0] == 0, "le centre doit etre perce"
    assert grid[center, 0, 0] == 1 or grid[0, center, 0] == 1, "le bord doit etre plein"


def test_build_reference_dispatches_by_name():
    grid = build_reference({"shape": "cube", "params": {"side": 3}})
    assert grid.shape == (3, 3, 3)


# ---------- IoU ----------

def test_iou_identical_is_one():
    grid = sphere(7)
    assert voxel_iou(grid, grid) == 1.0


def test_iou_ignores_translation():
    reference = cube(4)
    shifted = np.zeros((10, 10, 10), dtype=np.int16)
    shifted[3:7, 5:9, 2:6] = 1
    assert voxel_iou(reference, shifted) == 1.0


def test_iou_penalises_wrong_size():
    assert voxel_iou(cube(8), cube(4)) < 0.2


def test_iou_disjoint_shapes_is_low():
    a = np.zeros((4, 4, 4), dtype=np.int16)
    a[0, 0, 0] = 1
    b = np.zeros((4, 4, 4), dtype=np.int16)
    b[0, 0, 0] = 1
    b[1, 1, 1] = 1
    assert voxel_iou(a, b) == 0.5


# ---------- expressivite du DSL ----------

def _grid_to_layer_ops(grid, name="test", color="red"):
    nx, ny, nz = grid.shape
    ops = []
    for z in range(nz):
        layer = grid[:, :, z]
        if not layer.any():
            continue
        rows = ["".join("#" if layer[x, y] else "." for x in range(nx)) for y in range(ny)]
        ops.append({"op": "layer", "z": z, "origin": [0, 0], "grid": rows, "color": color})
    return {"name": name, "bbox": [nx, ny, nz], "ops": ops}


def test_dsl_can_express_curved_shapes_exactly():
    """Prouve que le DSL n'est PAS le goulot d'etranglement sur les formes courbes :
    converties en operations `layer`, elles se recompilent a l'identique (IoU = 1).
    Le cout reel est ergonomique — une operation par tranche Z, et le modele doit
    rasteriser les cercles lui-meme."""
    for reference in (cylinder(9, 3), sphere(7), ring(9, 3, 2)):
        recompiled = compile_design(_grid_to_layer_ops(reference))
        assert voxel_iou(reference, recompiled) == 1.0


# --- IoU tolerant a l'orientation -------------------------------------------------

def test_iou_oriente_tolere_une_rotation():
    """Un escalier tourne reste un escalier : le prompt n'impose aucune direction.

    C'est ce qui faisait de prim_escalier le pire cas du jeu. voxel_iou recadre sur
    le contenu mais ne tourne pas : une forme exacte construite dans l'autre sens
    etait punie comme une erreur.
    """
    from src.primitives import build_reference, voxel_iou, voxel_iou_oriente

    ref = build_reference({"shape": "stairs", "params": {"width": 4, "steps": 5}})
    for k in (1, 2, 3):
        tourne = np.rot90(ref, k, axes=(0, 1))
        assert voxel_iou_oriente(ref, tourne) == 1.0, f"rotation {k * 90} deg"
    # Et la metrique principale, elle, punit bien ces rotations : c'est le constat.
    assert voxel_iou(ref, np.rot90(ref, 1, axes=(0, 1))) < 1.0


def test_iou_oriente_tolere_un_miroir():
    from src.primitives import build_reference, voxel_iou_oriente

    ref = build_reference({"shape": "stairs", "params": {"width": 4, "steps": 5}})
    assert voxel_iou_oriente(ref, ref[::-1]) == 1.0


def test_iou_oriente_ne_tolere_PAS_le_retournement_vertical():
    """Le haut et le bas ne sont pas interchangeables : un modele a l'envers ne
    tiendrait pas debout. Seul le plan horizontal est libre."""
    from src.primitives import build_reference, voxel_iou_oriente

    ref = build_reference({"shape": "stairs", "params": {"width": 4, "steps": 5}})
    a_lenvers = ref[:, :, ::-1]
    assert voxel_iou_oriente(ref, a_lenvers) < 1.0


def test_iou_oriente_toujours_superieur_ou_egal():
    """La metrique secondaire ne peut pas etre plus severe que la principale."""
    from src.primitives import build_reference, voxel_iou, voxel_iou_oriente

    ref = build_reference({"shape": "stairs", "params": {"width": 4, "steps": 5}})
    for autre in ({"shape": "cube", "params": {"side": 5}},
                  {"shape": "pyramid", "params": {"base": 6, "height": 4}},
                  {"shape": "stairs", "params": {"width": 4, "steps": 5}}):
        g = build_reference(autre)
        assert voxel_iou_oriente(ref, g) >= voxel_iou(ref, g)


def test_iou_oriente_sur_forme_identique():
    from src.primitives import build_reference, voxel_iou_oriente

    ref = build_reference({"shape": "cube", "params": {"side": 6}})
    assert voxel_iou_oriente(ref, ref) == 1.0
