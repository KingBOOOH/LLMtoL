"""Generation v0 : demande directement au LLM une liste de cubes (pas de vraies pieces).

Script, pas module : s'execute en sous-processus depuis pipeline.py ou eval/run_eval.py,
avec le prompt en premier argument et une seed optionnelle en second. Ecrit model.json
dans le dossier designe par la variable d'environnement LEGO_WORK_DIR.

Approche conservee comme reference de comparaison mesuree face a la chaine v1 (DSL
contraint). Sa limite est structurelle : le modele place des cubes un par un, sans rien
qui garantisse que le resultat tienne debout — d'ou les membres detaches mesures sur les
prompts de creatures.

Trois tentatives en cas de JSON invalide. Elles sont a l'aveugle (meme requete rejouee),
contrairement a la boucle de reparation de src.designer qui, elle, renvoie l'erreur au
modele.
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from src.llm_client import call_llm, LLMCallError

WORK_DIR = os.environ.get("LEGO_WORK_DIR", os.getcwd())

# =========================
# SCHEMA
# =========================

SCHEMA = {
    "type": "object",
    "required": ["bricks"],
    "properties": {
        "bricks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["type", "position", "size", "color"],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["cube"]
                    },
                    "position": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": -50, "maximum": 50},
                        "minItems": 3,
                        "maxItems": 3
                    },
                    "size": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1, "maximum": 10},
                        "minItems": 3,
                        "maxItems": 3
                    },
                    "color": {
                        "type": "string",
                        "minLength": 1
                    }
                },
                "additionalProperties": False
            }
        }
    },
    "additionalProperties": False
}

SYSTEM_PROMPT = (
    "You are a LEGO structure generator.\n"
    "You must respond with ONLY a valid JSON object. "
    "No text before or after. No markdown. No code blocks. No explanation.\n\n"
    "You must use EXACTLY this JSON structure:\n"
    "{\n"
    "  \"bricks\": [\n"
    "    {\"type\": \"cube\", \"position\": [0, 0, 0], \"size\": [1, 1, 1], \"color\": \"red\"},\n"
    "    {\"type\": \"cube\", \"position\": [2, 0, 0], \"size\": [1, 1, 1], \"color\": \"blue\"}\n"
    "  ]\n"
    "}\n\n"
    "Required fields for every brick:\n"
    "- \"type\": always the string \"cube\"\n"
    "- \"position\": [x, y, z] — three INTEGERS (no decimals)\n"
    "- \"size\": [w, h, d] — three strictly positive INTEGERS (no decimals)\n"
    "- \"color\": a color name such as \"red\", \"blue\", \"green\", \"yellow\", \"orange\", \"white\", \"black\"\n\n"
    "The \"bricks\" list must not be empty.\n"
    "Do not add any other fields. Do not output anything except the JSON object."
)

# =========================
# EXTRACTION
# =========================

def extract_json(content):
    """Nettoie la réponse brute du LLM pour isoler l'objet JSON qu'elle contient
    (retire les blocs markdown ```json ... ``` et le texte avant/après l'objet)."""
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

# =========================
# VALIDATION
# =========================

def parse_and_validate(content):
    """Parse le JSON et le valide contre SCHEMA.

    Leve une exception au message lisible (repris tel quel dans error.txt et dans
    la boucle de tentatives), plutot que de laisser remonter la trace brute.
    """
    try:
        data = json.loads(content)
    except Exception as e:
        raise Exception(f"❌ JSON invalide : {e}")

    from jsonschema import validate, ValidationError
    try:
        validate(instance=data, schema=SCHEMA)
    except ValidationError as e:
        raise Exception(f"❌ JSON Schema invalide : {e.message}")

    return data

# =========================
# MAIN
# =========================

if len(sys.argv) < 2:
    raise Exception("❌ Aucun prompt fourni")

user_prompt = sys.argv[1]
seed = int(sys.argv[2]) if len(sys.argv) > 2 else None

SCHEMA_REMINDER = (
    "CRITICAL FORMAT REQUIREMENT — read before responding:\n"
    "FORBIDDEN: Do NOT start with '['. Do NOT use 'action' field. Do NOT use 'object' field.\n"
    "FORBIDDEN: Do NOT use decimal numbers (no 1.5, no 0.0). All position/size values are INTEGERS.\n"
    "REQUIRED: Your response must start with '{' and end with '}'.\n"
    "Use EXACTLY this schema:\n"
    "{\n"
    "  \"bricks\": [\n"
    "    {\"type\": \"cube\", \"position\": [0, 0, 0], \"size\": [1, 1, 1], \"color\": \"red\"}\n"
    "  ]\n"
    "}\n\n"
    "Task:\n"
)

MAX_RETRIES = 3

parsed = None
last_error = None

for attempt in range(1, MAX_RETRIES + 1):
    print(f"\n✅ Fetching from LM Studio (tentative {attempt}/{MAX_RETRIES})...")

    try:
        content, metadata = call_llm(
            SYSTEM_PROMPT,
            SCHEMA_REMINDER + user_prompt,
            schema=SCHEMA,
            temperature=0.2,
            seed=seed,
            log_dir=WORK_DIR,
            tag="prompt",
            raw_filename="raw_response.txt",
        )
    except LLMCallError as e:
        last_error = str(e)
        print(f"❌ {last_error}")
        continue

    print(f"\n📄 Réponse brute sauvegardée : {os.path.join(WORK_DIR, 'raw_response.txt')}")

    usage = metadata.get("usage", {})
    print("\n=========================")
    print("📊 TOKEN STATS")
    print(f"🧾 Input tokens   : {usage.get('prompt_tokens', 'N/A')}")
    print(f"📦 Output tokens  : {usage.get('completion_tokens', 'N/A')}")
    print(f"📊 Total tokens   : {usage.get('total_tokens', 'N/A')}")
    print("=========================")

    cleaned = extract_json(content)

    try:
        parsed = parse_and_validate(cleaned)
        print(f"\n✅ JSON conforme au schema (tentative {attempt}/{MAX_RETRIES})")
        break
    except Exception as e:
        last_error = str(e)
        print(f"⚠️ Tentative {attempt}/{MAX_RETRIES} échouée : {last_error}")
        parsed = None

if parsed is None:
    error_path = os.path.join(WORK_DIR, "error.txt")
    with open(error_path, "w", encoding="utf-8") as f:
        f.write(f"Echec apres {MAX_RETRIES} tentatives.\nDerniere erreur : {last_error}\n")
    print(f"\n❌ Abandon apres {MAX_RETRIES} tentatives. Voir {error_path}")
    sys.exit(1)

output_path = os.path.join(WORK_DIR, "model.json")
with open(output_path, "w") as f:
    json.dump(parsed, f, indent=2)

print(f"\n📁 Exporté vers {output_path}")
