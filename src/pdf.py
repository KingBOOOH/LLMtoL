"""Notice PDF (US-10). Deterministe, aucun appel LLM.

Structure : couverture avec le rendu du modele termine, page de presentation du projet,
les etapes de montage, puis la nomenclature complete avec images et quantites.

Pourquoi passer par le HTML plutot que par reportlab : src/instructions.py et src/bom.py
produisent deja cette mise en page, images comprises. L'ecrire une seconde fois dans un
moteur PDF donnerait deux mises en page a maintenir, qui divergeraient. On compose donc
un HTML unique et on le fait imprimer par un navigateur en mode headless — Edge est
livre avec Windows, donc zero dependance nouvelle (voir config.NAVIGATEURS_PDF).
"""

import os
import shutil
import subprocess
import sys
import tempfile

import config
from src.bom import build_bom
from src.instructions import chemin_image_etape
from src.validator import resume_constructibilite

A_PROPOS = """\
Ce modele a ete concu par <strong>LLMtoL</strong>, qui transforme une description en
francais en un modele LEGO fait de <strong>vraies pieces du catalogue LDraw</strong>.

Le modele de langage n'y dessine jamais les briques. Il ecrit un <em>programme
d'operations volumetriques</em> — poser un pave, creuser, empiler une colonne, extruder
un cylindre — et du code deterministe compile ce programme en briques reelles, puis
verifie sa geometrie : pas de collision, une seule piece d'un seul tenant, un modele
pose au sol.

Cette separation est volontaire. Imaginer une forme est ce que le modele fait bien ;
garantir qu'elle tient debout et poser les briques une a une sont deux choses qu'il fait
mal. Ces deux-la sont confiees a du code.
"""


def trouver_navigateur():
    """Premier navigateur disponible pour l'impression PDF, ou None.

    Cherche d'abord dans config.NAVIGATEURS_PDF, puis dans le PATH : une installation
    non standard reste utilisable sans editer la configuration.
    """
    for chemin in config.NAVIGATEURS_PDF:
        if os.path.isfile(chemin):
            return chemin
    for nom in ("msedge", "chromium", "google-chrome", "chrome"):
        trouve = shutil.which(nom)
        if trouve:
            return trouve
    return None


def _img(chemin, base, classe=""):
    """Balise <img> en chemin relatif, ou chaine vide si le fichier manque.

    Un PDF ampute d'une image reste utilisable ; une balise pointant dans le vide
    laisserait un cadre casse en plein milieu de la page.
    """
    if not chemin or not os.path.isfile(chemin):
        return ""
    rel = os.path.relpath(chemin, base).replace(os.sep, "/")
    attr = f" class='{classe}'" if classe else ""
    return f"<img src='{rel}'{attr}>"


