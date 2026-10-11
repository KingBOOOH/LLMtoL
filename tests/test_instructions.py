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


# --- Regroupement adaptatif (US-02) -----------------------------------------------

def test_petits_modeles_inchanges():
    """En dessous du seuil, le comportement historique ne doit pas bouger."""
    from src.instructions import NOTICE_MAX_ETAPES, PIECES_PAR_ETAPE, pieces_par_etape_pour

    seuil = PIECES_PAR_ETAPE * NOTICE_MAX_ETAPES
    for n in (1, 8, 50, 319, seuil):
        assert pieces_par_etape_pour(n) == PIECES_PAR_ETAPE, f"{n} pieces"


def test_gros_modele_reste_sous_le_plafond():
    """Le cas qui motivait US-02 : 1267 pieces donnaient 159 etapes sans images."""
    from src.instructions import NOTICE_MAX_ETAPES, pieces_par_etape_pour

    for n in (321, 1267, 5000, 50000):
        taille = pieces_par_etape_pour(n)
        n_etapes = -(-n // taille)  # division entiere arrondie au-dessus
        assert n_etapes <= NOTICE_MAX_ETAPES, f"{n} pieces -> {n_etapes} etapes"


def test_split_steps_respecte_le_plafond_sur_un_gros_modele():
    from src.instructions import NOTICE_MAX_ETAPES

    gros = _placements(bbox=(40, 40, 24))
    assert len(gros) > 320, f"le modele de test doit depasser le seuil ({len(gros)})"
    etapes = split_steps(gros)
    assert len(etapes) <= NOTICE_MAX_ETAPES
    assert sum(len(e) for e in etapes) == len(gros), "aucune piece perdue"


def test_choix_explicite_desactive_adaptation():
    """LEGO_PIECES_PAR_ETAPE est une consigne de l'utilisateur, on ne la discute pas."""
    import importlib

    import src.instructions as instr

    os.environ["LEGO_PIECES_PAR_ETAPE"] = "5"
    try:
        importlib.reload(instr)
        assert instr.PIECES_PAR_ETAPE_IMPOSE is True
        assert instr.pieces_par_etape_pour(100000) == 5, "l'adaptation doit etre ignoree"
    finally:
        del os.environ["LEGO_PIECES_PAR_ETAPE"]
        importlib.reload(instr)
    assert instr.PIECES_PAR_ETAPE_IMPOSE is False


def test_ordre_de_montage_tient_aussi_sur_un_gros_modele():
    """La propriete physique ne doit pas se perdre quand les etapes grossissent."""
    etapes = split_steps(_placements(bbox=(40, 40, 24)))
    z_max_precedent = -1
    for etape in etapes:
        assert min(p.z for p in etape) >= z_max_precedent
        z_max_precedent = max(p.z for p in etape)


# --- Constructibilite dans la notice (US-03) ---------------------------------------

def _design_toit_plat():
    """Toit plat au-dessus d'un vide : produit des pieces sans support."""
    return {"name": "creux", "bbox": [10, 10, 6], "ops": [
        {"op": "box", "from": [0, 0, 0], "to": [9, 9, 5], "color": "tan"},
        {"op": "carve", "from": [1, 1, 1], "to": [6, 6, 3]},
    ]}


def test_notice_annonce_un_modele_sain(tmp_path):
    from src.validator import validate

    # Base large + etage plus etroit : connexe, pose au sol, rien qui flotte, et un
    # taux de remplissage sous le seuil — un pave plein ferait 100 % et declencherait
    # a juste titre l'avertissement "quasiment un bloc plein".
    design = {"name": "gradin", "bbox": [8, 8, 6], "ops": [
        {"op": "box", "from": [0, 0, 0], "to": [7, 7, 2], "color": "red"},
        {"op": "box", "from": [2, 2, 3], "to": [5, 5, 5], "color": "blue"},
    ]}
    voxels = compile_design(design)
    p = tile(voxels)
    rapport = validate(voxels, p, design)
    etapes = split_steps(p)

    out = tmp_path / "notice.html"
    write_notice_html(etapes, [""] * len(etapes), str(out), "gradin", rapport=rapport)
    html = out.read_text(encoding="utf-8")
    assert "Avant de commencer" in html
    assert "etat bon" in html, "un modele sain doit le dire, pas afficher un encadre vide"


def test_notice_signale_les_etapes_sans_support(tmp_path):
    from src.validator import validate

    design = _design_toit_plat()
    voxels = compile_design(design)
    p = tile(voxels)
    rapport = validate(voxels, p, design)
    assert rapport["metrics"]["flottantes"], "le modele de test doit avoir des flottantes"

    etapes = split_steps(p)
    out = tmp_path / "notice.html"
    write_notice_html(etapes, [""] * len(etapes), str(out), "creux", rapport=rapport)
    html = out.read_text(encoding="utf-8")

    assert "etat attention" in html
    assert "class='alerte'" in html, "l'etape concernee doit etre signalee"
    # Autant d'alertes que d'etapes contenant au moins une piece sans support.
    sans_support = {tuple(c) for c in rapport["metrics"]["flottantes"]}
    attendu = sum(any((q.x, q.y, q.z) in sans_support for q in e) for e in etapes)
    assert html.count("class='alerte'") == attendu


def test_notice_reste_valide_sans_rapport(tmp_path):
    """L'argument est optionnel : la notice d'avant ne doit pas casser."""
    p = _placements()
    etapes = split_steps(p)
    out = tmp_path / "notice.html"
    write_notice_html(etapes, [""] * len(etapes), str(out), "pave")
    html = out.read_text(encoding="utf-8")
    assert "Avant de commencer" not in html
    assert "<!doctype html>" in html.lower()
