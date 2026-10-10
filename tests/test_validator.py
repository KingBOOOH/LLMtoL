import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.catalog import PARTS
from src.compiler import compile_design
from src.tiler import Placement, tile
from src.validator import (
    check_bbox_respect,
    check_center_of_mass,
    check_collisions,
    check_connectivity,
    check_fill_rate,
    check_floating,
    check_symmetry,
    validate,
)

PART_1X1 = next(p for p in PARTS if p.ldraw_id == "3005")   # brique 1x1, h=3
PART_2X4 = next(p for p in PARTS if p.ldraw_id == "3001")   # brique 2x4, h=3


def _design(bbox, ops):
    return {"name": "test", "bbox": bbox, "ops": ops}


# ---------- collisions ----------

def test_collisions_good_no_overlap():
    voxels = np.ones((4, 4, 3), dtype=np.int16)
    p1 = Placement(PART_1X1, 1, 0, 0, 0, False)
    p2 = Placement(PART_1X1, 1, 1, 0, 0, False)
    assert check_collisions([p1, p2], voxels) == []


def test_collisions_bad_overlap():
    voxels = np.ones((4, 4, 3), dtype=np.int16)
    p1 = Placement(PART_2X4, 1, 0, 0, 0, False)
    p2 = Placement(PART_1X1, 1, 1, 0, 0, False)  # dans l'emprise de p1
    errors = check_collisions([p1, p2], voxels)
    assert len(errors) == 1


# ---------- floating ----------

def test_floating_good_grounded_and_supported():
    voxels = np.zeros((2, 2, 6), dtype=np.int16)
    voxels[0, 0, 0:3] = 1
    voxels[0, 0, 3:6] = 1
    p_bottom = Placement(PART_1X1, 1, 0, 0, 0, False)
    p_top = Placement(PART_1X1, 1, 0, 0, 3, False)
    assert check_floating([p_bottom, p_top], voxels) == []


def test_floating_bad_nothing_underneath():
    voxels = np.zeros((2, 2, 6), dtype=np.int16)
    voxels[0, 0, 3:6] = 1  # rempli seulement en haut, rien dessous (voxel-level)
    p_top = Placement(PART_1X1, 1, 0, 0, 3, False)
    errors = check_floating([p_top], voxels)
    assert len(errors) == 1


# ---------- connectivity ----------

def test_connectivity_good_single_component():
    voxels = np.zeros((5, 1, 1), dtype=np.int16)
    voxels[:, 0, 0] = 1
    n, errors = check_connectivity(voxels)
    assert n == 1
    assert errors == []


def test_connectivity_bad_two_components():
    voxels = np.zeros((5, 1, 1), dtype=np.int16)
    voxels[0, 0, 0] = 1
    voxels[4, 0, 0] = 1  # isole, pas adjacent
    n, errors = check_connectivity(voxels)
    assert n == 2
    assert len(errors) == 1


# ---------- bbox respect ----------

def test_bbox_respect_good():
    design = _design([5, 5, 5], [{"op": "box", "from": [0, 0, 0], "to": [4, 4, 4], "color": "red"}])
    assert check_bbox_respect(design) == []


def test_bbox_respect_bad():
    design = _design([5, 5, 5], [{"op": "column", "at": [0, 0, 3], "height": 10, "color": "red"}])
    warnings = check_bbox_respect(design)
    assert len(warnings) == 1


# ---------- symmetry ----------

def test_symmetry_good_matches_declared_mirror():
    voxels = np.zeros((6, 3, 3), dtype=np.int16)
    voxels[0, 0, 0] = 1
    voxels[5, 0, 0] = 1  # miroir exact sur x (limite=5)
    design = _design([6, 3, 3], [{"op": "mirror", "axis": "x"}])
    assert check_symmetry(voxels, design) == []


def test_symmetry_bad_does_not_match_declared_mirror():
    voxels = np.zeros((6, 3, 3), dtype=np.int16)
    voxels[0, 0, 0] = 1  # pas de miroir en x=5
    design = _design([6, 3, 3], [{"op": "mirror", "axis": "x"}])
    warnings = check_symmetry(voxels, design)
    assert len(warnings) == 1


# ---------- fill rate ----------

def test_fill_rate_good_sparse():
    voxels = np.zeros((10, 10, 10), dtype=np.int16)
    voxels[0, 0, 0] = 1
    rate, warnings = check_fill_rate(voxels)
    assert rate < 0.01
    assert warnings == []


