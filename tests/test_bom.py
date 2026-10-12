"""Nomenclature (src/bom.py).

Module non teste jusqu'ici, alors que US-03 et US-10 vont s'appuyer dessus. Le point
le plus fragile est la cle interne `_color_index`, ajoutee aux lignes pour retrouver la
vignette : elle ne doit JAMAIS fuir dans le CSV, qui est un livrable.
"""

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.bom import FIELDNAMES, build_bom, write_bom
from src.compiler import compile_design
from src.tiler import tile


def _placements(couleur="red", bbox=(8, 8, 6)):
    design = {"name": "pave", "bbox": list(bbox),
              "ops": [{"op": "box", "from": [0, 0, 0],
                       "to": [bbox[0] - 1, bbox[1] - 1, bbox[2] - 1], "color": couleur}]}
    return tile(compile_design(design))


def test_total_egale_le_nombre_de_placements():
    p = _placements()
    assert sum(r["quantite"] for r in build_bom(p)) == len(p)


def test_agregation_par_reference_et_couleur():
    """Deux couleurs de la meme reference font deux lignes, pas une."""
    bicolore = {"name": "t", "bbox": [8, 8, 2], "ops": [
        {"op": "box", "from": [0, 0, 0], "to": [7, 7, 0], "color": "red"},
        {"op": "box", "from": [0, 0, 1], "to": [7, 7, 1], "color": "blue"},
    ]}
    rows = build_bom(tile(compile_design(bicolore)))
    couleurs = {r["couleur"] for r in rows}
    assert {"red", "blue"} <= couleurs
    for r in rows:
        assert r["quantite"] >= 1
        assert r["ldraw_id"]


def test_lignes_triees_par_quantite_decroissante():
    rows = build_bom(_placements())
    quantites = [r["quantite"] for r in rows]
    assert quantites == sorted(quantites, reverse=True)


def test_csv_ne_fuit_pas_les_cles_internes(tmp_path):
    """`_color_index` sert a retrouver la vignette, il n'a rien a faire dans le CSV."""
    rows = build_bom(_placements())
    assert any(k.startswith("_") for k in rows[0]), "le test ne verifie rien si la cle a disparu"

    write_bom(_placements(), str(tmp_path), "pave")
    with open(tmp_path / "bom.csv", newline="", encoding="utf-8") as f:
        lecteur = csv.DictReader(f)
        assert lecteur.fieldnames == FIELDNAMES
        assert not any(c.startswith("_") for c in lecteur.fieldnames)
        lignes = list(lecteur)
    assert sum(int(l["quantite"]) for l in lignes) == len(_placements())


def test_html_lisible_sans_vignettes(tmp_path):
    """Les vignettes n'existent pas a la premiere ecriture : la page doit tenir."""
    write_bom(_placements(), str(tmp_path), "pave")
    html = (tmp_path / "bom.html").read_text(encoding="utf-8")
    assert "<!doctype html>" in html.lower()
    assert "pave" in html
    assert "<img" not in html, "aucune image ne doit etre referencee sans vignette"


def test_html_avec_vignettes_manquantes_sur_disque(tmp_path):
    """Un chemin de vignette qui n'existe pas ne doit pas casser la page."""
    p = _placements()
    rows = build_bom(p)
    images = {(r["ldraw_id"], r["_color_index"]): str(tmp_path / "inexistant.png")
              for r in rows}
    write_bom(p, str(tmp_path), "pave", piece_images=images)
    html = (tmp_path / "bom.html").read_text(encoding="utf-8")
    assert "<img" not in html
    assert "pave" in html


def test_html_avec_vignettes_reelles(tmp_path):
    p = _placements()
    rows = build_bom(p)
    vignette = tmp_path / "v.png"
    vignette.write_bytes(b"\x89PNG\r\n\x1a\n")  # contenu sans importance, seul l'acces compte
    images = {(r["ldraw_id"], r["_color_index"]): str(vignette) for r in rows}
    write_bom(p, str(tmp_path), "pave", piece_images=images)
    html = (tmp_path / "bom.html").read_text(encoding="utf-8")
    assert html.count("<img") == len(rows)


def test_modele_vide():
    assert build_bom([]) == []
