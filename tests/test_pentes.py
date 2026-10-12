"""Pose des pentes par le pavage, et les quatre orientations.

Une pente n'est pas un pave : elle remplit sa rangee haute puis deborde d'une rangee
en descendant. Tout l'enjeu est de savoir QUAND le pavage a le droit d'en poser une.
Trois conditions, et chacune a son test ici :

  - rien au-dessus (une pente ne supporte rien, son dessus est incline) ;
  - la rangee ou descend la rampe est VIDE dans la grille ;
  - cette rangee n'est pas deja reclamee, sinon deux pentes face a face se
    percuteraient dans le creux qui les separe — et le validateur ne le verrait pas,
    puisqu'il ne connait que l'empreinte haute.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.catalog import DIRECTION_RAMPE, PARTS, est_une_pente
from src.ldraw_writer import IDENTITY, MATRICES, ROTATE_90_Y, ROTATE_180_Y, ROTATE_270_Y
from src.tiler import Placement, tile

ROUGE = 5  # un index de couleur quelconque, mais constant


def grille(x, y, z):
    return np.zeros((x, y, z), dtype=np.int16)


def pentes_de(placements):
    return [p for p in placements if est_une_pente(p.part)]


# --- le catalogue decrit bien l'empreinte HAUTE -----------------------------------

def test_une_pente_ne_declare_que_sa_rangee_haute():
    """w et d d'une pente decrivent les cases qu'elle REMPLIT, pas son emprise totale.
    Prendre l'emprise totale 1x2 decalerait le centrage LDraw d'un demi-tenon, en
    silence : l'origine de la piece est centree sur son tenon haut (verifie sur les
    sommets de 3040b.dat)."""
    for part in PARTS:
        if est_une_pente(part):
            assert part.d == 1, f"{part.ldraw_id} : une pente n'a qu'une rangee haute"
            assert part.h == 3, f"{part.ldraw_id} : une pente fait une brique de haut"


# --- quand une pente est posee ----------------------------------------------------

def test_une_marche_recoit_une_pente():
    """Le cas type : une colonne de hauteur brique, rien au-dessus, du vide a cote."""
    v = grille(4, 4, 3)
    v[1:3, 1:3, 0:3] = ROUGE          # un bloc 2x2x3 isole au milieu du vide
    placements = tile(v)
    assert pentes_de(placements), "une marche entouree de vide doit recevoir une pente"


def test_pas_de_pente_sous_quelque_chose():
    """Son dessus est incline : rien ne peut s'y poser."""
    v = grille(4, 4, 6)
    v[1:3, 1:3, 0:3] = ROUGE
    v[1:3, 1:3, 3:6] = ROUGE          # une seconde assise par-dessus
    placements = tile(v)
    basses = [p for p in pentes_de(placements) if p.z == 0]
    assert not basses, "la couche du bas porte une charge, elle ne peut pas etre inclinee"


def test_pas_de_pente_quand_la_rampe_tomberait_dans_la_matiere():
    """La rampe retirerait de la matiere que le modele demande."""
    v = grille(4, 4, 3)
    v[:, :, 0:3] = ROUGE              # bloc plein : aucune case libre autour
    placements = tile(v)
    assert not pentes_de(placements)


def test_pas_de_pente_quand_la_rampe_sortirait_de_la_grille():
    """Laisser la rampe depasser reviendrait a ignorer le bbox declare par le modele.
    Ici le bloc occupe toute la grille en X/Y : il n'y a nulle part ou descendre."""
    v = grille(2, 2, 3)
    v[:, :, :] = ROUGE
    assert not pentes_de(tile(v))


def test_deux_marches_face_a_face_ne_se_percutent_pas():
    """Deux pentes separees d'une seule case vide voudraient toutes deux y descendre.
    La premiere reserve la case ; la seconde doit renoncer. Aucun controle en aval ne
    rattraperait l'erreur, le validateur ne connaissant que l'empreinte haute."""
    v = grille(3, 1, 3)
    v[0, 0, 0:3] = ROUGE
    v[2, 0, 0:3] = ROUGE              # creux d'exactement une case en x=1
    placements = tile(v)
    pentes = pentes_de(placements)
    assert len(pentes) <= 1, "une seule des deux marches peut incliner vers le creux"


# --- l'invariant du pavage tient toujours -----------------------------------------

def _couverture_complete(v):
    """Chaque voxel plein est couvert exactement une fois par l'empreinte HAUTE."""
    compte = np.zeros(v.shape, dtype=np.int16)
    for p in tile(v):
        w, d = (p.part.d, p.part.w) if p.rotated else (p.part.w, p.part.d)
        compte[p.x:p.x + w, p.y:p.y + d, p.z:p.z + p.part.h] += 1
    return compte


