"""Diagnostic en lecture seule de l'environnement LM Studio / Blender / LDraw.

Ne modifie rien. A lancer avant la Phase 1 (checkpoint STOP de la tache 0.2).
"""

import json
import os
import sys
from urllib.parse import urlsplit, urlunsplit

import requests

# Sortie REDIRIGEE sous Windows = cp1252, qui ne sait pas ecrire le "⚠" des
# avertissements ni un tiret demi-cadratin : le diagnostic plantait au lieu de
# diagnostiquer, et seulement quand on le journalisait. Meme patron que lego.py,
# agent.py et pipeline.py.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from src.catalog import LDRAW_CANDIDATE_DIRS, _find_ldraw_library

TEST_SCHEMA = {
    "type": "object",
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
    "additionalProperties": False,
}

# Marche a suivre affichee quand un reglage parasite est detecte. Le chemin du fichier
# est donne parce que le reglage y survit : le vider dans l'interface ne suffit pas si
# le modele est recharge ensuite.
FIX_OVERRIDE = """\
  LM Studio applique le *System Prompt* et le *Structured Output* configures sur le
  modele a toute requete qui n'envoie pas son propre response_format.
  Corriger : LM Studio > My Models > le modele charge > vider System Prompt ET
  Structured Output.
  ATTENTION : le reglage REVIENT a chaque rechargement du modele. Il est stocke dans
  .lmstudio/.internal/user-concrete-model-default-config/<editeur>/<modele>.json
  (cles llm.prediction.systemPrompt et llm.prediction.structured).
  Verifie : ni un system prompt envoye par la requete, ni un response_format valant
  {"type":"text"} ne le neutralisent. Seul un json_schema explicite prend le dessus —
  donc src/designer.py y resiste, src/planner.py NON."""

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


def detect_default_override(content):
    """Detecte un reglage LM Studio parasite depuis une reponse a une demande de PROSE.

    Fonction pure, sans reseau : c'est elle qui est testee, pas l'appel HTTP.

    Pourquoi ce controle existe : LM Studio reinjecte le system prompt et le
    Structured Output configures sur le modele des qu'une requete n'envoie pas son
    propre response_format. Le symptome est que le modele renvoie une structure JSON
    quel que soit le prompt. Ce piege a coute du temps quatre fois, et le diagnostic
    ne le voyait pas : check_truncation envoyait bien la seule requete vulnerable
    (sans response_format) mais ne regardait que finish_reason, jamais le contenu.

    Retourne un motif explicatif, ou None si la reponse ressemble a de la prose.
    """
    texte = (content or "").strip()
    if not texte:
        return "reponse vide a une demande de texte libre"

    # Une demande de paragraphe en prose qui revient en JSON valide : c'est le
    # Structured Output du modele qui a ete applique par-dessus la requete.
    if texte[0] in "[{":
        try:
            json.loads(texte)
        except ValueError:
            pass  # du texte qui commence par une accolade, pas du JSON : on laisse passer
        else:
            return "la reponse a une demande de prose est un JSON valide"

    # Signature de la chaine v0 supprimee, encore presente dans les reglages du modele.
    # On cherche "action" AVEC ses guillemets (donc une cle JSON) : une prose sur les
    # briques peut mentionner le mot cube, elle n'ecrit pas une cle JSON.
    bas = texte.lower()
    if '"action"' in bas and ('"cube"' in bas or '"rectangle"' in bas):
        return "la reponse reprend le schema action/cube/rectangle de la chaine v0 supprimee"

    return None


def check_truncation():
    """Verifie qu'une reponse se termine d'elle-meme ET qu'aucun reglage ne la detourne.

    Un "length" signale une generation coupee au plafond : le JSON arrive tronque
    et on accuserait le modele a tort.

    Cette requete n'envoie VOLONTAIREMENT pas de response_format : c'est la seule du
    diagnostic a etre vulnerable au piege LM Studio, donc la seule capable de le
    detecter. Son contenu est inspecte par detect_default_override.
    """
    # La demande doit tenir LARGEMENT sous max_tokens, sinon le controle se declenche
    # sur lui-meme. Version precedente : "at least 300 words" sous un plafond de 400
    # jetons — or 300 mots anglais en pesent 375 a 450. Le portail passait ou echouait
    # au hasard (mesure : 373 jetons et "stop" le matin, "length" l'apres-midi, meme
    # modele, meme contexte) et bloquait alors toute generation en accusant a tort la
    # longueur de contexte. ~80 mots laissent une marge de 3x.
    payload = {
        "model": config.LM_STUDIO_MODEL,
        "messages": [
            {"role": "user", "content": "Write a short paragraph of about 80 words about bricks."}
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
    content = (choice.get("message") or {}).get("content") or ""
    override = detect_default_override(content)
    return {
        "ok": finish_reason == "stop" and override is None,
        "finish_reason": finish_reason,
        "completion_tokens": completion_tokens,
        "override": override,
        "content_preview": content.strip()[:160],
    }


def preflight_llm():
    """Controle minimal avant de depenser une generation. Retourne (ok, message).

    Une seule requete, reutilisee de check_truncation : la troncature et le reglage
    parasite se detectent sur la meme reponse. Destine a etre appele par pipeline.py
    (et par lego.py / agent.py quand ils existeront), pas seulement par le diagnostic
    complet — un run long ne doit pas demarrer sur un modele detourne.
    """
    res = check_truncation()
    if res.get("error"):
        return False, f"LM Studio injoignable : {res['error']}"
    if res.get("override"):
        return False, (f"Reglage LM Studio parasite : {res['override']}\n"
                       f"  Reponse recue : {res['content_preview']!r}\n{FIX_OVERRIDE}")
    if res.get("finish_reason") != "stop":
        return False, (f"finish_reason={res['finish_reason']!r} au lieu de 'stop' — "
                       f"reponse coupee a {res.get('completion_tokens')} jetons. Verifier "
                       "le plafond max_tokens de la requete AVANT la longueur de contexte "
                       "du modele : une demande trop longue pour son propre plafond se "
                       "coupe toute seule, contexte intact.")
    return True, "LM Studio repond normalement a une requete non contrainte"


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

    print("\n[3/7] Requete NON contrainte : troncature + reglage parasite...")
    report["truncation"] = check_truncation()
    print(json.dumps(report["truncation"], indent=2, ensure_ascii=False))
    if report["truncation"].get("override"):
        print(f"\n  /!\\ PIEGE LM STUDIO DETECTE : {report['truncation']['override']}")
        print(FIX_OVERRIDE)

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
    # Bloquant et non "action manuelle" : sous ce piege, le planificateur produit du
    # JSON de cubes pour n'importe quel prompt. Lancer un run dans cet etat ne donne
    # pas un resultat degrade, il donne un resultat sans aucun rapport avec la demande.
    if report["truncation"].get("override"):
        blocking_phase0.append(
            f"Reglage LM Studio parasite ({report['truncation']['override']}) — "
            "le planificateur est detourne, voir la marche a suivre ci-dessus")
    elif report["truncation"].get("finish_reason") not in ("stop", None):
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
