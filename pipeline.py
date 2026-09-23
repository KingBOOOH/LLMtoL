"""Orchestrateur de la chaine v0 : prompts.json -> LM Studio -> Blender -> image finale.

Enchaine les trois scripts (src/prompt.py, src/blender_script.py, src/img_output.py)
en sous-processus, chacun recevant son dossier de travail par la variable
d'environnement LEGO_WORK_DIR.

Deux garanties :
- une iteration qui echoue est journalisee et n'interrompt PAS les suivantes ;
- chaque execution ecrit dans outputs/run_<horodatage>/, donc deux runs successifs
  ne s'ecrasent jamais.

La chaine v1 (DSL contraint, vraies pieces LEGO) ne passe pas par ce fichier :
voir src/designer.py et tools/build_from_design.py.
"""

import os
import time
import subprocess
import json
import sys

# A faire AVANT tout print : sortie redirigee (run en arriere-plan), Python utilise
# sinon l'encodage Windows par defaut (cp1252), qui ne sait pas ecrire les emojis
# ci-dessous et fait planter le script des la premiere ligne affichee.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src")
OUTPUTS_DIR = os.path.join(BASE_DIR, "outputs")

PROMPT_SCRIPT = os.path.join(SRC_DIR, "prompt.py")
BLENDER_SCRIPT = os.path.join(SRC_DIR, "blender_script.py")
IMG_SCRIPT = os.path.join(SRC_DIR, "img_output.py")

PROMPTS_FILE = os.path.join(BASE_DIR, "prompts.json")

if not os.path.exists(PROMPTS_FILE):
    raise Exception("❌ prompts.json introuvable")

with open(PROMPTS_FILE, "r") as f:
    prompts_data = json.load(f)

RUN_DIR = os.path.join(OUTPUTS_DIR, f"run_{time.strftime('%Y%m%d_%H%M%S')}")
os.makedirs(RUN_DIR, exist_ok=True)
print(f"📁 Run : {RUN_DIR}")

total_start = time.time()
failures = []
total_iterations = 0


def log_line(log_path, msg):
    """Journalise une ligne horodatee a l'ecran ET dans le log.txt de l'iteration.

    Le double envoi permet de diagnostiquer apres coup un run lance en arriere-plan,
    dont la sortie console est perdue.
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


for prompt_idx, prompt_data in enumerate(prompts_data):

    prompt_text = prompt_data["prompt"]
    iterations = prompt_data.get("iterations", 1)
    prompt_dir = os.path.join(RUN_DIR, f"prompt_{prompt_idx + 1:03d}")
    os.makedirs(prompt_dir, exist_ok=True)

    for j in range(iterations):
        total_iterations += 1

        iter_dir = os.path.join(prompt_dir, f"iteration_{j + 1:03d}")
        os.makedirs(iter_dir, exist_ok=True)

        log_path = os.path.join(iter_dir, "log.txt")

        with open(os.path.join(iter_dir, "prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt_text)

        print(f"\n=========================")
        print(f"🚀 PROMPT {prompt_idx + 1} : {prompt_text[:60]}...")
        print(f"🔁 ITERATION {j + 1}/{iterations}")
        print(f"📁 {iter_dir}")
        print(f"=========================\n")

        env = os.environ.copy()
        env["LEGO_WORK_DIR"] = iter_dir
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            # =========================
            # PROMPT
            # =========================

            log_line(log_path, "🤖 Lancement prompt.py...")
            t0 = time.time()
            subprocess.run(["python", PROMPT_SCRIPT, prompt_text], env=env, check=True)
            log_line(log_path, f"⏱️ LM STUDIO : {time.time() - t0:.2f}s")

            # =========================
            # BLENDER
            # =========================

            log_line(log_path, "🎬 Lancement Blender...")
            t2 = time.time()
            subprocess.run([
                config.BLENDER_EXE,
                "--background",
                "--python",
                BLENDER_SCRIPT
            ], env=env, check=True)
            log_line(log_path, f"⏱️ BLENDER : {time.time() - t2:.2f}s")

            # =========================
            # IMAGE OUTPUT
            # =========================

            log_line(log_path, "🖼️ Assemblage image finale...")
            subprocess.run(["python", IMG_SCRIPT], env=env, check=True)

            log_line(log_path, "✅ ITERATION OK")

        except subprocess.CalledProcessError as e:
            error_msg = f"❌ ITERATION ECHOUEE (commande : {' '.join(str(c) for c in e.cmd)}, code retour {e.returncode})"
            log_line(log_path, error_msg)
            failures.append({"prompt": prompt_idx + 1, "iteration": j + 1, "error": error_msg})
            continue
        except Exception as e:
            error_msg = f"❌ ITERATION ECHOUEE (erreur inattendue) : {e}"
            log_line(log_path, error_msg)
            failures.append({"prompt": prompt_idx + 1, "iteration": j + 1, "error": error_msg})
            continue

# =========================
# TOTAL
# =========================

print(f"\n=========================")
print(f"🚀 TOTAL PIPELINE : {time.time() - total_start:.2f}s")
print(f"📁 Run : {RUN_DIR}")
print(f"✅ Itérations réussies : {total_iterations - len(failures)}/{total_iterations}")
print(f"❌ Itérations échouées : {len(failures)}")
print("=========================")

if failures:
    summary_path = os.path.join(RUN_DIR, "errors_summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        for fail in failures:
            f.write(f"prompt_{fail['prompt']:03d} / iteration_{fail['iteration']:03d} : {fail['error']}\n")
    print(f"📄 Résumé des erreurs : {summary_path}")
