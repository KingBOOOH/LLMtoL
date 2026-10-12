"""Etape B : design contraint (Phase 3, tache 3.2) + boucle de reparation (tache 3.3).

Appel LLM contraint par le schema DSL (response_format), few-shot multi-tours (les 3
designs de la tache 1.7), temperature basse pour rester proche du schema. La boucle de
reparation est DISTINCTE des retries reseau de src.llm_client (tache 0.1) : elle reagit
a des erreurs SEMANTIQUES (schema invalide, ou design structurellement mauvais d'apres
src.validator) en renvoyant le contexte de l'erreur au LLM — pas un nouvel essai a
l'aveugle. Budget : MAX_REPAIRS reparations, puis abandon avec error.txt.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.compiler import compile_design
from src.dsl_schema import build_schema, resoudre_bornes, validate_design
from src.llm_client import LLMCallError, call_llm
from src.tiler import tile
from src.validator import validate

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
FEWSHOT_DIR = os.path.join(PROMPTS_DIR, "fewshot")

with open(os.path.join(PROMPTS_DIR, "designer_system.txt"), "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

MAX_REPAIRS = 2


def _build_fewshot_messages():
    """Construit les exemples few-shot sous forme de vrais tours user/assistant.

    Un exemple joue en dialogue ("voici une demande, voici le JSON que tu produis")
    aligne nettement mieux qu'un exemple recopie dans le prompt system : le modele
    voit un comportement d'assistant a imiter, pas un texte a lire. Les designs
    proviennent de prompts/fewshot/, tous compiles et verifies visuellement.

    Calcule une seule fois au chargement du module (les fichiers ne bougent pas).
    """
    with open(os.path.join(FEWSHOT_DIR, "manifest.json"), "r", encoding="utf-8") as f:
        manifest = json.load(f)

    messages = []
    for example in manifest:
        with open(os.path.join(FEWSHOT_DIR, example["design_file"]), "r", encoding="utf-8") as f:
            design_json = f.read()
        messages.append({"role": "user", "content": f"{example['prompt']}\n\nPlan :\n{example['plan']}"})
        messages.append({"role": "assistant", "content": design_json.strip()})
    return messages


_FEWSHOT_MESSAGES = _build_fewshot_messages()


def _extract_json(content):
    """Meme logique que src.prompt.extract_json (nettoie markdown/texte autour du JSON)."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return text


def _format_repair_message(errors, warnings):
    """Redige le message de reparation envoye au modele apres un rejet.

    Ne repete volontairement ni le prompt ni le plan : ils sont deja dans l'historique
    de conversation. Le message se limite aux erreurs a corriger, chacune nommant la
    piece et les coordonnees fautives — c'est ce qui rend la reparation ciblee plutot
    qu'un simple nouvel essai.
    """
    lines = ["Ton design precedent a ete rejete par le validateur geometrique.", "", "Erreurs :"]
    lines += [f"- {e}" for e in errors]
    if warnings:
        lines += ["", "Avertissements :"] + [f"- {w}" for w in warnings]
    lines += ["", "Corrige uniquement ces points. Renvoie le design complet corrige."]
    return "\n".join(lines)


def design(user_prompt, plan_text, work_dir, seed=None, taille=None, bbox_max=None):
    """Genere un design DSL, le compile, le pave, le valide ; repare jusqu'a
    MAX_REPAIRS fois sur echec (JSON/schema invalide ou erreurs bloquantes du
    validateur). Retourne {"design", "voxels", "placements", "report", "repairs_used"}.

    taille / bbox_max resserrent les bornes du schema envoye au modele. C'est une
    garantie et non une consigne : le decodage contraint rend le modele INCAPABLE
    d'emettre une coordonnee hors bornes. La borne appliquee est consignee dans
    report.json pour que le resultat reste rejouable.

    Leve LLMCallError (reseau, gere par call_llm) ou RuntimeError (echec apres
    reparations, error.txt ecrit dans work_dir) sinon.
    """
    bornes = resoudre_bornes(taille, bbox_max)
    schema = build_schema(bornes)
    user_message = f"{user_prompt}\n\nPlan :\n{plan_text}"
    extra_messages = list(_FEWSHOT_MESSAGES)
    last_report = None

    for attempt in range(MAX_REPAIRS + 1):
        tag = "designer" if attempt == 0 else f"designer_repair{attempt}"

        content, _metadata = call_llm(
            SYSTEM_PROMPT, user_message, schema=schema, temperature=0.3, top_p=0.9,
            seed=seed, log_dir=work_dir, tag=tag, extra_messages=extra_messages,
        )

        try:
            parsed = json.loads(_extract_json(content))
            validate_design(parsed, schema)
        # Capture large volontaire : tout echec de parsing ou de validation alimente
        # la boucle de reparation, qui renvoie l'erreur au modele. Restreindre la
        # capture laisserait passer une generation cassee au lieu de la faire reparer.
        except Exception as e:  # noqa: BLE001 - voir ci-dessus
            last_report = {"score": 0.0, "errors": [f"JSON/schema invalide : {e}"],
                           "warnings": [], "metrics": {}}
            extra_messages = extra_messages + [
                {"role": "user", "content": user_message}, {"role": "assistant", "content": content}
            ]
            user_message = _format_repair_message(last_report["errors"], [])
            continue

        voxels = compile_design(parsed)
        placements = tile(voxels)
        report = validate(voxels, placements, parsed)
        last_report = report

        if not report["errors"]:
            # Rejouabilite : sans cette trace, impossible de savoir plus tard a quelle
            # echelle le modele a ete contraint.
            report["bbox_max_applique"] = bornes
            report["taille_demandee"] = taille
            with open(os.path.join(work_dir, "design.json"), "w", encoding="utf-8") as f:
                json.dump(parsed, f, indent=2)
            with open(os.path.join(work_dir, "report.json"), "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            return {"design": parsed, "voxels": voxels, "placements": placements,
                    "report": report, "repairs_used": attempt}

        if attempt < MAX_REPAIRS:
            extra_messages = extra_messages + [
                {"role": "user", "content": user_message}, {"role": "assistant", "content": content}
            ]
            user_message = _format_repair_message(report["errors"], report["warnings"])

    error_path = os.path.join(work_dir, "error.txt")
    with open(error_path, "w", encoding="utf-8") as f:
        f.write(f"Echec apres {MAX_REPAIRS} reparations.\n")
        f.write(json.dumps(last_report, indent=2, ensure_ascii=False) if last_report else "Aucun rapport.")
    raise RuntimeError(f"Echec apres {MAX_REPAIRS} reparations. Voir {error_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python -m src.designer "<prompt>" [work_dir] [seed]')
        sys.exit(1)

    prompt_text = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("LEGO_WORK_DIR", os.getcwd())
    seed_arg = int(sys.argv[3]) if len(sys.argv) > 3 else None
    os.makedirs(out_dir, exist_ok=True)

    from src.planner import plan as make_plan

    try:
        plan_text = make_plan(prompt_text, out_dir, seed=seed_arg)
        print("=== PLAN ===\n" + plan_text)
        result = design(prompt_text, plan_text, out_dir, seed=seed_arg)
    except (LLMCallError, RuntimeError) as e:
        print(f"Erreur : {e}")
        sys.exit(1)

    print("\n=== DESIGN ===")
    print(json.dumps(result["design"], indent=2, ensure_ascii=False))
    print(f"\nScore : {result['report']['score']} — reparations utilisees : {result['repairs_used']}")
