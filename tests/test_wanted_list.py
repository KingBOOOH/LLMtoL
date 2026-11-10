"""Liste d'achat BrickLink : correspondances verifiees et format du fichier.

L'enjeu de ces tests n'est pas le XML, qui est trivial, mais la CORRESPONDANCE entre
deux numerotations externes qui se ressemblent sans etre identiques. Le projet s'etait
abstenu de la deviner ; elle est maintenant verifiee une par une contre BrickLink, et
ces tests sont ce qui empeche quelqu'un de la "simplifier" plus tard en supposant que
les deux numeros sont toujours egaux.
"""

import xml.etree.ElementTree as ET

import pytest

from src.bom import build_bom
from src.catalog import (
    BRICKLINK_COLORS,
    COULEURS_HORS_PRODUCTION,
    PALETTE,
    PARTS,
    Part,
    couleur_bricklink,
    reference_bricklink,
    verify_bricklink,
    verify_catalog,
)
from src.compiler import compile_design
from src.tiler import tile
from src.wanted_list import (
    avertissements_de_production,
    construire_items,
    construire_xml,
    write_wanted_list,
)

# Les trois seuls ecarts reels, releves un par un sur bricklink.com le 2026-10-12.
# Ce sont EUX qui justifient de ne pas deduire le numero de boutique du numero LDraw.
ECARTS_CONNUS = {
    "3023b": "3023",   # LDraw a renomme la piece (mise a jour 2024-07), BrickLink non
    "3070b": "3070",   # BrickLink liste 3070b en numero ALTERNE
    "3069b": "3069",   # idem
    # Celui-ci a ete trouve PAR ce test, en ajoutant les pentes (US V2-05) : 3040b
    # n'existe pas du tout chez BrickLink, pas meme en numero alterne. Correspondance
    # etablie par le nom et les dimensions (BrickLink 3040 "Slope 45 2 x 1", 2 x 1 x 1).
    "3040b": "3040",
}


# --- les correspondances sont completes et explicites -----------------------------

def test_chaque_piece_du_catalogue_a_une_correspondance_declaree():
    assert verify_bricklink() is True


def test_une_piece_ajoutee_sans_correspondance_fait_echouer_le_controle():
    """C'est tout l'interet du controle : elargir le catalogue (US V2-05) sans se
    poser la question du numero de boutique doit casser, pas produire une liste
    d'achat trouee en silence."""
    from src import catalog

    sauve = catalog.PARTS
    catalog.PARTS = [*sauve, Part("9999", "brick", 1, 1, 3)]
    try:
        with pytest.raises(ValueError, match="9999"):
            catalog.verify_bricklink()
    finally:
        catalog.PARTS = sauve


def test_chaque_couleur_de_la_palette_a_un_identifiant_bricklink():
    manquantes = [nom for nom in PALETTE if nom not in BRICKLINK_COLORS]
    assert not manquantes, f"couleurs sans identifiant BrickLink : {manquantes}"


def test_aucune_couleur_bricklink_inventee():
    """L'inverse compte aussi : une entree qui ne correspond a aucune couleur du DSL
    serait du code mort qu'on finirait par croire utilise."""
    en_trop = [nom for nom in BRICKLINK_COLORS if nom not in PALETTE]
    assert not en_trop, f"couleurs inconnues du DSL : {en_trop}"


# --- les ecarts entre les deux numerotations sont verrouilles ---------------------

def test_les_trois_ecarts_connus_sont_preserves():
    """Si quelqu'un "simplifie" en posant numero BrickLink = numero LDraw, ces trois
    lignes cassent — et la liste d'achat enverrait commander des pieces inexistantes.
    Verifie : 3023b, 3070b et 3069b renvoient tous les trois "not found" sur
    bricklink.com."""
    for ldraw_id, attendu in ECARTS_CONNUS.items():
        numero, _nom = reference_bricklink(ldraw_id)
        assert numero == attendu, f"{ldraw_id} doit valoir {attendu} chez BrickLink"


def test_les_autres_pieces_portent_le_meme_numero():
    """Le pendant du test precedent : partout ailleurs les deux numeros coincident,
    et une divergence nouvelle doit se voir."""
    for part in PARTS:
        if part.ldraw_id in ECARTS_CONNUS:
            continue
        numero, _ = reference_bricklink(part.ldraw_id)
        assert numero == part.ldraw_id, f"{part.ldraw_id} a change de numero"


