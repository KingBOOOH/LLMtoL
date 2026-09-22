"""Etape A : planification (Phase 3, tache 3.1). Appel LLM libre (pas de response_format),
temperature elevee pour favoriser la reflexion spatiale avant de figer quoi que ce soit.
Sortie : texte structure sauvegarde en plan.md. Aucune validation geometrique ici — c'est
le role de designer.py + validator.py en aval.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.llm_client import LLMCallError, call_llm

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")

with open(os.path.join(PROMPTS_DIR, "planner_system.txt"), "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()


def plan(user_prompt, work_dir, seed=None):
    """Retourne le texte du plan et le sauvegarde dans work_dir/plan.md.
    Leve LLMCallError en cas d'echec reseau/HTTP apres retries (gere par call_llm)."""
    content, _metadata = call_llm(
        SYSTEM_PROMPT,
        user_prompt,
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
