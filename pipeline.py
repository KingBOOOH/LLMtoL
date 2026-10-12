"""Orchestrateur bout-en-bout : prompts.json -> planches de rendus en vraies pieces LEGO.

    prompt -> planner -> designer -> compiler -> tiler -> ldraw_writer -> Blender -> final.png

Deroulement en deux temps, et non modele par modele :
1. GENERATION — pour chaque prompt et chaque iteration : plan (LLM libre), design (LLM
   contraint par le schema DSL, avec boucle de reparation), compilation en voxels,
   pavage en pieces du catalogue, ecriture du model.ldr.
2. RENDU — un SEUL processus Blender rend tous les modeles produits, puis les planches
   sont assemblees. Grouper evite de repayer le demarrage de Blender a chaque modele.

Garanties conservees de la version precedente :
- une iteration qui echoue est journalisee et n'interrompt pas les suivantes ;
- chaque execution ecrit dans outputs/run_<horodatage>/, donc deux runs ne s'ecrasent
  jamais.

La chaine d'origine (le LLM produisait directement une liste de cubes) a ete retiree :
elle ne produisait pas de vraies pieces et ne garantissait rien geometriquement. Sa
mesure de reference reste figee dans eval/results/.
"""

import json
import os
import re
import subprocess
import sys
import time

# A faire AVANT tout print : sortie redirigee (run en arriere-plan), Python utilise
# sinon l'encodage Windows par defaut (cp1252), qui ne sait pas ecrire les emojis
# ci-dessous et fait planter le script des la premiere ligne affichee.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import config
from src.bom import write_bom
from src.designer import design as make_design
from src.instructions import (compose_all_steps, write_notice_html, write_piece_models,
                              write_step_models)
from src.ldraw_writer import write_ldr
from src.pdf import write_pdf
from src.llm_client import LLMCallError
from src.planner import plan as make_plan

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src")
OUTPUTS_DIR = os.path.join(BASE_DIR, "outputs")

BLENDER_SCRIPT = os.path.join(SRC_DIR, "blender_script.py")
IMG_SCRIPT = os.path.join(SRC_DIR, "img_output.py")
PROMPTS_FILE = os.path.join(BASE_DIR, "prompts.json")


def log_line(log_path, msg):
    """Journalise une ligne horodatee a l'ecran ET dans le log.txt de l'iteration.

    Le double envoi permet de diagnostiquer apres coup un run lance en arriere-plan,
    dont la sortie console est perdue.
    """
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def generer_modele(prompt_text, iter_dir, log_path, seed=None, taille=None,
                   bbox_max=None):
    """Produit plan.md, design.json, report.json et model.ldr. Rien de plus.

    Separe de la preparation de notice parce que les deux n'ont pas le meme cout :
    ceci prend quelques dizaines de secondes, la notice demande un rendu Blender par
    etape. Quand on propose plusieurs variantes a l'utilisateur, seule la variante
    qu'il retient merite sa notice — d'ou la coupure ici (voir agent.py).

    Retourne le dict de src.designer.design, enrichi de "nom".
    """
    log_line(log_path, "Planification...")
    t0 = time.time()
    plan_text = make_plan(prompt_text, iter_dir, seed=seed, taille=taille,
                          bbox_max=bbox_max)
    log_line(log_path, f"Plan obtenu ({time.time() - t0:.1f}s)")

    log_line(log_path, "Design contraint par le DSL...")
    t1 = time.time()
    result = make_design(prompt_text, plan_text, iter_dir, seed=seed, taille=taille,
                         bbox_max=bbox_max)
    report = result["report"]
    log_line(log_path,
             f"Design valide ({time.time() - t1:.1f}s) — score {report['score']}, "
             f"{report['metrics']['n_pieces']} pieces, "
             f"{result['repairs_used']} reparation(s)")

    nom = result["design"].get("name", "model")
    write_ldr(result["placements"], os.path.join(iter_dir, "model.ldr"), nom)
    log_line(log_path, "model.ldr ecrit")

    result["nom"] = nom
    return result


def preparer_notice(placements, iter_dir, nom, log_path):
    """Ecrit la nomenclature et les sous-modeles d'etapes, prets a etre rendus.

    Retourne (dossiers_etapes, vignettes_de_pieces, etapes). Le nombre d'etapes est
    borne par src.instructions (regroupement adaptatif), donc ce lot de rendus reste
    de taille previsible quelle que soit la taille du modele.
    """
    rows = write_bom(placements, iter_dir, nom)
    log_line(log_path,
             f"Nomenclature : {sum(r['quantite'] for r in rows)} pieces, "
             f"{len(rows)} references")

    steps_dir = os.path.join(iter_dir, "etapes")
    etape_dirs, etapes = write_step_models(placements, steps_dir, nom)

    # Une piece par (reference, couleur) pour l'encart, rendue une seule fois meme si
    # elle revient dans plusieurs etapes.
    piece_models = write_piece_models(etapes, os.path.join(steps_dir, "pieces"))
    log_line(log_path,
             f"Notice : {len(etapes)} etapes, {len(piece_models)} vignettes de pieces")
    return etape_dirs, piece_models, etapes


