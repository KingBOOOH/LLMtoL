"""Notice de montage (Phase 5, tache 5.3). Deterministe, aucun appel LLM.

Decoupe le modele en etapes de montage, ecrit un .ldr CUMULATIF par etape (etat du modele
a la fin de l'etape), puis assemble une notice HTML une fois les etapes rendues.

Choix du generateur maison plutot que LPub3D (tache 5.2) : LPub3D n'est pas installe sur
cette machine, et l'evaluer supposerait d'ajouter une dependance externe lourde pour un
besoin que la machinerie existante couvre deja — on sait deja ecrire un .ldr et rendre un
lot en un seul processus Blender.

Decoupage : par couche z croissante, car c'est l'ordre de montage reel (on ne pose pas une
piece sous une piece deja posee). Les couches trop maigres sont fusionnees et les couches
trop denses scindees, pour viser un nombre de pieces lisible par etape.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.ldraw_writer import write_ldr

PIECES_PAR_ETAPE = int(os.environ.get("LEGO_PIECES_PAR_ETAPE", "8"))


def split_steps(placements, pieces_par_etape=None):
    """Repartit les placements en etapes d'un nombre FIXE de pieces.

    Decoupage par quantite et non par couche z : une couche peut contenir une seule
    piece comme cinquante, ce qui donnait des etapes tres inegales et faisait perdre
    le fil. A nombre constant, chaque etape demande le meme effort.

    L'ordre de montage reste physiquement valide : les placements sont tries par z
    croissant d'abord (on ne pose pas une piece sous une piece deja posee), puis par
    position dans la couche pour que la progression soit lisible d'une etape a l'autre.
    """
    taille = pieces_par_etape or PIECES_PAR_ETAPE
    ordonnes = sorted(placements, key=lambda p: (p.z, p.y, p.x))
    return [ordonnes[i:i + taille] for i in range(0, len(ordonnes), taille)]


def piece_key(placement):
    """Identifie un type de piece a afficher dans l'encart : reference + couleur."""
    return (placement.part.ldraw_id, placement.color_index)


def write_piece_models(etapes, pieces_dir):
    """Ecrit un .ldr d'UNE seule piece par type utilise, pour l'encart des etapes.

    Une piece par (reference, couleur), pas par etape : un meme type revenant dans
    plusieurs etapes n'est rendu qu'une fois. Retourne {cle_piece: dossier}.
    """
    os.makedirs(pieces_dir, exist_ok=True)
    modeles = {}
    for etape in etapes:
        for p in etape:
            cle = piece_key(p)
            if cle in modeles:
                continue
            dossier = os.path.join(pieces_dir, f"{cle[0]}_c{cle[1]}")
            os.makedirs(dossier, exist_ok=True)
            # Place la piece a l'origine, sans rotation : l'encart montre le type de
            # piece, pas son orientation dans le modele.
            seule = p._replace(x=0, y=0, z=0, rotated=False)
            write_ldr([seule], os.path.join(dossier, "model.ldr"), f"piece_{cle[0]}")
            modeles[cle] = dossier
    return modeles


