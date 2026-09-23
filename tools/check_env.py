"""Diagnostic en lecture seule de l'environnement LM Studio / Blender / LDraw.

Ne modifie rien. A lancer avant la Phase 1 (checkpoint STOP de la tache 0.2).
"""

import json
import os
import sys
from urllib.parse import urlsplit, urlunsplit

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from src.catalog import LDRAW_CANDIDATE_DIRS, _find_ldraw_library

TEST_SCHEMA = {
    "type": "object",
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
    "additionalProperties": False,
}

BLENDER_ADDON_DIRS = [
    os.path.expandvars(r"%APPDATA%\Blender Foundation\Blender\5.1\scripts\addons"),
    os.path.expandvars(r"%APPDATA%\Blender Foundation\Blender\5.1\extensions"),
]


VIEWER_DIRS = [r"C:\Program Files", r"C:\Program Files (x86)"]
VIEWER_NAMES = ["leocad", "ldview", "lpub"]


def base_url():
    """Derive l'URL racine du serveur depuis l'endpoint configure."""
    parts = urlsplit(config.LM_STUDIO_URL)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def check_models():
    """Liste les modeles connus de LM Studio (API compatible OpenAI)."""
    url = base_url() + "/v1/models"
    try:
        r = requests.get(url, timeout=5)
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}"}
    return {"ok": True, "data": r.json()}


def check_model_details():
    """Quantification et longueur de contexte du modele CHARGE.

    Passe par /api/v0/models, API propre a LM Studio : l'endpoint OpenAI standard
    n'expose pas ces champs. Absent sur les versions plus anciennes, d'ou le
    drapeau "exposed" plutot qu'une valeur inventee.
    """
    url = base_url() + "/api/v0/models"
    try:
        r = requests.get(url, timeout=5)
    except requests.RequestException as e:
        return {"ok": False, "error": str(e), "exposed": False}
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}", "exposed": False}
    try:
        data = r.json()
    except ValueError as e:
        return {"ok": False, "error": str(e), "exposed": False}

    loaded = [m for m in data.get("data", []) if m.get("state") == "loaded"]
    if not loaded:
        return {"ok": False, "error": "aucun modele charge", "exposed": True}

    m = loaded[0]
    return {
        "ok": True,
        "exposed": True,
        "id": m.get("id"),
        "quantization": m.get("quantization"),
        "max_context_length": m.get("max_context_length"),
        "loaded_context_length": m.get("loaded_context_length"),
    }


def check_truncation():
    """Verifie qu'une reponse se termine d'elle-meme (finish_reason == "stop").

    Un "length" signale une generation coupee au plafond : le JSON arrive tronque
    et on accuserait le modele a tort.
    """
    payload = {
        "model": config.LM_STUDIO_MODEL,
        "messages": [
            {"role": "user", "content": "Write a random paragraph of at least 300 words about bricks."}
        ],
        "max_tokens": 400,
        "temperature": 0.5,
    }
    try:
        r = requests.post(config.LM_STUDIO_URL, json=payload, timeout=120)
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
    result = r.json()
    choice = (result.get("choices") or [{}])[0]
    finish_reason = choice.get("finish_reason")
    completion_tokens = result.get("usage", {}).get("completion_tokens")
    return {
        "ok": finish_reason == "stop",
        "finish_reason": finish_reason,
        "completion_tokens": completion_tokens,
    }


def check_response_format():
    """Verifie que le decodage contraint par schema fonctionne sur ce modele.

    C'est le mecanisme dont depend tout l'alignement du projet : s'il ne repond
    pas, rien en aval ne tient.
    """
    payload = {
        "model": config.LM_STUDIO_MODEL,
        "messages": [{"role": "user", "content": "Return ok=true."}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "check_env_test", "strict": True, "schema": TEST_SCHEMA},
        },
        "max_tokens": 50,
    }
    try:
        r = requests.post(config.LM_STUDIO_URL, json=payload, timeout=60)
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
    result = r.json()
    content = (result.get("choices") or [{}])[0].get("message", {}).get("content", "")
    try:
        parsed = json.loads(content)
    except ValueError as e:
        return {"ok": False, "error": f"contenu non-JSON : {e}", "content": content}
    return {"ok": "ok" in parsed, "content": content}


def check_blender():
    """Verifie que l'executable Blender existe au chemin de config.py."""
    return {"path": config.BLENDER_EXE, "exists": os.path.exists(config.BLENDER_EXE)}


def check_ldraw_addon():
    """Cherche l'add-on ImportLDraw dans les dossiers d'extensions de Blender."""
    found = [d for d in BLENDER_ADDON_DIRS if os.path.isdir(d)]
    ldraw_hits = []
    for d in found:
        for name in os.listdir(d):
            if "ldraw" in name.lower():
                ldraw_hits.append(os.path.join(d, name))
    return {"searched": BLENDER_ADDON_DIRS, "dirs_existing": found, "ldraw_found": ldraw_hits}