def construire_html(etapes, dossiers, placements, base, model_name="modele",
                    rendu_final=None, piece_images=None, prompt=None, identifiant=None,
                    date=None, vue="iso", rapport=None):
    """Compose le HTML des quatre sections, pret a imprimer.

    base : dossier depuis lequel les chemins d'images sont calcules — le HTML doit etre
    ecrit la, sinon les images ne se resolvent pas.
    """
    piece_images = piece_images or {}
    rows = build_bom(placements)
    total = sum(r["quantite"] for r in rows)

    # --- couverture ---
    meta = [f"<strong>{total}</strong> pieces", f"{len(etapes)} etapes"]
    if date:
        meta.append(date)
    if identifiant:
        meta.append(f"<code>{identifiant}</code>")
    couverture = (
        f"<section class='page couverture'>"
        f"{_img(rendu_final, base, 'grand')}"
        f"<h1>{model_name}</h1>"
        + (f"<p class='prompt'>&laquo;&nbsp;{prompt}&nbsp;&raquo;</p>" if prompt else "")
        + f"<p class='meta'>{' &middot; '.join(meta)}</p></section>"
    )

    apropos = f"<section class='page apropos'><h2>A propos</h2>{A_PROPOS}</section>"

    # Meme texte que la notice HTML et que l'interface web (US-03) : ce qu'il faut
    # savoir avant de commencer a construire.
    etat = ""
    if rapport:
        resume = resume_constructibilite(rapport)
        if resume["ok"]:
            etat = ("<div class='etat'><strong>Avant de commencer.</strong> Ce modele "
                    "est d'un seul tenant, pose au sol, et chaque piece repose sur "
                    "quelque chose. Rien a surveiller.</div>")
        else:
            points = "".join(f"<li>{x}</li>" for x in resume["phrases"])
            etat = (f"<div class='etat attention'><strong>Avant de commencer.</strong>"
                    f"<ul>{points}</ul></div>")

    # --- etapes ---
    blocs = []
    for i, (etape, dossier) in enumerate(zip(etapes, dossiers), 1):
        image = chemin_image_etape(dossier, vue)

        pieces = {}
        for p in etape:
            pieces[f"{p.part.ldraw_id} ({p.part.w}x{p.part.d})"] = \
                pieces.get(f"{p.part.ldraw_id} ({p.part.w}x{p.part.d})", 0) + 1
        liste = ", ".join(f"{n}x {ref}" for ref, n in sorted(pieces.items()))

        blocs.append(
            f"<section class='etape'><h3>Etape {i} <small>/ {len(etapes)}</small></h3>"
            f"{_img(image, base)}"
            f"<p><strong>{len(etape)} piece(s) a poser :</strong> {liste}</p></section>")

    # --- nomenclature ---
    lignes = []
    for r in rows:
        vignette = _img(piece_images.get((r["ldraw_id"], r["_color_index"])), base, "vign")
        lignes.append(
            f"<tr><td>{vignette}</td><td class='q'>{r['quantite']}x</td>"
            f"<td><code>{r['ldraw_id']}</code></td>"
            f"<td>{r['type']} {r['dimensions']}</td>"
            f"<td>{r['couleur_ldraw']}</td></tr>")
    nomenclature = (
        f"<section class='page'><h2>Liste des pieces</h2>"
        f"<p>{total} pieces, {len(rows)} references distinctes.</p>"
        f"<table><tr><th></th><th>Qte</th><th>Ref.</th><th>Piece</th><th>Couleur</th></tr>"
        f"{''.join(lignes)}</table></section>")

    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Notice — {model_name}</title>