def test_rien_n_est_laisse_decouvert_ni_couvert_deux_fois():
    """L'invariant central du pavage, que les pentes ne doivent pas entamer."""
    rng = np.random.default_rng(7)
    for _ in range(12):
        v = (rng.random((6, 6, 7)) < 0.55).astype(np.int16) * ROUGE
        compte = _couverture_complete(v)
        assert not np.any((v != 0) & (compte == 0)), "voxel plein non couvert"
        assert not np.any(compte > 1), "voxel couvert deux fois"
        assert not np.any((v == 0) & (compte > 0)), "piece posee sur du vide"


def test_une_pyramide_reste_entierement_couverte():
    """Le cas ou les pentes abondent : chaque gradin en recoit."""
    v = grille(9, 9, 9)
    for etage in range(3):
        marge = etage * 2
        v[marge:9 - marge, marge:9 - marge, etage * 3:(etage + 1) * 3] = ROUGE
    placements = tile(v)
    assert len(pentes_de(placements)) > 0
    compte = _couverture_complete(v)
    assert not np.any((v != 0) & (compte == 0))
    assert not np.any((v == 0) & (compte > 0))


# --- les quatre orientations ------------------------------------------------------

def test_les_quatre_orientations_sont_distinctes():
    assert len(set(DIRECTION_RAMPE.values())) == 4
    assert len({MATRICES[k] for k in MATRICES}) == 4


def test_la_direction_de_rampe_releve_du_rendu_de_verification():
    """Verrouille la constante EMPIRIQUE. Elle n'a pas ete calculee de tete : elle a
    ete relevee sur un rendu des quatre matrices, ou le tenon marque la partie haute.
    Si quelqu'un la "corrige" par raisonnement, les toits partiront a l'envers."""
    assert DIRECTION_RAMPE[(False, False)] == (0, -1)
    assert DIRECTION_RAMPE[(True, False)] == (-1, 0)
    assert DIRECTION_RAMPE[(False, True)] == (0, 1)
    assert DIRECTION_RAMPE[(True, True)] == (1, 0)


def test_chaque_orientation_a_sa_matrice():
    assert MATRICES[(False, False)] == IDENTITY
    assert MATRICES[(True, False)] == ROTATE_90_Y
    assert MATRICES[(False, True)] == ROTATE_180_Y
    assert MATRICES[(True, True)] == ROTATE_270_Y


def test_les_matrices_sont_des_rotations_pures():
    """Un determinant de -1 signifierait une symetrie : la piece serait rendue en
    miroir, ce qui pour une pente se verrait comme une descente du mauvais cote."""
    for m in MATRICES.values():
        a = np.array(m, dtype=float).reshape(3, 3)
        assert np.isclose(np.linalg.det(a), 1.0)
        assert np.allclose(a @ a.T, np.eye(3))


def test_le_demi_tour_n_echange_pas_l_empreinte():
    """L'empreinte ne depend que de `rotated`. Si un demi-tour echangeait w et d, le
    validateur verrait des collisions la ou il n'y en a pas."""
    from src.validator import _footprint

    piece = next(p for p in PARTS if p.ldraw_id == "3039")
    base = Placement(piece, ROUGE, 0, 0, 0, False, False)
    assert _footprint(base) == _footprint(base._replace(retournee=True))
    assert _footprint(base._replace(rotated=True)) == (piece.d, piece.w)


def test_le_champ_retournee_a_une_valeur_par_defaut():
    """Les appels existants a six arguments doivent rester valides : le pavage n'est
    pas le seul producteur de Placement (tests, outils)."""
    p = Placement(PARTS[0], ROUGE, 1, 2, 3, False)
    assert p.retournee is False


# --- le fichier LDraw produit -----------------------------------------------------

def test_une_pente_est_ecrite_avec_sa_matrice(tmp_path):
    from src.ldraw_writer import write_ldr

    piece = next(p for p in PARTS if p.ldraw_id == "3040b")
    chemin = tmp_path / "m.ldr"
    write_ldr([Placement(piece, ROUGE, 0, 0, 0, True, True)], str(chemin), "essai")
    ligne = next(ligne for ligne in chemin.read_text(encoding="utf-8").splitlines()
                 if ligne.startswith("1 "))
    assert "3040b.dat" in ligne
    assert " ".join(str(v) for v in ROTATE_270_Y) in ligne
