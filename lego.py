"""Genere un modele LEGO complet depuis une description, en une commande.

    python lego.py "un phare blanc avec une lanterne rouge"
    python lego.py "une petite maison" --taille petite --iterations 3

Produit un dossier horodate contenant le plan, le design DSL, le rapport du validateur,
model.ldr, les 4 rendus, la planche final.png, la nomenclature et la notice.

Ne reimplemente RIEN : tout vient de pipeline.py (generer_modele, preparer_notice,
render_all), qui sert aussi a pipeline.py lui-meme et a agent.py. Un seul chemin de
generation dans le projet, donc un seul endroit a corriger quand il bouge.
"""

import argparse
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import pipeline
from src.bom import write_bom
from src.dsl_schema import TAILLES, resoudre_bornes
from src.instructions import compose_all_steps, vignette_piece, write_notice_html
from src.llm_client import LLMCallError
from src.pdf import write_pdf

OUTPUTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")


def _nom_de_dossier(prompt):
    """Fabrique un fragment de nom de dossier lisible a partir du prompt.

    Sert uniquement au confort de navigation dans outputs/ : l'horodatage reste ce qui
    garantit l'unicite.
    """
    garde = [c.lower() if c.isalnum() else "_" for c in prompt[:40]]
    return "".join(garde).strip("_").replace("__", "_") or "modele"


def analyser_bbox_max(texte):
    """\"10x10x48\" -> [10, 10, 48]. None si rien n'est demande.

    Echec NET sur une saisie invalide : une borne mal lue genererait silencieusement a
    la mauvaise echelle, et l'utilisateur ne verrait le probleme qu'au rendu.
    """
    if not texte:
        return None
    morceaux = texte.lower().replace("*", "x").split("x")
    if len(morceaux) != 3:
        raise SystemExit(f"--bbox-max attend XxYxZ, recu {texte!r} (ex. 10x10x48)")
    try:
        valeurs = [int(m) for m in morceaux]
    except ValueError:
        raise SystemExit(f"--bbox-max attend trois entiers, recu {texte!r}") from None
    try:
        return resoudre_bornes(None, valeurs)
    except ValueError as e:
        raise SystemExit(f"--bbox-max : {e}") from None


