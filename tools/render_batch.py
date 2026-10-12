"""Rend N modeles en un seul processus Blender (Phase 4, tache 4.3).

Construit le manifeste attendu par src/blender_script.py puis lance Blender une seule
fois. Economise le demarrage de Blender et l'activation de l'add-on a chaque modele :
environ 1,6 s par modele (mesure), soit quelques minutes sur un gros benchmark.

Un modele defectueux est signale puis saute, il n'interrompt pas le lot.

Usage:
    python -m tools.render_batch <dossier>...              # dossiers explicites
    python -m tools.render_batch --glob "eval/results/_work/v1/*/attempt_*"
    python -m tools.render_batch --glob "..." --assemble   # + planches final.png
"""

import argparse
import glob as globlib
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLENDER_SCRIPT = os.path.join(BASE_DIR, "src", "blender_script.py")
IMG_SCRIPT = os.path.join(BASE_DIR, "src", "img_output.py")


def write_manifest(work_dirs, path):
    """Ecrit un dossier par ligne, en chemins absolus Windows.

    Un FICHIER plutot que des arguments : la ligne de commande Windows plafonne a
    ~32 ko, vite atteint avec des chemins longs et beaucoup de modeles. Les chemins
    sont normalises car le Python de Blender est natif Windows et ne comprend pas la
    forme /c/Users/... de Git Bash.
    """
    # Le dossier n'existe pas forcement : depot fraichement clone, ou conteneur ou
    # outputs/ n'arrive que par un montage. Le creer vaut mieux qu'echouer sur le
    # chemin d'un fichier de travail.
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(os.path.abspath(d) + "\n" for d in work_dirs)
    return path


def render(work_dirs, engine=None, samples=None, resolution=None):
    """Lance Blender une fois sur tout le lot. Retourne le code de retour."""
    manifest = write_manifest(work_dirs, os.path.join(BASE_DIR, "outputs", "_batch_manifest.txt"))

    env = os.environ.copy()
    env["LEGO_BATCH_MANIFEST"] = manifest
    env["PYTHONIOENCODING"] = "utf-8"
    if not env.get("LEGO_LDRAW_DIR"):
        from src.catalog import _find_ldraw_library
        env["LEGO_LDRAW_DIR"] = _find_ldraw_library() or ""
    if engine:
        env["LEGO_RENDER_ENGINE"] = engine
    if samples:
        env["LEGO_RENDER_SAMPLES"] = str(samples)
    if resolution:
        env["LEGO_RENDER_RES"] = str(resolution)

    t0 = time.time()
    result = subprocess.run(
        [config.BLENDER_EXE, "--background", "--python", BLENDER_SCRIPT],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    duration = time.time() - t0

    for line in (result.stdout or "").splitlines():
        if line.startswith(("[", "Mode lot", "Rendus termines", "  boite", "  4 rendus",
                            "  IGNORE", "  ECHEC", "ERREUR")):
            print(line)

    print(f"\nDuree totale : {duration:.0f}s pour {len(work_dirs)} modeles "
          f"({duration / max(len(work_dirs), 1):.0f}s par modele)")
    return result.returncode


def assemble(work_dirs):
    """Assemble la planche final.png de chaque dossier ayant ses quatre rendus."""
    done = 0
    for work_dir in work_dirs:
        env = os.environ.copy()
        env["LEGO_WORK_DIR"] = os.path.abspath(work_dir)
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run([sys.executable, IMG_SCRIPT], env=env,
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace", check=False)
        if result.returncode == 0:
            done += 1
    print(f"Planches assemblees : {done}/{len(work_dirs)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dirs", nargs="*", help="dossiers de travail contenant model.ldr")
    parser.add_argument("--glob", help="motif de recherche des dossiers")
    parser.add_argument("--engine", help="cycles (defaut), workbench ou eevee")
    parser.add_argument("--samples", type=int, help="echantillons Cycles")
    parser.add_argument("--res", type=int, help="resolution en pixels")
    parser.add_argument("--assemble", action="store_true",
                        help="assembler aussi les planches final.png")
    args = parser.parse_args()

    targets = list(args.dirs)
    if args.glob:
        targets += [d for d in globlib.glob(args.glob) if os.path.isdir(d)]
    # Ne garder que les dossiers reellement rendables, pour que le compte affiche
    # corresponde au travail reel.
    targets = [d for d in targets if os.path.isfile(os.path.join(d, "model.ldr"))]

    if not targets:
        print("Aucun dossier contenant model.ldr. Rien a faire.")
        sys.exit(1)

    code = render(targets, args.engine, args.samples, args.res)
    if args.assemble:
        assemble(targets)
    sys.exit(code)
