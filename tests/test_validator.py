import os
import sys

import numpy as np

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


def _toit_creux(taille):
    """Boite creuse coiffee d'un toit plat : des plaques centrales n'ont rien sous
    leur emprise, donc des flottantes, en nombre croissant avec la taille."""
    n = taille - 1
    return _design([taille, taille, 4], [
        {"op": "box", "from": [0, 0, 0], "to": [n, n, 2], "color": "tan"},
        {"op": "carve", "from": [1, 1, 1], "to": [n - 1, n - 1, 2]},
        {"op": "box", "from": [0, 0, 3], "to": [n, n, 3], "color": "red"},
    ])


def test_flottantes_penalisees_au_taux_et_non_au_nombre():
    """A taux de defaut comparable, agrandir un modele ne doit pas baisser son score.

    L'ancienne formule retirait 5 points PAR piece flottante, plafonnes a -20. Un
    modele atteignait donc le plafond des qu'il avait 4 flottantes, ce qui arrive
    mecaniquement quand il grossit, alors qu'un modele de 7 pieces ne pouvait pas
    structurellement l'atteindre. Mesure : 73,7 pour le 20x20 contre 77,0 pour le
    14x14 — le plus gros etait classe derriere pour un taux de defaut equivalent.
    """
    rapports = {}
    for taille in (14, 20):
        design = _toit_creux(taille)
        voxels = compile_design(design)
        placements = tile(voxels)
        rapports[taille] = validate(voxels, placements, design)

    petit, grand = rapports[14], rapports[20]
    flottantes = {t: sum(1 for w in r["warnings"] if "flottante" in w)
                  for t, r in rapports.items()}

    # Premisse du test : le grand a bien PLUS de flottantes en nombre absolu, et
    # depasse le plafond de 4 — sans quoi l'ancienne formule ne serait pas en cause.
    assert flottantes[20] > flottantes[14] >= 1, flottantes
    assert flottantes[20] >= 4, flottantes
    assert grand["errors"] == [] and petit["errors"] == []

    # ... pour un taux comparable : c'est ce qui rend la comparaison legitime.
    taux = {t: flottantes[t] / r["metrics"]["n_pieces"] for t, r in rapports.items()}
    assert abs(taux[20] - taux[14]) < 0.05, taux

    # L'invariant. Avec le compte plafonne, grand["score"] valait 73,7 et ne pouvait
    # pas depasser 80 ; il est maintenant au-dessus du petit.
    assert grand["score"] >= petit["score"], (petit["score"], grand["score"])
    assert grand["score"] > 80, grand["score"]


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


# --- Constructibilite annoncee en francais (US-03) ---------------------------------

def test_resume_modele_sain_ne_dit_rien():
    from src.validator import resume_constructibilite

    r = resume_constructibilite({"metrics": {"n_components": 1, "z_min": 0,
                                             "flottantes": [], "fill_rate": 0.4}})
    assert r["ok"] is True and r["phrases"] == []


def test_resume_nomme_chaque_defaut():
    from src.validator import resume_constructibilite

    r = resume_constructibilite({"metrics": {
        "n_components": 3, "z_min": 2, "flottantes": [[1, 1, 1], [2, 2, 2]],
        "fill_rate": 0.92}})
    assert r["ok"] is False
    assert len(r["phrases"]) == 4
    joint = " ".join(r["phrases"])
    assert "3 morceaux" in joint
    assert "2 plate(s)" in joint
    assert "2 piece(s)" in joint
    assert "92%" in joint


def test_resume_sans_vocabulaire_interne():
    """L'utilisateur ne doit jamais lire 'voxel' ni 'composante connexe'."""
    from src.validator import resume_constructibilite

    r = resume_constructibilite({"metrics": {
        "n_components": 2, "z_min": 1, "flottantes": [[0, 0, 1]], "fill_rate": 0.9}})
    for phrase in r["phrases"]:
        bas = phrase.lower()
        for mot in ("voxel", "composante", "connex", "bbox", "iou", "placement"):
            assert mot not in bas, f"vocabulaire interne dans : {phrase}"


def test_resume_tolere_un_rapport_incomplet():
    from src.validator import resume_constructibilite

    assert resume_constructibilite({})["ok"] is True
    assert resume_constructibilite({"metrics": {}})["ok"] is True


def test_pieces_flottantes_est_coherent_avec_check_floating():
    """Les deux doivent voir exactement les memes pieces : l'un derive de l'autre."""
    from src.compiler import compile_design
    from src.tiler import tile
    from src.validator import check_floating, pieces_flottantes

    # Toit plat au-dessus d'un vide : le cas qui genere des pieces sans support.
    design = {"name": "t", "bbox": [10, 10, 6], "ops": [
        {"op": "box", "from": [0, 0, 0], "to": [9, 9, 5], "color": "tan"},
        {"op": "carve", "from": [1, 1, 1], "to": [6, 6, 3]},
    ]}
    voxels = compile_design(design)
    placements = tile(voxels)
    flottantes = pieces_flottantes(placements, voxels)
    assert flottantes, "le modele de test doit produire des pieces sans support"
    assert len(check_floating(placements, voxels)) == len(flottantes)


def test_metrics_expose_les_coordonnees_des_flottantes():
    """La notice en a besoin pour nommer les etapes concernees."""
    from src.compiler import compile_design
    from src.tiler import tile
    from src.validator import pieces_flottantes, validate

    design = {"name": "t", "bbox": [10, 10, 6], "ops": [
        {"op": "box", "from": [0, 0, 0], "to": [9, 9, 5], "color": "tan"},
        {"op": "carve", "from": [1, 1, 1], "to": [6, 6, 3]},
    ]}
    voxels = compile_design(design)
    placements = tile(voxels)
    rapport = validate(voxels, placements, design)
    attendu = {(p.x, p.y, p.z) for p in pieces_flottantes(placements, voxels)}
    assert attendu, "le modele de test doit produire des pieces sans support"
    assert {tuple(c) for c in rapport["metrics"]["flottantes"]} == attendu