def compose_step_image(step_render, piece_images, out_path, largeur_encart=0.16):
    """Compose l'image finale d'une etape : assemblage + encart des pieces en haut a droite.

    piece_images : liste de (chemin_image, quantite). L'encart est un bandeau blanc
    arrondi pose sur le coin superieur droit, chaque piece y figurant en grand avec sa
    quantite. Retourne False si le rendu de l'etape manque.
    """
    from PIL import Image, ImageDraw, ImageFont

    if not os.path.isfile(step_render):
        return False

    base = Image.open(step_render).convert("RGB")
    W, H = base.size

    vignettes = [(p, n) for p, n in piece_images if os.path.isfile(p)]
    if not vignettes:
        base.save(out_path)
        return True

    # Grille au plus proche du carre : evite une colonne unique tres haute comme une
    # ligne unique tres large. L'encart est plafonne au TIERS de la largeur : au-dela
    # il recouvrait l'assemblage dans les dernieres etapes, quand le modele est grand.
    n = len(vignettes)
    colonnes = min(n, 3 if n <= 3 else math.ceil(math.sqrt(n)))
    lignes = math.ceil(n / colonnes)
    cell = min(int(W * largeur_encart), int(W * 0.34) // colonnes)
    box_w, box_h = cell * colonnes, cell * lignes
    marge = int(W * 0.02)

    encart = Image.new("RGB", (box_w, box_h), (255, 255, 255))
    dessin = ImageDraw.Draw(encart)
    try:
        police = ImageFont.truetype("arialbd.ttf", max(14, cell // 5))
    except OSError:
        police = ImageFont.load_default()

    for i, (chemin, quantite) in enumerate(vignettes):
        ligne, col = divmod(i, colonnes)
        # Derniere ligne incomplete : on centre ses elements plutot que de laisser un
        # bloc blanc vide dans un coin, qui se lit comme une piece manquante.
        dans_ligne = min(colonnes, n - ligne * colonnes)
        decalage = (box_w - dans_ligne * cell) // 2
        cx = decalage + col * cell
        cy = ligne * cell

        vign = Image.open(chemin).convert("RGB").resize((cell, cell), Image.LANCZOS)
        encart.paste(vign, (cx, cy))

        texte = f"{quantite}x"
        # Fond sombre derriere le texte : sur une piece claire, un libelle blanc seul
        # serait illisible.
        tx, ty = cx + int(cell * 0.06), cy + int(cell * 0.72)
        bbox = dessin.textbbox((tx, ty), texte, font=police)
        dessin.rectangle([bbox[0] - 6, bbox[1] - 4, bbox[2] + 6, bbox[3] + 4],
                         fill=(30, 30, 34))
        dessin.text((tx, ty), texte, fill=(255, 255, 255), font=police)

    dessin.rectangle([0, 0, box_w - 1, box_h - 1], outline=(180, 180, 180), width=3)
    base.paste(encart, (W - box_w - marge, marge))
    base.save(out_path)
    return True


def compose_all_steps(etapes, etape_dirs, piece_models, vue="iso"):
    """Compose l'image de chaque etape. Retourne le nombre d'images produites."""
    faites = 0
    for etape, dossier in zip(etapes, etape_dirs):
        if not dossier:
            continue
        comptes = {}
        for p in etape:
            cle = piece_key(p)
            comptes[cle] = comptes.get(cle, 0) + 1

        vignettes = [(os.path.join(piece_models[cle], f"render_{vue}.png"), n)
                     for cle, n in sorted(comptes.items(), key=lambda kv: -kv[1])
                     if cle in piece_models]

        if compose_step_image(os.path.join(dossier, f"render_{vue}.png"),
                              vignettes, os.path.join(dossier, "etape.png")):
            faites += 1
    return faites


def write_step_models(placements, steps_dir, model_name="modele"):
    """Ecrit un .ldr CUMULATIF par etape et retourne la liste des dossiers crees.

    Cumulatif et non differentiel : une notice montre l'etat du modele apres chaque
    etape, pas les pieces isolees. Chaque etape a son propre dossier pour que le
    renderer par lot (tools/render_batch) puisse les traiter tels quels.
    """
    os.makedirs(steps_dir, exist_ok=True)
    etapes = split_steps(placements)

    dossiers = []
    cumul = []
    for i, etape in enumerate(etapes, 1):
        cumul.extend(etape)
        etape_dir = os.path.join(steps_dir, f"etape_{i:03d}")
        os.makedirs(etape_dir, exist_ok=True)
        write_ldr(list(cumul), os.path.join(etape_dir, "model.ldr"),
                  f"{model_name}_etape_{i:03d}")
        dossiers.append(etape_dir)

    return dossiers, etapes


def write_notice_html(etapes, dossiers, path, model_name="modele", vue="iso"):
    """Assemble la notice HTML a partir des rendus deja produits pour chaque etape.

    Les images sont referencees en chemin relatif : la notice reste lisible tant qu'elle
    accompagne le dossier des etapes.
    """
    base = os.path.dirname(os.path.abspath(path))
    total = sum(len(e) for e in etapes)

    blocs = []
    for i, (etape, dossier) in enumerate(zip(etapes, dossiers), 1):
        # etape.png = assemblage + encart des pieces ; on retombe sur le rendu brut
        # si la composition n'a pas eu lieu (rendus sautes sur un gros modele).
        image = os.path.join(dossier, "etape.png") if dossier else ""
        if not os.path.isfile(image) and dossier:
            image = os.path.join(dossier, f"render_{vue}.png")
        rel = os.path.relpath(image, base).replace(os.sep, "/") if image else ""
        dispo = bool(image) and os.path.isfile(image)

        pieces = {}
        for p in etape:
            cle = f"{p.part.ldraw_id} ({p.part.w}x{p.part.d})"
            pieces[cle] = pieces.get(cle, 0) + 1
        liste = ", ".join(f"{n}x {ref}" for ref, n in sorted(pieces.items()))

        visuel = (f"<img src='{rel}' alt='etape {i}'>" if dispo
                  else "<div class='manque'>rendu absent</div>")
        blocs.append(
            f"<section><h2>Etape {i} <small>/ {len(etapes)}</small></h2>"
            f"{visuel}<p class='ajout'><strong>{len(etape)} piece(s) a poser :</strong> "
            f"{liste}</p></section>"
        )

    html = f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<title>Notice de montage — {model_name}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #222; background: #fafafa; }}
 h1 {{ margin-bottom: .2rem; }}
 .resume {{ color: #666; margin-bottom: 2rem; }}
 section {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
            padding: 1rem; margin-bottom: 1.5rem; }}
 h2 {{ margin: 0 0 .8rem; font-size: 1.1rem; }}
 h2 small {{ color: #888; font-weight: normal; }}
 img {{ max-width: 420px; width: 100%; border-radius: 6px; background: #2b2d33; }}
 .ajout {{ margin: .8rem 0 0; font-size: .92rem; }}
 .manque {{ padding: 3rem; background: #f0f0f0; color: #999; text-align: center;
            border-radius: 6px; }}
</style></head><body>
<h1>Notice de montage — {model_name}</h1>
<p class="resume">{len(etapes)} etapes, {total} pieces au total.
Chaque image montre le modele <em>a la fin</em> de l'etape.</p>
{chr(10).join(blocs)}
</body></html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return len(etapes)


if __name__ == "__main__":
    import json

    from src.compiler import compile_design
    from src.tiler import tile

    if len(sys.argv) < 2:
        print("Usage: python -m src.instructions design.json [dossier_sortie]")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        design_data = json.load(f)
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(sys.argv[1]))
    nom = design_data.get("name", "modele")

    placements_data = tile(compile_design(design_data))
    steps_dir = os.path.join(out_dir, "etapes")
    dossiers_data, etapes_data = write_step_models(placements_data, steps_dir, nom)
    write_notice_html(etapes_data, dossiers_data,
                      os.path.join(out_dir, "notice.html"), nom)

    print(f"{len(etapes_data)} etapes ecrites dans {steps_dir}")
    print(f"Rendre les etapes :  python -m tools.render_batch --glob \"{steps_dir}/etape_*\"")
    print(f"Puis rouvrir        {os.path.join(out_dir, 'notice.html')}")