def generate_one(prompt_text, iter_dir, log_path, seed=None):
    """Genere model.ldr, la nomenclature et les etapes de notice pour une iteration.

    Composition de generer_modele + preparer_notice, conservee telle quelle pour
    pipeline.py qui rend tout d'un bloc.

    Retourne (dossiers_etapes, vignettes, etapes, placements, rapport).
    """
    result = generer_modele(prompt_text, iter_dir, log_path, seed=seed)
    placements = result["placements"]
    etape_dirs, piece_models, etapes = preparer_notice(
        placements, iter_dir, result["nom"], log_path)
    return etape_dirs, piece_models, etapes, placements, result["report"]


# Blender annonce chaque modele du lot par "[k/n] nom" (voir src/blender_script.py).
# C'est la seule progression reelle disponible pendant la phase la plus longue : on la
# lit plutot que d'inventer une barre qui avance toute seule.
_PROGRES_BLENDER = re.compile(r"^\[(\d+)/(\d+)\]")


def render_all(work_dirs, run_dir, assemble_dirs=None, env_extra=None,
               manifest_name="_manifest.txt", on_progress=None):
    """Rend tous les modeles en un seul processus Blender, puis assemble les planches.

    assemble_dirs : sous-ensemble pour lequel produire la planche 2x2. Les etapes de
    notice n'ont besoin que de leur rendu isometrique, pas d'une planche complete.

    on_progress(fait, total) : appele a chaque modele rendu. Sans lui, la sortie de
    Blender est simplement relayee comme avant.
    """
    if not work_dirs:
        print("Aucun modele a rendre.")
        return

    manifest = os.path.join(run_dir, manifest_name)
    with open(manifest, "w", encoding="utf-8") as f:
        for d in work_dirs:
            f.write(os.path.abspath(d) + "\n")

    env = (env_extra or os.environ).copy()
    env["LEGO_BATCH_MANIFEST"] = manifest
    env["PYTHONIOENCODING"] = "utf-8"

    print(f"\nRendu de {len(work_dirs)} modeles en un seul processus Blender...")
    t0 = time.time()
    subprocess.run([config.BLENDER_EXE, "--background", "--python", BLENDER_SCRIPT],
                   env=env, check=False)
    print(f"Rendus termines en {time.time() - t0:.0f}s")

    assembled = 0
    for work_dir in (assemble_dirs if assemble_dirs is not None else work_dirs):
        env_img = os.environ.copy()
        env_img["LEGO_WORK_DIR"] = os.path.abspath(work_dir)
        env_img["PYTHONIOENCODING"] = "utf-8"
        if subprocess.run([sys.executable, IMG_SCRIPT], env=env_img,
                          capture_output=True).returncode == 0:
            assembled += 1
    print(f"Planches assemblees : {assembled}")


def _rendre_en_suivant(env, on_progress):
    """Lance Blender en lisant sa sortie ligne a ligne pour suivre l'avancement.

    Separe de render_all pour que le cas sans suivi reste un simple subprocess.run :
    lire un flux ligne a ligne ouvre des cas d'erreur (encodage, blocage) qu'on ne
    veut pas imposer aux appelants qui n'en ont pas besoin.
    """
    proc = subprocess.Popen(
        [config.BLENDER_EXE, "--background", "--python", BLENDER_SCRIPT],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1)
    for ligne in proc.stdout:
        print(ligne, end="")
        m = _PROGRES_BLENDER.match(ligne)
        if m:
            try:
                on_progress(int(m.group(1)), int(m.group(2)))
            except Exception as e:
                # Un rapport d'avancement casse ne doit jamais interrompre un rendu
                # de plusieurs minutes deja engage.
                print(f"(suivi d'avancement ignore : {e})")
    proc.wait()


