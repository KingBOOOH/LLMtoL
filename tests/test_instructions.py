"""Decoupage en etapes et generation de la notice (src/instructions.py).

Module non teste jusqu'ici, alors que US-02 et US-03 vont le reecrire en profondeur :
ces tests sont le filet qui dira si ces reecritures cassent quelque chose.

Aucun rendu Blender ici — les images d'etapes demandent Blender, ce qui n'a pas sa
place dans une suite qui doit tourner en une seconde. On teste le decoupage, les cles
de pieces, les .ldr d'etapes et le HTML.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.compiler import compile_design
from src.instructions import (PIECES_PAR_ETAPE, piece_key, split_steps,
                              write_notice_html, write_step_models)
from src.tiler import tile


def _placements(bbox=(8, 8, 6), to=None):
    """Pave plein : simple, deterministe, et assez gros pour faire plusieurs etapes."""
    to = to or [bbox[0] - 1, bbox[1] - 1, bbox[2] - 1]
    design = {"name": "pave", "bbox": list(bbox),
              "ops": [{"op": "box", "from": [0, 0, 0], "to": to, "color": "red"}]}
    return tile(compile_design(design))


def test_split_steps_taille_fixe():
    p = _placements()
    etapes = split_steps(p, pieces_par_etape=5)
    assert all(len(e) == 5 for e in etapes[:-1]), "seule la derniere etape peut etre partielle"
    assert 1 <= len(etapes[-1]) <= 5
    assert sum(len(e) for e in etapes) == len(p), "aucune piece perdue ni dupliquee"


def test_split_steps_defaut():
    etapes = split_steps(_placements())
    assert all(len(e) <= PIECES_PAR_ETAPE for e in etapes)


def test_split_steps_ordre_de_montage_valide():
    """On ne doit jamais poser une piece sous une piece deja posee.

    Le z de chaque etape doit donc etre croissant (ou egal) d'une etape a l'autre.
    C'est la seule propriete du decoupage qui soit physiquement obligatoire.
    """
    etapes = split_steps(_placements(), pieces_par_etape=4)
    z_max_precedent = -1
    for etape in etapes:
        z_min_etape = min(p.z for p in etape)
        assert z_min_etape >= z_max_precedent, "une etape redescend sous la precedente"
        z_max_precedent = max(p.z for p in etape)


def test_split_steps_liste_vide():
    assert split_steps([]) == []


def test_piece_key_distingue_reference_et_couleur():
    p = _placements()[0]
    assert piece_key(p) == (p.part.ldraw_id, p.color_index)

    bleu = {"name": "t", "bbox": [4, 4, 1],
            "ops": [{"op": "box", "from": [0, 0, 0], "to": [3, 3, 0], "color": "blue"}]}
    q = tile(compile_design(bleu))[0]
    rouge = _placements(bbox=(4, 4, 1), to=[3, 3, 0])[0]
    assert piece_key(q) != piece_key(rouge), "deux couleurs = deux entrees d'encart"


def test_write_step_models_cumulatif(tmp_path):
    """Chaque etape montre l'assemblage COMPLET a ce stade, pas seulement ses pieces."""
    p = _placements()
    dossiers, etapes = write_step_models(p, str(tmp_path), "pave")

    assert len(dossiers) == len(etapes)
    for i, d in enumerate(dossiers, 1):
        ldr = os.path.join(d, "model.ldr")
        assert os.path.isfile(ldr), f"etape {i} sans model.ldr"
        lignes = [l for l in open(ldr, encoding="utf-8").read().splitlines()
                  if l.startswith("1 ")]
        attendu = sum(len(e) for e in etapes[:i])
        assert len(lignes) == attendu, f"etape {i} : {len(lignes)} pieces au lieu de {attendu}"


def test_write_notice_html(tmp_path):
    p = _placements()
    etapes = split_steps(p)
    out = tmp_path / "notice.html"
    n = write_notice_html(etapes, [""] * len(etapes), str(out), "pave")

    assert n == len(etapes)
    html = out.read_text(encoding="utf-8")
    assert "<!doctype html>" in html.lower()
    assert "pave" in html
    # Chaque etape doit etre nommee dans la page, sinon la notice est inutilisable.
    for i in range(1, len(etapes) + 1):
        assert str(i) in html


def test_total_pieces_coherent_entre_etapes_et_nomenclature():
    """La somme des etapes doit egaler le total de la nomenclature.

    Si ces deux chiffres divergent, l'utilisateur se retrouve avec une notice qui
    ne mene pas au modele annonce.
    """
    from src.bom import build_bom

    p = _placements()
    etapes = split_steps(p)
    assert sum(len(e) for e in etapes) == len(p)
    assert sum(r["quantite"] for r in build_bom(p)) == len(p)