def test_les_deux_numerotations_de_couleurs_ne_sont_pas_interchangeables():
    """Preuve chiffree qu'il fallait deux tables : sur les MEMES nombres, les deux
    systemes designent des couleurs differentes."""
    assert PALETTE["dark_gray"]["ldraw_code"] == 8
    assert BRICKLINK_COLORS["brown"][0] == 8
    assert PALETTE["reddish_brown"]["ldraw_code"] == 70
    assert BRICKLINK_COLORS["reddish_brown"][0] == 88


def test_une_piece_inconnue_ne_renvoie_rien_plutot_qu_une_devinette():
    assert reference_bricklink("0000") is None
    assert couleur_bricklink("mauve") is None


# --- le catalogue refuse les references depreciees --------------------------------

def test_le_catalogue_ne_contient_aucune_reference_depreciee():
    """verify_catalog ne testait que la PRESENCE du fichier, et laissait donc passer
    3023 une fois la piece renommee 3023b par la mise a jour LDraw 2024-07. Un stub
    "~Moved to" fonctionne encore par redirection, mais peut disparaitre."""
    from src.catalog import _find_ldraw_library

    if _find_ldraw_library() is None:
        pytest.skip("bibliotheque LDraw absente")
    assert verify_catalog() is True


def test_un_stub_moved_to_est_rejete():
    """Verifie le controle lui-meme sur le vrai fichier 3023.dat, qui EST un stub."""
    from src import catalog

    if catalog._find_ldraw_library() is None:
        pytest.skip("bibliotheque LDraw absente")
    sauve = catalog.PARTS
    catalog.PARTS = [Part("3023", "plate", 2, 1, 1)]
    try:
        with pytest.raises(ValueError, match="Moved to 3023b"):
            catalog.verify_catalog()
    finally:
        catalog.PARTS = sauve


# --- format du fichier -----------------------------------------------------------

DESIGN = {
    "name": "essai",
    "bbox": [6, 4, 3],
    "ops": [
        {"op": "box", "from": [0, 0, 0], "to": [5, 3, 0], "color": "red"},
        {"op": "box", "from": [0, 0, 1], "to": [5, 3, 2], "color": "white"},
    ],
}


def _lignes_de(design):
    return build_bom(tile(compile_design(design)))


def test_le_xml_ne_porte_pas_de_declaration():
    """BrickLink l'interdit explicitement (help.asp?helpID=207). C'est exactement le
    genre de detail qu'on aurait mis par reflexe."""
    xml = construire_xml(*[construire_items(_lignes_de(DESIGN))[0]])
    assert not xml.lstrip().startswith("<?xml")


def test_le_xml_est_bien_forme_et_porte_les_champs_obligatoires():
    items, _ = construire_items(_lignes_de(DESIGN))
    racine = ET.fromstring(construire_xml(items, "essai"))
    assert racine.tag == "INVENTORY"
    balises = racine.findall("ITEM")
    assert len(balises) == len(items) > 0
    for balise in balises:
        # ITEMTYPE et ITEMID sont les deux seuls obligatoires : les omettre provoque
        # une erreur a l'import.
        assert balise.findtext("ITEMTYPE") == "P"
        assert balise.findtext("ITEMID")
        assert int(balise.findtext("MINQTY")) > 0


def test_les_quantites_du_xml_sont_celles_de_la_nomenclature():
    """Une liste d'achat qui ne demande pas le bon nombre de pieces est pire
    qu'inutile."""
    lignes = _lignes_de(DESIGN)
    items, manquants = construire_items(lignes)
    assert manquants == []
    racine = ET.fromstring(construire_xml(items, "essai"))
    total_xml = sum(int(b.findtext("MINQTY")) for b in racine.findall("ITEM"))
    assert total_xml == sum(r["quantite"] for r in lignes)


def test_une_piece_sans_correspondance_est_signalee_et_non_devinee():
    """La regle du projet : jamais de numero invente. La ligne sort du XML et part
    dans la liste des manquants, ou l'utilisateur la verra."""
    lignes = [{"ldraw_id": "9999", "couleur": "red", "quantite": 3},
              {"ldraw_id": "3001", "couleur": "red", "quantite": 2}]
    items, manquants = construire_items(lignes)
    assert [i["item_id"] for i in items] == ["3001"]
    assert len(manquants) == 1
    assert manquants[0]["ldraw_id"] == "9999"
    assert manquants[0]["quantite"] == 3
    assert "non verifie" in manquants[0]["raison"]