def main():
    """Execute prompts.json de bout en bout : generation, rendus, notices.

    Les rendus sont groupes en DEUX lots Blender : les modeles complets au cadrage
    serre (quatre vues, pour la planche), puis les images de notice dezoomees et en
    vue iso seule. Separer les deux permet des reglages differents sans repayer un
    demarrage par modele.
    """
    if not os.path.exists(PROMPTS_FILE):
        raise SystemExit("prompts.json introuvable")

    # Avant de depenser la moindre generation : verifier que LM Studio n'est pas
    # detourne par un System Prompt / Structured Output configure sur le modele. Sous
    # ce piege le planificateur repond du JSON de cubes a n'importe quelle demande, et
    # un run complet part entierement a la poubelle. Une seule requete, quelques
    # secondes. --skip-env-check pour un run hors ligne.
    if "--skip-env-check" not in sys.argv:
        from tools.check_env import preflight_llm
        ok, message = preflight_llm()
        print(f"Pre-controle LM Studio : {message}")
        if not ok:
            raise SystemExit(
                "\nRun interrompu AVANT toute generation.\n"
                "Corrige le point ci-dessus, ou relance avec --skip-env-check pour forcer.")

    with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
        prompts_data = json.load(f)

    run_dir = os.path.join(OUTPUTS_DIR, f"run_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(run_dir, exist_ok=True)
    print(f"Run : {run_dir}")

    total_start = time.time()
    failures = []
    generated = []
    notice_dirs = []  # etapes + vignettes, rendues avec une marge plus large
    notices = []     # (dossier_iteration, dossiers_etapes, etapes)
    total_iterations = 0

    for prompt_idx, prompt_data in enumerate(prompts_data, 1):
        prompt_text = prompt_data["prompt"]
        iterations = prompt_data.get("iterations", 1)
        prompt_dir = os.path.join(run_dir, f"prompt_{prompt_idx:03d}")

        for j in range(1, iterations + 1):
            total_iterations += 1
            iter_dir = os.path.join(prompt_dir, f"iteration_{j:03d}")
            os.makedirs(iter_dir, exist_ok=True)
            log_path = os.path.join(iter_dir, "log.txt")

            with open(os.path.join(iter_dir, "prompt.txt"), "w", encoding="utf-8") as f:
                f.write(prompt_text)

            print(f"\n=== PROMPT {prompt_idx} iteration {j}/{iterations} ===")
            print(f"{prompt_text[:70]}...")

            try:
                etape_dirs, piece_models, etapes, placements, result_report =                     generate_one(prompt_text, iter_dir, log_path)
                generated.append(iter_dir)
                # Les etapes partent dans le MEME lot Blender que le modele complet :
                # un seul demarrage pour tout rendre.
                notices.append((iter_dir, etape_dirs, piece_models, etapes, placements,
                                result_report))
                notice_dirs.extend(etape_dirs)
                notice_dirs.extend(piece_models.values())
            except (LLMCallError, RuntimeError) as e:
                # Echec attendu : reseau, ou design toujours invalide apres reparations.
                log_line(log_path, f"ECHEC : {e}")
                failures.append({"prompt": prompt_idx, "iteration": j, "error": str(e)})
            except Exception as e:
                log_line(log_path, f"ECHEC inattendu : {e}")
                failures.append({"prompt": prompt_idx, "iteration": j, "error": str(e)})

    render_all(generated, run_dir)

    if notice_dirs:
        # Lot separe : les images de notice sont rendues avec une marge plus large.
        # L'encart des pieces occupe le coin superieur droit et masquerait
        # l'assemblage dans les dernieres etapes, quand le modele devient grand.
        env_marge = os.environ.copy()
        env_marge["LEGO_RENDER_MARGIN"] = os.environ.get("LEGO_NOTICE_MARGIN", "1.7")
        # Seule l'iso sert dans l'encart et les images d'etapes : rendre les trois
        # autres vues serait du calcul jete (mesure : 3,5x plus lent).
        env_marge["LEGO_RENDER_VIEWS"] = "iso"
        render_all(notice_dirs, run_dir, assemble_dirs=[], env_extra=env_marge,
                   manifest_name="_manifest_notice.txt")

    for (iter_dir, etape_dirs, piece_models, etapes, placements,
         result_report) in notices:
        nom = os.path.basename(os.path.dirname(iter_dir))
        if etape_dirs:
            composees = compose_all_steps(etapes, etape_dirs, piece_models)
            print(f"Images d'etapes composees : {composees}/{len(etapes)}")
        # Nomenclature reecrite avec les vignettes, maintenant qu'elles existent.
        images = {cle: os.path.join(d, "render_iso.png") for cle, d in piece_models.items()}
        write_bom(placements, iter_dir, nom, piece_images=images)
        n = write_notice_html(etapes, etape_dirs or [""] * len(etapes),
                              os.path.join(iter_dir, "notice.html"), nom,
                              rapport=result_report)
        print(f"Notice : {n} etapes -> {os.path.join(iter_dir, 'notice.html')}")
        # Un PDF rate ne doit PAS invalider le modele, qui lui est bien genere.
        try:
            write_pdf(etapes, etape_dirs or [""] * len(etapes), placements, iter_dir, nom,
                      rendu_final=os.path.join(iter_dir, "render_iso.png"),
                      piece_images=images, rapport=result_report)
        except RuntimeError as e:
            print(f"Notice PDF non produite : {e}")

    print(f"\n=========================")
    print(f"TOTAL : {time.time() - total_start:.0f}s")
    print(f"Run : {run_dir}")
    print(f"Modeles generes : {len(generated)}/{total_iterations}")
    print(f"Echecs : {len(failures)}")
    print("=========================")

    if failures:
        summary_path = os.path.join(run_dir, "errors_summary.txt")
        with open(summary_path, "w", encoding="utf-8") as f:
            for fail in failures:
                f.write(f"prompt_{fail['prompt']:03d} / iteration_{fail['iteration']:03d} : "
                        f"{fail['error']}\n")
        print(f"Resume des erreurs : {summary_path}")


# Garde indispensable : sans elle, un simple `from pipeline import generate_one`
# declencherait un run complet de prompts.json. lego.py et agent.py (US-01, US-11)
# doivent pouvoir importer la generation sans la lancer.
if __name__ == "__main__":
    main()
