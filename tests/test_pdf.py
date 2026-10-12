"""Notice PDF (src/pdf.py).

Le gros du test porte sur `construire_html`, qui est pur : il ne demande ni navigateur
ni rendu Blender. La conversion reelle en PDF est testee une fois, et sautee si aucun
navigateur n'est installe — la suite doit rester verte sur une machine nue.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.bom import build_bom
from src.compiler import compile_design
from src.instructions import split_steps
from src.pdf import construire_html, html_vers_pdf, trouver_navigateur, write_pdf
from src.tiler import tile
from src.validator import validate

DESIGN = {"name": "maison", "bbox": [10, 10, 6], "ops": [
    {"op": "box", "from": [0, 0, 0], "to": [9, 9, 5], "color": "tan"},
    {"op": "carve", "from": [1, 1, 1], "to": [6, 6, 3]},
]}


@pytest.fixture
def modele():
    voxels = compile_design(DESIGN)
    placements = tile(voxels)
    return {"placements": placements, "etapes": split_steps(placements),
            "rapport": validate(voxels, placements, DESIGN)}


def _html(modele, tmp_path, **kw):
    return construire_html(modele["etapes"], [""] * len(modele["etapes"]),
                           modele["placements"], str(tmp_path), "maison", **kw)


def test_les_quatre_sections_dans_l_ordre_demande(modele, tmp_path):
    """Couverture, A propos, etapes, puis liste des pieces — l'ordre est la demande."""
    html = _html(modele, tmp_path, prompt="Une maison creuse.")
    positions = [html.index(m) for m in
                 ("couverture", "A propos", "Montage", "Liste des pieces")]
    assert positions == sorted(positions), "sections dans le desordre"


def test_couverture_porte_les_informations_demandees(modele, tmp_path):
    html = _html(modele, tmp_path, prompt="Une maison creuse.",
                 identifiant="abc-123", date="11 octobre 2026")
    debut = html[:html.index("A propos")]
    assert "maison" in debut
    assert "Une maison creuse." in debut
    assert "abc-123" in debut
    assert "11 octobre 2026" in debut
    assert str(len(modele["placements"])) in debut, "le total de pieces doit figurer"


def test_totaux_coherents(modele, tmp_path):
    """Couverture, somme des etapes et nomenclature doivent concorder.

    Si ces trois chiffres divergent, l'utilisateur suit une notice qui ne mene pas au
    modele annonce.
    """
    total = len(modele["placements"])
    assert sum(len(e) for e in modele["etapes"]) == total
    assert sum(r["quantite"] for r in build_bom(modele["placements"])) == total

    html = _html(modele, tmp_path)
    assert f"<strong>{total}</strong> pieces" in html
    assert f"{total} pieces," in html, "le total doit aussi figurer dans la nomenclature"


def test_aucune_etape_coupee_entre_deux_pages(modele, tmp_path):
    """Une etape a cheval sur deux pages est inutilisable au montage."""
    html = _html(modele, tmp_path)
    regle = html[html.index(".etape {"):html.index(".page {")]
    assert "page-break-inside: avoid" in regle


def test_toutes_les_etapes_figurent(modele, tmp_path):
    html = _html(modele, tmp_path)
    for i in range(1, len(modele["etapes"]) + 1):
        assert f"Etape {i} " in html


def test_images_absentes_ne_laissent_pas_de_balise_cassee(modele, tmp_path):
    """Aucun rendu n'existe ici : il ne doit y avoir aucune <img> pointant dans le vide."""
    html = _html(modele, tmp_path, rendu_final=str(tmp_path / "inexistant.png"))
    assert "<img" not in html


def test_image_presente_est_referencee_en_relatif(modele, tmp_path):
    rendu = tmp_path / "final.png"
    rendu.write_bytes(b"\x89PNG\r\n\x1a\n")
    html = _html(modele, tmp_path, rendu_final=str(rendu))
    assert "<img src='final.png'" in html, "chemin relatif attendu, pas absolu"


def test_etat_de_constructibilite_present(modele, tmp_path):
    """Meme texte que la notice HTML et l'interface web (US-03)."""
    assert modele["rapport"]["metrics"]["flottantes"], "le modele de test doit en avoir"
    html = _html(modele, tmp_path, rapport=modele["rapport"])
    assert "Avant de commencer" in html
    assert "etat attention" in html


def test_sans_rapport_pas_d_encadre(modele, tmp_path):
    assert "Avant de commencer" not in _html(modele, tmp_path)


def test_a_propos_parle_du_projet(modele, tmp_path):
    html = _html(modele, tmp_path)
    apropos = html[html.index("A propos"):html.index("Montage")]
    assert "LLMtoL" in apropos
    assert "LDraw" in apropos


def test_html_vers_pdf_echoue_clairement_sans_navigateur(tmp_path, monkeypatch):
    """L'appelant doit pouvoir distinguer ce cas, pas recevoir un PDF vide."""
    from src import pdf

    monkeypatch.setattr(pdf.config, "NAVIGATEURS_PDF", [])
    monkeypatch.setattr(pdf.shutil, "which", lambda _: None)
    source = tmp_path / "x.html"
    source.write_text("<p>x</p>", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Aucun navigateur"):
        html_vers_pdf(str(source), str(tmp_path / "x.pdf"))


@pytest.mark.skipif(trouver_navigateur() is None,
                    reason="aucun navigateur installe pour produire un PDF")
def test_pdf_reellement_produit(modele, tmp_path):
    chemin = write_pdf(modele["etapes"], [""] * len(modele["etapes"]),
                       modele["placements"], str(tmp_path), "maison",
                       prompt="Une maison creuse.", rapport=modele["rapport"])
    donnees = Path(chemin).read_bytes()
    assert donnees.startswith(b"%PDF"), "ce n'est pas un PDF"
    assert donnees.rstrip().endswith(b"%%EOF"), "PDF tronque"
    assert len(donnees) > 2000
    # Le HTML intermediaire est conserve : il explique un PDF rate sans tout rejouer.
    assert os.path.isfile(tmp_path / "notice_pdf.html")