def check_ldraw_library():
    """Localise la bibliotheque de pieces LDraw (delegue a src.catalog)."""
    found = _find_ldraw_library()
    return {"searched": LDRAW_CANDIDATE_DIRS, "found": [found] if found else []}


def check_viewers():
    """Cherche un visualiseur LDraw installe (LeoCAD, LDView, LPub3D)."""
    found = []
    for base in VIEWER_DIRS:
        if not os.path.isdir(base):
            continue
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for entry in entries:
            low = entry.lower()
            if any(name in low for name in VIEWER_NAMES):
                found.append(os.path.join(base, entry))
    return {"searched": VIEWER_DIRS, "found": found}


def main():
    """Execute tous les controles et resume ce qui bloque quoi."""
    report = {}

    print("=" * 60)
    print("DIAGNOSTIC ENVIRONNEMENT — lecture seule, rien n'est modifie")
    print("=" * 60)

    print("\n[1/7] Modeles LM Studio (/v1/models)...")
    report["models"] = check_models()
    print(json.dumps(report["models"], indent=2, ensure_ascii=False)[:800])

    print("\n[2/7] Details du modele charge (/api/v0/models)...")
    report["model_details"] = check_model_details()
    print(json.dumps(report["model_details"], indent=2, ensure_ascii=False))
    md = report["model_details"]
    if md.get("exposed") and md.get("loaded_context_length"):
        if md["loaded_context_length"] < 16384:
            print(f"  ⚠ ATTENTION : loaded_context_length={md['loaded_context_length']} < 16384 "
                  f"— a augmenter manuellement dans l'interface LM Studio avant la Phase 1.")
        if md.get("quantization") and md["quantization"].upper().startswith("Q4"):
            print(f"  ⚠ ATTENTION : quantification {md['quantization']} (<= Q4) "
                  f"— peut degrader la fidelite geometrique, a evaluer manuellement.")
    elif not md.get("exposed"):
        print("  (non expose par cette version/API de LM Studio — verifier manuellement dans l'interface)")

    print("\n[3/7] Test de troncature (300 tokens demandes)...")
    report["truncation"] = check_truncation()
    print(json.dumps(report["truncation"], indent=2, ensure_ascii=False))

    print("\n[4/7] Test response_format json_schema...")
    report["response_format"] = check_response_format()
    print(json.dumps(report["response_format"], indent=2, ensure_ascii=False))

    print("\n[5/7] Blender (config.BLENDER_EXE)...")
    report["blender"] = check_blender()
    print(json.dumps(report["blender"], indent=2, ensure_ascii=False))

    print("\n[6/7] Add-on ImportLDraw...")
    report["ldraw_addon"] = check_ldraw_addon()
    print(json.dumps(report["ldraw_addon"], indent=2, ensure_ascii=False))

    print("\n[7/8] Bibliotheque de pieces LDraw (LDConfig.ldr)...")
    report["ldraw_library"] = check_ldraw_library()
    print(json.dumps(report["ldraw_library"], indent=2, ensure_ascii=False))

    print("\n[8/8] Viewers LDraw (LeoCAD / LDView / LPub3D)...")
    report["viewers"] = check_viewers()
    print(json.dumps(report["viewers"], indent=2, ensure_ascii=False))

    print("\n" + "=" * 60)
    print("RESUME")
    print("=" * 60)
    blocking_phase0 = []
    manual_actions = []

    if not report["models"].get("ok"):
        blocking_phase0.append("LM Studio ne repond pas sur /v1/models")
    if not report["truncation"].get("ok"):
        manual_actions.append("finish_reason != 'stop' sur un test de 300 tokens — risque de troncature silencieuse")
    if not report["response_format"].get("ok"):
        manual_actions.append("response_format json_schema non fonctionnel avec le modele charge")
    if not report["blender"].get("exists"):
        blocking_phase0.append(f"Blender introuvable a {report['blender']['path']}")
    if not report["ldraw_addon"].get("ldraw_found"):
        manual_actions.append("Add-on ImportLDraw absent — a installer avant la Phase 1/4")
    if not report["ldraw_library"].get("found"):
        manual_actions.append("Bibliotheque de pieces LDraw (LDConfig.ldr) absente — a installer avant la Phase 1")
    if not report["viewers"].get("found"):
        manual_actions.append("Aucun viewer LDraw (LeoCAD/LDView/LPub3D) trouve — necessaire pour les checkpoints STOP 1.1/1.6")

    print(f"Bloquant pour la Phase 0 : {blocking_phase0 or 'aucun'}")
    print("Actions manuelles avant la Phase 1 :")
    if manual_actions:
        for a in manual_actions:
            print(f"  - {a}")
    else:
        print("  (aucune)")

    return report


if __name__ == "__main__":
    main()