def test_une_couleur_sans_correspondance_est_signalee_aussi():
    lignes = [{"ldraw_id": "3001", "couleur": "mauve", "quantite": 1}]
    items, manquants = construire_items(lignes)
    assert items == []
    assert "couleur" in manquants[0]["raison"]


# --- couleurs hors production ----------------------------------------------------

def test_les_gris_hors_production_sont_annonces():
    """Ces deux couleurs existent au catalogue BrickLink mais ne sont plus
    fabriquees : l'offre est rare et chere. Le dire est la difference entre une liste
    d'achat et une liste de souhaits."""
    items, _ = construire_items(
        [{"ldraw_id": "3001", "couleur": "light_gray", "quantite": 4}])
    phrases = avertissements_de_production(items)
    assert len(phrases) == 1
    assert "2007" in phrases[0] and "Light Bluish Gray" in phrases[0]


def test_aucun_avertissement_sur_une_couleur_courante():
    items, _ = construire_items(
        [{"ldraw_id": "3001", "couleur": "red", "quantite": 4}])
    assert avertissements_de_production(items) == []


def test_les_avertissements_restent_lisibles_en_cp1252():
    """Ces phrases partent sur stdout, et une sortie redirigee sous Windows est en
    cp1252 : un guillemet francais ou un accent y casse l'affichage."""
    items, _ = construire_items(
        [{"ldraw_id": "3001", "couleur": c, "quantite": 1}
         for c in COULEURS_HORS_PRODUCTION])
    for phrase in avertissements_de_production(items):
        phrase.encode("cp1252")  # leve UnicodeEncodeError si un caractere passe mal


# --- ecriture sur disque ---------------------------------------------------------

def test_le_fichier_est_ecrit_meme_avec_des_pieces_manquantes(tmp_path):
    """Une liste partielle accompagnee de ce qui lui manque est utile ; une absence
    de fichier ne l'est pas."""
    chemin = tmp_path / "wanted_list.xml"
    lignes = [{"ldraw_id": "9999", "couleur": "red", "quantite": 1},
              {"ldraw_id": "3020", "couleur": "blue", "quantite": 7}]
    items, manquants, _ = write_wanted_list(lignes, str(chemin), "essai")
    assert chemin.is_file()
    assert len(items) == 1 and len(manquants) == 1
    racine = ET.fromstring(chemin.read_text(encoding="utf-8"))
    (balise,) = racine.findall("ITEM")
    assert balise.findtext("ITEMID") == "3020"
    assert balise.findtext("COLOR") == "7"
    assert balise.findtext("MINQTY") == "7"


def test_la_remarque_garde_la_trace_du_numero_ldraw(tmp_path):
    """Sans elle, impossible de retrouver l'ecart 3023b/3023 une fois le fichier
    sorti du projet."""
    chemin = tmp_path / "wl.xml"
    write_wanted_list(
        [{"ldraw_id": "3023b", "couleur": "white", "quantite": 2}], str(chemin), "tour")
    racine = ET.fromstring(chemin.read_text(encoding="utf-8"))
    (balise,) = racine.findall("ITEM")
    assert balise.findtext("ITEMID") == "3023"
    assert "3023b" in balise.findtext("REMARKS")
    assert "tour" in balise.findtext("REMARKS")


def test_les_caracteres_speciaux_du_nom_sont_echappes(tmp_path):
    """Le nom du modele vient du LLM : il peut contenir n'importe quoi, et un & brut
    rendrait le fichier illisible par BrickLink."""
    chemin = tmp_path / "wl.xml"
    write_wanted_list([{"ldraw_id": "3001", "couleur": "red", "quantite": 1}],
                      str(chemin), 'bateau & <voile>')
    racine = ET.fromstring(chemin.read_text(encoding="utf-8"))  # leve si mal echappe
    assert "bateau & <voile>" in racine.find("ITEM").findtext("REMARKS")


def test_un_modele_reel_produit_une_liste_entierement_commandable(tmp_path):
    """Bout en bout sur un design complet : aucune piece ne doit rester hors
    commande, sinon la correspondance a un trou."""
    chemin = tmp_path / "wl.xml"
    items, manquants, _ = write_wanted_list(_lignes_de(DESIGN), str(chemin), "essai")
    assert manquants == [], f"pieces hors commande : {manquants}"
    assert sum(i["quantite"] for i in items) > 0
