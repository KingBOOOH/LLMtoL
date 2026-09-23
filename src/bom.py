"""Nomenclature des pieces (Phase 5, tache 5.1). Deterministe, aucun appel LLM.

Compte les pieces d'un modele par (reference LDraw, couleur) et produit bom.csv et
bom.html.

Volontairement SANS correspondance BrickLink/Rebrickable : pour les pieces classiques du
catalogue, la reference LDraw coincide le plus souvent avec l'identifiant de conception
LEGO utilise par ces sites, mais "le plus souvent" ne suffit pas — le projet s'interdit de
deviner une convention externe non verifiee. Tout ce qui figure ici provient de
src.catalog, donc de la bibliotheque LDraw reellement installee.
"""

import csv
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.catalog import PALETTE

KIND_LABELS = {"brick": "brique", "plate": "plate", "tile": "tile"}


def build_bom(placements):
    """Agrege les placements en lignes de nomenclature, triees par quantite decroissante.

    Chaque ligne : reference LDraw, type, dimensions, couleur (nom interne, nom et code
    LDraw) et quantite.
    """
    counts = Counter()
    parts_by_id = {}
    for p in placements:
        counts[(p.part.ldraw_id, p.color_index)] += 1
        parts_by_id[p.part.ldraw_id] = p.part

    # index couleur -> nom : meme ordre que src.compiler.COLOR_INDEX (0 = vide)
    names = list(PALETTE.keys())

    rows = []
    for (ldraw_id, color_index), quantity in counts.most_common():
        part = parts_by_id[ldraw_id]
        color_name = names[color_index - 1] if 1 <= color_index <= len(names) else "?"
        entry = PALETTE.get(color_name, {})
        rows.append({
            "ldraw_id": ldraw_id,
            "type": KIND_LABELS.get(part.kind, part.kind),
            "dimensions": f"{part.w}x{part.d}",
            "hauteur_plates": part.h,
            "couleur": color_name,
            "couleur_ldraw": entry.get("ldraw_name", "?"),
            "code_ldraw": entry.get("ldraw_code", ""),
            "quantite": quantity,
            # conserve pour retrouver la vignette ; retire du CSV (voir FIELDNAMES)
            "_color_index": color_index,
        })
    return rows


FIELDNAMES = ["ldraw_id", "type", "dimensions", "hauteur_plates",
              "couleur", "couleur_ldraw", "code_ldraw", "quantite"]


def write_csv(rows, path):
    """Ecrit la nomenclature en CSV (les cles internes prefixees _ sont ignorees)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_html(rows, path, model_name="modele", piece_images=None):
    """Ecrit la nomenclature en HTML, avec une vignette et une pastille par ligne.

    piece_images : {(ldraw_id, index_couleur): chemin_image}. Les vignettes sont celles
    deja rendues pour la notice — aucun rendu supplementaire. Une ligne sans image
    reste lisible, la colonne affiche simplement un tiret.
    """
    total = sum(r["quantite"] for r in rows)
    base = os.path.dirname(os.path.abspath(path))
    piece_images = piece_images or {}

    lignes = []
    for r in rows:
        rgb = PALETTE.get(r["couleur"], {}).get("rgb", (0.5, 0.5, 0.5, 1.0))
        hexa = "#%02x%02x%02x" % tuple(int(c * 255) for c in rgb[:3])

        image = piece_images.get((r["ldraw_id"], r["_color_index"]))
        if image and os.path.isfile(image):
            rel = os.path.relpath(image, base).replace(os.sep, "/")
            vignette = f"<img src='{rel}' alt='{r['ldraw_id']}'>"
        else:
            vignette = "<span class='sans'>—</span>"

        lignes.append(
            f"<tr><td class='img'>{vignette}</td>"
            f"<td class='q'>{r['quantite']}x</td>"
            f"<td><code>{r['ldraw_id']}</code></td>"
            f"<td>{r['type']} {r['dimensions']}</td>"
            f"<td><span class='pastille' style='background:{hexa}'></span>"
            f"{r['couleur_ldraw']} <small>({r['code_ldraw']})</small></td></tr>"
        )

    html = f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<title>Nomenclature — {model_name}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #222; }}
 table {{ border-collapse: collapse; margin-top: 1rem; }}
 th, td {{ padding: .4rem .8rem; border-bottom: 1px solid #ddd; text-align: left; }}
 th {{ background: #f4f4f4; }}
 .q {{ font-weight: 700; text-align: right; white-space: nowrap; }}
 .pastille {{ display: inline-block; width: 1rem; height: 1rem; border-radius: 3px;
              border: 1px solid #0003; vertical-align: -2px; margin-right: .5rem; }}
 code {{ background: #f4f4f4; padding: .1rem .3rem; border-radius: 3px; }}
 .img {{ width: 92px; padding: .2rem; }}
 .img img {{ width: 88px; height: 88px; object-fit: contain; background: #2b2d33;
             border-radius: 6px; display: block; }}
 .sans {{ color: #bbb; }}
</style></head><body>
<h1>Nomenclature — {model_name}</h1>
<p><strong>{total}</strong> pieces au total, <strong>{len(rows)}</strong> references distinctes.</p>
<table><tr><th></th><th>Qte</th><th>Ref. LDraw</th><th>Piece</th><th>Couleur</th></tr>
{chr(10).join(lignes)}
</table></body></html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def write_bom(placements, work_dir, model_name="modele", piece_images=None):
    """Ecrit bom.csv et bom.html dans work_dir. Retourne les lignes de nomenclature.

    piece_images est optionnel : la nomenclature est ecrite une premiere fois des la
    generation (sans vignettes, les rendus n'existent pas encore), puis reecrite apres
    le rendu pour y inclure les images.
    """
    rows = build_bom(placements)
    write_csv(rows, os.path.join(work_dir, "bom.csv"))
    write_html(rows, os.path.join(work_dir, "bom.html"), model_name, piece_images)
    return rows


if __name__ == "__main__":
    import json

    from src.compiler import compile_design
    from src.tiler import tile

    if len(sys.argv) < 2:
        print("Usage: python -m src.bom design.json [dossier_sortie]")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        design_data = json.load(f)
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(sys.argv[1]))

    placements_data = tile(compile_design(design_data))
    bom_rows = write_bom(placements_data, out_dir, design_data.get("name", "modele"))

    print(f"{sum(r['quantite'] for r in bom_rows)} pieces, "
          f"{len(bom_rows)} references -> {out_dir}")
    for row in bom_rows[:12]:
        print(f"  {row['quantite']:>4}x  {row['ldraw_id']:>6}  {row['type']} "
              f"{row['dimensions']:<5} {row['couleur_ldraw']}")