<style>
 @page {{ size: A4; margin: 14mm; }}
 body {{ font-family: system-ui, sans-serif; color: #1b1b1b; margin: 0; }}
 /* Une etape coupee en deux est inutilisable : on l'empeche de franchir une page. */
 .etape {{ page-break-inside: avoid; border: 1px solid #ddd; border-radius: 6px;
           padding: 10px 12px; margin-bottom: 12px; }}
 .page {{ page-break-after: always; }}
 .couverture {{ text-align: center; padding-top: 25mm; }}
 .couverture h1 {{ font-size: 30pt; margin: 10mm 0 4mm; }}
 .prompt {{ font-size: 13pt; color: #555; font-style: italic; margin: 0 0 6mm; }}
 .meta {{ color: #777; font-size: 10pt; }}
 img {{ max-width: 100%; }}
 img.grand {{ max-width: 135mm; border-radius: 8px; background: #2b2d33; }}
 .etape img {{ max-width: 95mm; border-radius: 5px; background: #2b2d33; }}
 .apropos {{ font-size: 11.5pt; line-height: 1.6; }}
 .etat {{ padding: 4mm 5mm; border-left: 3px solid #4ec37a; background: #eef9f1;
          border-radius: 4px; margin-bottom: 6mm; font-size: 10.5pt;
          page-break-inside: avoid; }}
 .etat.attention {{ border-color: #e8a33d; background: #fff6e0; }}
 .etat ul {{ margin: 2mm 0 0; padding-left: 5mm; }}
 h2 {{ font-size: 17pt; border-bottom: 2px solid #1b1b1b; padding-bottom: 3mm; }}
 h3 {{ font-size: 12pt; margin: 0 0 6px; }}
 h3 small {{ color: #888; font-weight: normal; }}
 table {{ border-collapse: collapse; width: 100%; font-size: 10pt; }}
 th, td {{ border-bottom: 1px solid #e0e0e0; padding: 4px 6px; text-align: left; }}
 tr {{ page-break-inside: avoid; }}
 .q {{ font-weight: 700; white-space: nowrap; }}
 img.vign {{ width: 42px; height: 42px; object-fit: contain; background: #2b2d33;
             border-radius: 4px; display: block; }}
</style></head><body>
{couverture}
{apropos}
<section><h2>Montage</h2>{etat}{''.join(blocs)}</section>
{nomenclature}
</body></html>"""


def arguments_de_bac_a_sable():
    """--no-sandbox, mais UNIQUEMENT quand il est indispensable.

    Chromium refuse de demarrer en root sans cette option : "Running as root without
    --no-sandbox is not supported". C'est le cas dans le conteneur de generation, qui
    tourne en root. Le message etait invisible jusqu'ici parce que la sortie du
    navigateur etait capturee puis jetee : on voyait seulement "aucun PDF", apres neuf
    minutes de rendus.

    La condition est volontairement etroite. Desactiver le bac a sable sur un poste de
    travail affaiblirait reellement le navigateur, pour un gain nul : hors root, il
    demarre tres bien avec.
    """
    if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0:
        return ["--no-sandbox"]
    return []


def html_vers_pdf(html_path, pdf_path, timeout=180):
    """Imprime un fichier HTML local en PDF via un navigateur headless.

    Leve RuntimeError si aucun navigateur n'est disponible ou si le PDF n'est pas
    produit : l'appelant decide quoi en faire. Un PDF manquant ne doit pas invalider le
    modele, qui lui est bien genere.
    """
    navigateur = trouver_navigateur()
    if navigateur is None:
        raise RuntimeError(
            "Aucun navigateur trouve pour produire le PDF. Chemins essayes : "
            + ", ".join(config.NAVIGATEURS_PDF))

    # --user-data-dir dans un dossier jetable : sans lui, une instance Edge deja
    # ouverte capte la commande et le mode headless ne produit rien.
    #
    # --disable-dev-shm-usage : dans un conteneur, /dev/shm vaut 64 Mo par defaut et
    # Chromium peut s'y effondrer sur une page lourde. Precaution, sans effet ailleurs.
    with tempfile.TemporaryDirectory(prefix="llmtol_pdf_") as profil:
        commande = [
            navigateur, "--headless=new", "--disable-gpu", "--disable-dev-shm-usage",
            f"--user-data-dir={profil}", "--no-pdf-header-footer",
            *arguments_de_bac_a_sable(),
            f"--print-to-pdf={os.path.abspath(pdf_path)}",
            "file:///" + os.path.abspath(html_path).replace(os.sep, "/"),
        ]
        try:
            sortie = subprocess.run(commande, capture_output=True, timeout=timeout,
                                    check=False)
        except subprocess.TimeoutExpired as e:
            # Sans ce rattrapage, l'exception echappe au `except (RuntimeError, OSError)`
            # de l'appelant et fait echouer tout le job pour un PDF manquant, alors que
            # le modele, lui, est bon.
            raise RuntimeError(
                f"Le navigateur n'a pas fini en {timeout}s ({navigateur}). "
                "Notice trop lourde ?") from e

    if not os.path.isfile(pdf_path) or os.path.getsize(pdf_path) == 0:
        # La sortie du navigateur etait capturee puis jetee : l'erreur disait seulement
        # "aucun PDF", sans jamais dire pourquoi. Elle est maintenant rapportee.
        details = (sortie.stderr or sortie.stdout or b"").decode("utf-8", "replace")
        details = " / ".join(details.split()[-40:]) if details.strip() else "aucun message"
        raise RuntimeError(
            f"Le navigateur n'a produit aucun PDF ({navigateur}, code "
            f"{sortie.returncode}) : {details[:400]}")
    return pdf_path


def write_pdf(etapes, dossiers, placements, work_dir, model_name="modele",
              rendu_final=None, piece_images=None, prompt=None, identifiant=None,
              date=None, rapport=None):
    """Ecrit notice.pdf dans work_dir. Retourne son chemin.

    Le HTML intermediaire est conserve a cote (notice_pdf.html) : il explique un PDF
    rate sans avoir a tout rejouer.
    """
    html_path = os.path.join(work_dir, "notice_pdf.html")
    html = construire_html(etapes, dossiers, placements, work_dir, model_name,
                           rendu_final=rendu_final, piece_images=piece_images,
                           prompt=prompt, identifiant=identifiant, date=date,
                           rapport=rapport)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    return html_vers_pdf(html_path, os.path.join(work_dir, "notice.pdf"))


if __name__ == "__main__":
    import json

    from src.compiler import compile_design
    from src.tiler import tile

    if len(sys.argv) < 2:
        print("Usage: python -m src.pdf design.json [dossier_sortie]")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        design_data = json.load(f)
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(sys.argv[1]))

    from src.instructions import split_steps

    placements_data = tile(compile_design(design_data))
    etapes_data = split_steps(placements_data)
    chemin = write_pdf(etapes_data, [""] * len(etapes_data), placements_data, out_dir,
                       design_data.get("name", "modele"))
    print(f"{len(etapes_data)} etapes, {len(placements_data)} pieces -> {chemin}")