def test_fill_rate_bad_solid_block():
    voxels = np.ones((4, 4, 4), dtype=np.int16)
    rate, warnings = check_fill_rate(voxels)
    assert rate == 1.0
    assert len(warnings) == 1


# ---------- center of mass ----------

def test_center_of_mass_good_supported():
    voxels = np.zeros((3, 3, 3), dtype=np.int16)
    voxels[1, 1, 0] = 1
    voxels[1, 1, 1] = 1
    assert check_center_of_mass(voxels) == []


def test_center_of_mass_bad_unsupported():
    voxels = np.zeros((5, 5, 3), dtype=np.int16)
    voxels[4, 4, 2] = 1  # tout le poids loin du centre, rien au sol sous le centroide
    voxels[0, 0, 2] = 1
    warnings = check_center_of_mass(voxels)
    assert len(warnings) == 1


# ---------- validate() bout en bout ----------

def test_validate_end_to_end_on_connected_design_has_no_blocking_errors():
    design = _design([4, 4, 2], [{"op": "box", "from": [0, 0, 0], "to": [3, 3, 0], "color": "red"}])
    voxels = compile_design(design)
    placements = tile(voxels)
    report = validate(voxels, placements, design)
    assert report["errors"] == []
    assert report["score"] > 80


def test_empty_model_is_blocking_and_scores_zero():
    """Regression : un modele vide passait tous les controles a vide et decrochait
    100/100 (constate sur creature_02 pendant le run v1)."""
    voxels = np.zeros((5, 5, 5), dtype=np.int16)
    design = _design([5, 5, 5], [{"op": "box", "from": [0, 0, 0], "to": [1, 1, 1], "color": "red"}])
    report = validate(voxels, [], design)

    assert report["errors"], "un modele vide doit etre bloquant"
    assert any("vide" in e for e in report["errors"])
    assert report["score"] == 0.0


def test_tiny_model_warns():
    voxels = np.zeros((5, 5, 5), dtype=np.int16)
    voxels[0, 0, 0] = 1
    design = _design([5, 5, 5], [{"op": "box", "from": [0, 0, 0], "to": [0, 0, 0], "color": "red"}])
    report = validate(voxels, tile(voxels), design)
    assert any("minuscule" in w for w in report["warnings"])


def test_floating_pieces_warn_but_do_not_block():
    """Ecart assume avec la table du plan : une piece sans rien directement en-dessous
    (typiquement un toit plat au-dessus d'une piece creuse) est un AVERTISSEMENT, pas
    un bloquant, tant que la structure reste une seule composante connexe."""
    # interieur creux volontairement plus large que la plus grande piece (8 tenons),
    # pour qu'au moins une plaque de toit ne touche aucun mur dans son emprise
    design = _design([14, 14, 4], [
        {"op": "box", "from": [0, 0, 0], "to": [13, 13, 2], "color": "tan"},
        {"op": "carve", "from": [1, 1, 1], "to": [12, 12, 2]},
        {"op": "box", "from": [0, 0, 3], "to": [13, 13, 3], "color": "red"},
    ])
    voxels = compile_design(design)
    placements = tile(voxels)
    report = validate(voxels, placements, design)

    assert report["metrics"]["n_components"] == 1
    assert report["errors"] == []
    assert any("flottante" in w for w in report["warnings"])


def test_validate_end_to_end_score_is_bounded():
    design = _design([4, 4, 2], [{"op": "box", "from": [0, 0, 0], "to": [3, 3, 0], "color": "red"}])
    voxels = compile_design(design)
    placements = tile(voxels)
    report = validate(voxels, placements, design)
    assert 0 <= report["score"] <= 100


def test_ground_contact_flags_model_starting_at_z1():
    """Le decalage d'indice z (defaut mesure : 8 designs sur 26) doit etre nomme."""
    from src.validator import check_ground_contact

    voxels = np.zeros((4, 4, 4), dtype=np.int16)
    voxels[0:2, 0:2, 1:3] = 1
    z_min, warnings = check_ground_contact(voxels)
    assert z_min == 1
    assert len(warnings) == 1 and "ne touche pas le sol" in warnings[0]


def test_ground_contact_silent_when_model_sits_on_floor():
    from src.validator import check_ground_contact

    voxels = np.zeros((4, 4, 4), dtype=np.int16)
    voxels[0:2, 0:2, 0:2] = 1
    assert check_ground_contact(voxels) == (0, [])