def generer(prompt, run_dir, iterations=1, taille=None, avec_notice=True,
            bbox_max=None):
    """Genere `iterations` modeles dans run_dir. Retourne (dossiers_produits, echecs).

    Une iteration qui echoue est journalisee et n'interrompt PAS les suivantes : le
    reproche fait a la chaine d'origine etait justement qu'un echec emportait tout.
    """
    produits, notices, echecs = [], [], []

    for i in range(1, iterations + 1):
        iter_dir = os.path.join(run_dir, f"iteration_{i:03d}")
        os.makedirs(iter_dir, exist_ok=True)
        log_path = os.path.join(iter_dir, "log.txt")
        with open(os.path.join(iter_dir, "prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt)

        if iterations > 1:
            print(f"\n=== iteration {i}/{iterations} ===")
        try:
            result = pipeline.generer_modele(prompt, iter_dir, log_path, taille=taille,
                                             bbox_max=bbox_max)
        except (LLMCallError, RuntimeError) as e:
            pipeline.log_line(log_path, f"ECHEC : {e}")
            echecs.append((i, str(e)))
            continue

        produits.append(iter_dir)
        if avec_notice:
            etape_dirs, piece_models, etapes = pipeline.preparer_notice(
                result["placements"], iter_dir, result["nom"], log_path)
            notices.append((iter_dir, result, etape_dirs, piece_models, etapes))

    if not produits:
        return produits, echecs

    pipeline.render_all(produits, run_dir)

    # Lot separe pour les images de notice : marge plus large et vue iso seule.
    dossiers_notice = [d for _, _, ed, pm, _ in notices for d in list(ed) + list(pm.values())]
    if dossiers_notice:
        env = os.environ.copy()
        env["LEGO_RENDER_MARGIN"] = os.environ.get("LEGO_NOTICE_MARGIN", "1.7")
        env["LEGO_RENDER_VIEWS"] = "iso"
        pipeline.render_all(dossiers_notice, run_dir, assemble_dirs=[], env_extra=env,
                            manifest_name="_manifest_notice.txt")

    for iter_dir, result, etape_dirs, piece_models, etapes in notices:
        images = {}
        if etape_dirs:
            compose_all_steps(etapes, etape_dirs, piece_models)
            images = {c: vignette_piece(d) for c, d in piece_models.items()}
            write_bom(result["placements"], iter_dir, result["nom"], piece_images=images)
        write_notice_html(etapes, etape_dirs or [""] * len(etapes),
                          os.path.join(iter_dir, "notice.html"), result["nom"],
                          rapport=result["report"])
        # Un PDF rate ne doit PAS invalider le modele, qui lui est bien genere.
        try:
            write_pdf(etapes, etape_dirs or [""] * len(etapes), result["placements"],
                      iter_dir, result["nom"],
                      rendu_final=os.path.join(iter_dir, "render_iso.png"),
                      piece_images=images, prompt=prompt, rapport=result["report"])
        except RuntimeError as e:
            print(f"Notice PDF non produite : {e}")

    return produits, echecs


def main():
    parser = argparse.ArgumentParser(
        description="Genere un modele LEGO complet depuis une description.")
    parser.add_argument("prompt", help="ce qu'on veut construire, en francais")
    parser.add_argument("--taille", choices=sorted(TAILLES),
                        help="borne la taille du modele (garantie par le schema DSL)")
    parser.add_argument("--bbox-max", metavar="XxYxZ",
                        help="borne exacte, ex. 10x10x48 pour une forme elancee ; l'emporte sur --taille")
    parser.add_argument("--iterations", type=int, default=1,
                        help="nombre de variantes a produire (defaut 1)")
    parser.add_argument("--sans-notice", action="store_true",
                        help="saute nomenclature et notice (plus rapide)")
    parser.add_argument("--skip-env-check", action="store_true",
                        help="ne pas verifier LM Studio avant de generer")
    args = parser.parse_args()

    if args.iterations < 1:
        raise SystemExit("--iterations doit valoir au moins 1")

    # Une forme elancee ne tient pas dans un plafond cubique : un phare demande en
    # --taille petite plafonne a 16x16x20 et sort en gateau. --bbox-max permet de dire
    # etroit ET haut, ce que les trois tailles nommees ne savent pas exprimer.
    bornes_demandees = analyser_bbox_max(args.bbox_max)

    # Avant de depenser la moindre generation : LM Studio reinjecte le System Prompt et
    # le Structured Output configures sur le modele des qu'une requete n'envoie pas son
    # propre response_format. Sous ce piege le planificateur repond n'importe quoi.
    if not args.skip_env_check:
        from tools.check_env import preflight_llm
        ok, message = preflight_llm()
        print(f"Pre-controle LM Studio : {message}")
        if not ok:
            raise SystemExit("\nInterrompu AVANT toute generation. Corrige le point "
                             "ci-dessus, ou relance avec --skip-env-check.")

    run_dir = os.path.join(
        OUTPUTS_DIR, f"{time.strftime('%Y%m%d_%H%M%S')}_{_nom_de_dossier(args.prompt)}")
    os.makedirs(run_dir, exist_ok=True)
    print(f"Dossier : {run_dir}")

    t0 = time.time()
    produits, echecs = generer(args.prompt, run_dir, iterations=args.iterations,
                               taille=args.taille, avec_notice=not args.sans_notice,
                               bbox_max=bornes_demandees)
    duree = time.time() - t0

    reussites = len(produits)
    print(f"\n{reussites}/{args.iterations} modele(s) produit(s) en {duree:.0f}s")
    for i, err in echecs:
        print(f"  iteration {i} en echec : {err}")

    if reussites == 0:
        raise SystemExit("Aucun modele produit.")

    # Le premier dossier REUSSI, pas iteration_001 : si la premiere iteration a echoue,
    # pointer vers son dossier donnerait un chemin sans rendu ni notice.
    premier = os.path.abspath(produits[0])
    print(f"\nResultat  : {os.path.abspath(run_dir)}")
    print(f"Planche   : {os.path.join(premier, 'final.png')}")
    if not args.sans_notice:
        print(f"Notice    : {os.path.join(premier, 'notice.html')}")
        pdf = os.path.join(premier, "notice.pdf")
        if os.path.isfile(pdf):
            print(f"Notice PDF: {pdf}")
    # Code de sortie non nul si une iteration au moins a echoue, pour qu'un script
    # appelant puisse le detecter.
    if echecs:
        sys.exit(1)


if __name__ == "__main__":
    main()
