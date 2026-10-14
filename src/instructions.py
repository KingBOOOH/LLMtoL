"""Notice de montage (Phase 5, tache 5.3). Deterministe, aucun appel LLM.

Decoupe le modele en etapes de montage, ecrit un .ldr CUMULATIF par etape (etat du modele
a la fin de l'etape), puis assemble une notice HTML une fois les etapes rendues.

Choix du generateur maison plutot que LPub3D (tache 5.2) : LPub3D n'est pas installe sur
cette machine, et l'evaluer supposerait d'ajouter une dependance externe lourde pour un
besoin que la machinerie existante couvre deja — on sait deja ecrire un .ldr et rendre un
lot en un seul processus Blender.

Decoupage : nombre FIXE de pieces par etape, dans l'ordre de montage reel (z croissant :
on ne pose pas une piece sous une piece deja posee). Sur les gros modeles ce nombre
augmente automatiquement pour que le total d'etapes reste borne — rendre une image par
etape coute un passage Blender, et une notice de 159 etapes est de toute facon illisible.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.ldraw_writer import write_ldr
from src.validator import resume_constructibilite

# L'image d'etape est en JPEG, et c'est tout le poids de la notice PDF. Mesure sur un
# rendu reel de 800x800 : PNG 398 ko, JPEG qualite 92 a RESOLUTION IDENTIQUE 22 ko, soit
# 18x moins. Un rendu Blender est une image photographique (degrades, anticrenelage), le
# pire cas pour la compression sans perte de PNG et le cas nominal pour JPEG. On ne
# reduit PAS la definition : c'est elle qui tient la qualite d'impression, et elle ne
# coute presque rien une fois le bon format choisi.
NOM_IMAGE_ETAPE = "etape.jpg"
QUALITE_JPEG = 92
# Vignette de piece pour la liste des pieces : affichee en 42 px de cote, soit environ
# 131 px a 300 DPI. 160 laisse de la marge a l'impression sans payer le plein format.
NOM_VIGNETTE = "vignette.jpg"
TAILLE_VIGNETTE = 160
# Les notices produites avant ce changement ont un etape.png a cote : on le relit plutot
# que de leur faire perdre l'encart des pieces a la regeneration.
NOM_IMAGE_ETAPE_LEGACY = "etape.png"

PIECES_PAR_ETAPE = int(os.environ.get("LEGO_PIECES_PAR_ETAPE", "8"))
# Si l'utilisateur fixe la variable, son choix prime et le regroupement automatique est
# desactive : on ne discute pas une consigne explicite.
PIECES_PAR_ETAPE_IMPOSE = "LEGO_PIECES_PAR_ETAPE" in os.environ

# Plafond du nombre d'etapes. Vit ici, la ou le decoupage se decide, et non dans
# pipeline.py qui ne fait que consommer le resultat.
NOTICE_MAX_ETAPES = int(os.environ.get("LEGO_NOTICE_MAX_ETAPES", "40"))


def pieces_par_etape_pour(n_pieces, max_etapes=None):
    """Combien de pieces par etape pour que le total d'etapes reste sous le plafond.

    En dessous du seuil (8 x 40 = 320 pieces), renvoie PIECES_PAR_ETAPE : le
    comportement des petits modeles ne bouge pas. Au-dela, le nombre grossit juste ce
    qu'il faut. Un choix explicite via LEGO_PIECES_PAR_ETAPE desactive l'adaptation.
    """
    plafond = max_etapes or NOTICE_MAX_ETAPES
    if PIECES_PAR_ETAPE_IMPOSE:
        return PIECES_PAR_ETAPE
    return max(PIECES_PAR_ETAPE, math.ceil(n_pieces / plafond))


def split_steps(placements, pieces_par_etape=None, max_etapes=None):
    """Repartit les placements en etapes d'un nombre fixe de pieces.

    Decoupage par quantite et non par couche z : une couche peut contenir une seule
    piece comme cinquante, ce qui donnait des etapes tres inegales et faisait perdre
    le fil. A nombre constant, chaque etape demande le meme effort.

    Sur un gros modele le nombre de pieces par etape augmente automatiquement, pour que
    le total d'etapes reste sous NOTICE_MAX_ETAPES. Avant, au-dela de 320 pieces les
    rendus d'etapes etaient purement SAUTES : un chateau de 1267 pieces donnait 159
    etapes et une notice sans la moindre image, donc inutilisable. Mieux vaut des
    etapes plus chargees qu'une notice vide.

    L'ordre de montage reste physiquement valide : les placements sont tries par z
    croissant d'abord (on ne pose pas une piece sous une piece deja posee), puis par
    position dans la couche pour que la progression soit lisible d'une etape a l'autre.
    """
    taille = pieces_par_etape or pieces_par_etape_pour(len(placements), max_etapes)
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
            # piece, pas son orientation dans le modele. `retournee` est remis a plat
            # pour la meme raison, sinon la vignette d'une pente dependrait de celle
            # de ses occurrences qui a ete rencontree en premier.
            seule = p._replace(x=0, y=0, z=0, rotated=False, retournee=False)
            write_ldr([seule], os.path.join(dossier, "model.ldr"), f"piece_{cle[0]}")
            modeles[cle] = dossier
    return modeles


def vignette_piece(dossier, vue="iso"):
    """Petite image d'une piece pour la liste des pieces, ou "" si le rendu manque.

    La notice PDF affiche ces vignettes en 42 px de cote (img.vign dans src/pdf.py) et
    les embarquait en 800x800 : 360 fois plus de pixels que necessaire, et de loin le
    premier poste de poids du PDF — 32 vignettes pesaient 20,1 Mo sur un modele de 134
    pieces, contre 7,4 Mo pour les 17 images d'etape.

    TAILLE_VIGNETTE est dimensionnee pour l'IMPRESSION, pas pour l'ecran : 42 px CSS
    font environ 131 px a 300 DPI. On garde de la marge sans payer le plein format.

    La vignette est ecrite a cote du rendu et reutilisee si elle existe deja.
    """
    if not dossier:
        return ""
    source = os.path.join(dossier, f"render_{vue}.png")
    if not os.path.isfile(source):
        return ""
    cible = os.path.join(dossier, NOM_VIGNETTE)
    if os.path.isfile(cible):
        return cible
    try:
        from PIL import Image

        im = Image.open(source).convert("RGB")
        im.thumbnail((TAILLE_VIGNETTE, TAILLE_VIGNETTE), Image.LANCZOS)
        im.save(cible, "JPEG", quality=QUALITE_JPEG, optimize=True)
        return cible
    except OSError:
        # Une vignette ratee ne doit pas faire echouer une notice : on sert le rendu
        # plein format, qui est lourd mais juste.
        return source


def _enregistrer(image, out_path):
    """Ecrit l'image, en JPEG si l'extension le demande (voir QUALITE_JPEG)."""
    if out_path.lower().endswith((".jpg", ".jpeg")):
        image.save(out_path, "JPEG", quality=QUALITE_JPEG, optimize=True)
    else:
        image.save(out_path)


def chemin_image_etape(dossier, vue="iso"):
    """Image a afficher pour une etape, ou "" si aucune n'existe.

    Trois candidats dans l'ordre : l'image composee du jour, celle des notices
    anterieures au passage en JPEG, puis le rendu brut — ce dernier n'a pas l'encart
    des pieces, mais une notice amputee d'un encart reste utilisable, alors qu'une
    image manquante laisse un cadre casse en pleine page.

    Vit ici et pas en double dans src/pdf.py : les deux generateurs de notice doivent
    tomber sur la meme image, sinon le HTML et le PDF divergent sans que rien ne le
    signale.
    """
    if not dossier:
        return ""
    for nom in (NOM_IMAGE_ETAPE, NOM_IMAGE_ETAPE_LEGACY, f"render_{vue}.png"):
        chemin = os.path.join(dossier, nom)
        if os.path.isfile(chemin):
            return chemin
    return ""


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
    W, _ = base.size

    vignettes = [(p, n) for p, n in piece_images if os.path.isfile(p)]
    if not vignettes:
        _enregistrer(base, out_path)
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

    # Fond TRANSPARENT, et un bandeau blanc dessine par ligne, large exactement de son
    # contenu. Un rectangle blanc unique laissait une zone vide des que le nombre de
    # pieces n'etait pas un multiple du nombre de colonnes (5 ou 7 pieces sur 3
    # colonnes) : ca se lisait comme une piece manquante. Centrer la derniere ligne
    # deplacait le vide sans le supprimer.
    encart = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    dessin = ImageDraw.Draw(encart)
    try:
        police = ImageFont.truetype("arialbd.ttf", max(14, cell // 5))
    except OSError:
        police = ImageFont.load_default()

    # Lignes alignees a DROITE : l'encart est colle au coin superieur droit, son bord
    # droit reste donc rectiligne et le decrochage se voit a gauche, ou il est discret.
    def _decalage(ligne):
        return box_w - min(colonnes, n - ligne * colonnes) * cell

    for ligne in range(lignes):
        x = _decalage(ligne)
        y = ligne * cell
        largeur = min(colonnes, n - ligne * colonnes) * cell
        dessin.rectangle([x, y, x + largeur - 1, y + cell - 1],
                         fill=(255, 255, 255, 255), outline=(180, 180, 180), width=3)

    for i, (chemin, quantite) in enumerate(vignettes):
        ligne, col = divmod(i, colonnes)
        cx = _decalage(ligne) + col * cell
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

    # Colle avec son canal alpha : les zones sans piece laissent voir l'assemblage.
    base.paste(encart, (W - box_w - marge, marge), encart)
    _enregistrer(base, out_path)
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
                              vignettes, os.path.join(dossier, NOM_IMAGE_ETAPE)):
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


def _bloc_etat(resume):
    """Encadre d'ouverture de la notice : le modele tient-il debout ?

    Un modele sans probleme le dit explicitement, plutot que d'afficher un encadre
    vide qui laisserait croire a un oubli.
    """
    if resume is None:
        return ""
    if resume["ok"]:
        return ("<div class='etat bon'><h3>Avant de commencer</h3>"
                "Ce modele est d'un seul tenant, pose au sol, et chaque piece repose "
                "sur quelque chose. Rien a surveiller.</div>")
    points = "".join(f"<li>{p}</li>" for p in resume["phrases"])
    return (f"<div class='etat attention'><h3>Avant de commencer</h3>"
            f"<ul>{points}</ul></div>")


def write_notice_html(etapes, dossiers, path, model_name="modele", vue="iso",
                      rapport=None):
    """Assemble la notice HTML a partir des rendus deja produits pour chaque etape.

    Les images sont referencees en chemin relatif : la notice reste lisible tant qu'elle
    accompagne le dossier des etapes.

    rapport : le rapport du validateur. S'il est fourni, la notice s'ouvre sur un etat
    de constructibilite en francais et signale les etapes contenant une piece sans
    support. Sans lui la notice reste correcte, simplement muette sur ce point — ce
    qu'elle etait avant : l'utilisateur recevait quatre images et devait juger seul.
    """
    base = os.path.dirname(os.path.abspath(path))
    total = sum(len(e) for e in etapes)

    resume = resume_constructibilite(rapport) if rapport else None
    # Coordonnees des pieces sans support -> numeros d'etapes concernees. Les etapes
    # etant une partition des placements, l'appartenance est exacte.
    sans_support = {tuple(c) for c in (rapport or {}).get("metrics", {}).get("flottantes", [])}

    blocs = []
    for i, (etape, dossier) in enumerate(zip(etapes, dossiers), 1):
        image = chemin_image_etape(dossier, vue)
        rel = os.path.relpath(image, base).replace(os.sep, "/") if image else ""
        dispo = bool(image)

        pieces = {}
        for p in etape:
            cle = f"{p.part.ldraw_id} ({p.part.w}x{p.part.d})"
            pieces[cle] = pieces.get(cle, 0) + 1
        liste = ", ".join(f"{n}x {ref}" for ref, n in sorted(pieces.items()))

        visuel = (f"<img src='{rel}' alt='etape {i}'>" if dispo
                  else "<div class='manque'>rendu absent</div>")

        n_sans_support = sum((p.x, p.y, p.z) in sans_support for p in etape)
        alerte = (f"<p class='alerte'>{n_sans_support} piece(s) de cette etape n'ont "
                  f"rien juste en dessous : maintiens-les en place le temps de poser "
                  f"la suite.</p>" if n_sans_support else "")

        blocs.append(
            f"<section><h2>Etape {i} <small>/ {len(etapes)}</small></h2>"
            f"{visuel}<p class='ajout'><strong>{len(etape)} piece(s) a poser :</strong> "
            f"{liste}</p>{alerte}</section>"
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
 .alerte {{ margin: .6rem 0 0; padding: .5rem .7rem; font-size: .9rem;
            background: #fff6e0; border-left: 3px solid #e8a33d; border-radius: 4px; }}
 .etat {{ padding: .9rem 1.1rem; border-radius: 8px; margin-bottom: 1.8rem;
          border-left: 4px solid; }}
 .etat.bon {{ background: #eef9f1; border-color: #4ec37a; }}
 .etat.attention {{ background: #fff6e0; border-color: #e8a33d; }}
 .etat h3 {{ margin: 0 0 .4rem; font-size: 1rem; }}
 .etat ul {{ margin: .4rem 0 0; padding-left: 1.2rem; }}
</style></head><body>
<h1>Notice de montage — {model_name}</h1>
<p class="resume">{len(etapes)} etapes, {total} pieces au total.
Chaque image montre le modele <em>a la fin</em> de l'etape.</p>
{_bloc_etat(resume)}
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
