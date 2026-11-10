"""Etape A : planification (Phase 3, tache 3.1). Appel LLM libre (pas de response_format),
temperature elevee pour favoriser la reflexion spatiale avant de figer quoi que ce soit.
Sortie : texte structure sauvegarde en plan.md. Aucune validation geometrique ici — c'est
le role de designer.py + validator.py en aval.
"""

import os
import sys

from src.dsl_schema import resoudre_bornes, resoudre_plancher
from src.llm_client import LLMCallError, call_llm

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")

with open(os.path.join(PROMPTS_DIR, "planner_system.txt"), "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()


def plan(user_prompt, work_dir, seed=None, taille=None, bbox_max=None,
         bbox_min=None):
    """Retourne le texte du plan et le sauvegarde dans work_dir/plan.md.

    taille / bbox_max : la borne est AUSSI rappelee au planificateur, pas seulement
    imposee au designer. Sans ca il proposerait une boite de 40 que le schema
    resserre refuserait ensuite, et le designer passerait son budget de reparations
    a rattraper un plan infaisable.

    Leve LLMCallError en cas d'echec reseau/HTTP apres retries (gere par call_llm)."""
    message = user_prompt
    if taille is not None or bbox_max is not None:
        bx, by, bz = resoudre_bornes(taille, bbox_max)
        px, py, pz = resoudre_plancher(taille, bbox_min, bbox_max)
        contrainte = (f"Contrainte de taille imperative : la boite englobante ne doit "
                      f"pas depasser {bx} tenons (X) x {by} tenons (Y) x {bz} plates (Z).")
        if max(px, py, pz) > 1:
            # Le plancher doit etre dit AU PLANIFICATEUR, pas seulement impose au
            # designer : son prompt systeme lui souffle de preferer des tailles modestes,
            # et un plan trop petit ferait passer au designer son budget de reparations a
            # rattraper une boite que le schema refuse. Symetrique exact du plafond, pour
            # la meme raison.
            contrainte += (f" Elle doit aussi mesurer AU MOINS {px} x {py} x {pz} : la "
                           f"consigne de preferer des tailles modestes ne s'applique pas "
                           f"ici, l'utilisateur a demande cette echelle explicitement.")
        message = f"{user_prompt}\n\n{contrainte}"

    content, _metadata = call_llm(
        SYSTEM_PROMPT,
        message,
        schema=None,
        temperature=0.7,
        seed=seed,
        log_dir=work_dir,
        tag="planner",
    )

    with open(os.path.join(work_dir, "plan.md"), "w", encoding="utf-8") as f:
        f.write(content)

    return content


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python -m src.planner "<prompt>" [work_dir] [seed]')
        sys.exit(1)

    prompt_text = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("LEGO_WORK_DIR", os.getcwd())
    seed_arg = int(sys.argv[3]) if len(sys.argv) > 3 else None

    try:
        result = plan(prompt_text, out_dir, seed=seed_arg)
    except LLMCallError as e:
        print(f"Erreur : {e}")
        sys.exit(1)

    print(result)
