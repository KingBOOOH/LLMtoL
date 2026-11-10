"""Liste d'achat BrickLink (wanted list XML). Deterministe, aucun appel LLM.

Transforme la nomenclature en un fichier que BrickLink sait importer tel quel, pour
passer du modele a l'ecran a un panier reel.

Le format est VERIFIE sur la page d'aide de BrickLink (help.asp?helpID=207) et non
suppose. Deux details s'y jouent, qu'on aurait rates en devinant :

  - le fichier ne doit PAS porter de declaration <?xml ...?> ;
  - seuls ITEMTYPE et ITEMID sont obligatoires, COLOR et MINQTY sont optionnels.

Les correspondances numero LDraw -> numero BrickLink et couleur interne -> couleur
BrickLink viennent de src/catalog.py, ou elles ont ete verifiees une par une. Une
piece sans correspondance verifiee n'est PAS devinee : elle est exclue du XML et
rapportee a part, pour que l'utilisateur sache ce qui manque a sa commande.
"""

import os
import sys
from xml.sax.saxutils import escape

from src.catalog import COULEURS_HORS_PRODUCTION, couleur_bricklink, reference_bricklink

# "P" = Part dans la nomenclature des types d'objets BrickLink (S set, P part, M
# minifig, B book, G gear, C catalog, I instruction, O original box).
ITEMTYPE_PIECE = "P"


def construire_items(rows):
    """Traduit les lignes de nomenclature en (items_achetables, manquants).

    `rows` est la sortie de src.bom.build_bom : chaque ligne porte ldraw_id, couleur
    et quantite. Les deux listes retournees sont disjointes et couvrent tout : rien
    ne disparait en silence.
    """
    items, manquants = [], []
    for r in rows:
        piece = reference_bricklink(r["ldraw_id"])
        couleur = couleur_bricklink(r["couleur"])
        if piece is None or couleur is None:
            manquants.append({
                "ldraw_id": r["ldraw_id"],
                "couleur": r["couleur"],
                "quantite": r["quantite"],
                "raison": ("numero BrickLink non verifie" if piece is None
                           else "couleur BrickLink non verifiee"),
            })
            continue
        items.append({
            "item_id": piece[0],
            "nom": piece[1],
            "couleur_id": couleur[0],
            "couleur_nom": couleur[1],
            "quantite": r["quantite"],
            "ldraw_id": r["ldraw_id"],
            "couleur_interne": r["couleur"],
        })
    return items, manquants


def avertissements_de_production(items):
    """Phrases sur les couleurs qui ne sont plus produites, pour les items presents.

    Le XML reste valide et importable : la couleur existe bien au catalogue. Mais
    elle n'est plus fabriquee, donc l'offre est rare et chere. Le dire est la
    difference entre une liste d'achat et une liste de souhaits.
    """
    phrases = []
    for interne in dict.fromkeys(i["couleur_interne"] for i in items):
        info = COULEURS_HORS_PRODUCTION.get(interne)
        if info is None:
            continue
        code, nom = info["remplacee_par"]
        # Pas de guillemets francais ni d'accents : ce texte part sur stdout, et une
        # sortie redirigee sous Windows est en cp1252.
        phrases.append(
            f"La couleur '{interne}' n'est plus produite depuis "
            f"{info['derniere_annee']} : disponible seulement d'occasion. "
            f"Equivalent actuel : {nom} (couleur BrickLink {code}).")
    return phrases


def construire_xml(items, nom_modele="modele"):
    """Assemble le XML de wanted list. Pas de declaration <?xml ?> : BrickLink
    l'interdit explicitement."""
    lignes = ["<INVENTORY>"]
    for it in items:
        # REMARKS est prive et sert de trace : il rappelle de quel modele vient la
        # ligne, et sous quel numero LDraw la piece y figure — c'est la seule facon
        # de retrouver l'ecart 3023b/3023 une fois le fichier sorti du projet.
        remarque = escape(f"{nom_modele} / LDraw {it['ldraw_id']}")
        lignes += [
            "<ITEM>",
            f"<ITEMTYPE>{ITEMTYPE_PIECE}</ITEMTYPE>",
            f"<ITEMID>{escape(str(it['item_id']))}</ITEMID>",
            f"<COLOR>{int(it['couleur_id'])}</COLOR>",
            f"<MINQTY>{int(it['quantite'])}</MINQTY>",
            f"<REMARKS>{remarque}</REMARKS>",
            "</ITEM>",
        ]
    lignes.append("</INVENTORY>")
    return "\n".join(lignes) + "\n"


def write_wanted_list(rows, path, nom_modele="modele"):
    """Ecrit le XML et retourne (items, manquants, avertissements).

    Le fichier est ecrit meme quand des pieces manquent : une liste partielle
    accompagnee de ce qui lui manque est utile, une absence de fichier ne l'est pas.
    """
    items, manquants = construire_items(rows)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(construire_xml(items, nom_modele))
    return items, manquants, avertissements_de_production(items)


if __name__ == "__main__":
    import json

    from src.bom import build_bom
    from src.compiler import compile_design
    from src.tiler import tile

    if len(sys.argv) < 2:
        print("Usage: python -m src.wanted_list design.json [sortie.xml]")
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        design = json.load(f)
    sortie = (sys.argv[2] if len(sys.argv) > 2
              else os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])),
                                "wanted_list.xml"))

    lignes_bom = build_bom(tile(compile_design(design)))
    achetables, absents, avertis = write_wanted_list(
        lignes_bom, sortie, design.get("name", "modele"))

    total = sum(i["quantite"] for i in achetables)
    print(f"{total} pieces sur {len(achetables)} references -> {sortie}")
    for phrase in avertis:
        print(f"  ATTENTION : {phrase}")
    for m in absents:
        print(f"  NON COMMANDABLE : {m['quantite']}x {m['ldraw_id']} "
              f"({m['couleur']}) — {m['raison']}")
