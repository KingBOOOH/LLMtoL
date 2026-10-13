"""Juge visuel : un VLM note la ressemblance entre un rendu et la demande (US V2-01).

Comble le trou de mesure du projet. On sait mesurer exactement la validite geometrique
(src.validator) et la ressemblance aux formes primitives (IoU contre une reference
analytique, src.primitives). Entre les deux, rien : sur « un chateau avec quatre
tours », aucun chiffre ne dit si le resultat ressemble a un chateau.

Trois choix a ne pas defaire :

1. ON NE DONNE PAS DE JSON A RELIRE A UN MODELE. Le juge ne voit qu'une IMAGE. Un
   modele texte qui relirait design.json pour verifier la geometrie remplacerait une
   reponse sure et gratuite (src.validator) par une opinion.
2. SORTIE CONTRAINTE PAR SCHEMA. Une note entiere bornee, pas du texte a parser. Ca
   immunise aussi le juge contre le Structured Output herite que LM Studio reinjecte
   dans toute requete sans response_format (voir README).
3. PASSE SEPAREE, APRES LES RENDUS. Le juge ne s'insere pas dans la generation : il
   lit des .png sur disque. Generateur et VLM n'ont donc jamais a etre charges en
   meme temps.

Le juge est OPTIONNEL : sans LEGO_VLM_MODEL, disponible() est faux et rien n'appelle
noter(). Aucune generation ne doit echouer parce qu'un modele facultatif manque.

ATTENTION, A NE PAS OUBLIER A LA CALIBRATION : la sphere du projet est ronde dans la
grille mais APLATIE a l'ecran d'un facteur ~2,5 (un tenon vaut 20 LDU, une plate 8 ;
voir AGENTS.md). La reference d'IoU assume ce choix, un oeil humain ou un VLM non.
Sur prim_sphere, le juge et l'IoU peuvent donc diverger legitimement. Regarder la
correlation avec ET sans cette forme avant de conclure.
"""

import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from src.llm_client import call_llm

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")

with open(os.path.join(PROMPTS_DIR, "juge_vlm_system.txt"), "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

# Note entiere bornee : le modele devient INCAPABLE de rendre 7,5, "bien" ou une note
# sur 100. additionalProperties=False et required complet sont exiges par le mode
# strict de LM Studio.
SCHEMA = {
    "type": "object",
    "properties": {
        "note": {"type": "integer", "minimum": 0, "maximum": 10},
        # maxLength n'est pas cosmetique : sans borne, un petit modele part en
        # repetition degeneree ("le toit est en forme de carre" cinq fois), depasse
        # max_tokens et renvoie un JSON tronque donc inexploitable. Mesure sur
        # Qwen3-VL-2B : 4 notes perdues sur 36. La borne traite la cause.
        "raison": {"type": "string", "maxLength": 200},
    },
    "required": ["note", "raison"],
    "additionalProperties": False,
}

NOM_FICHIER = "juge_vlm.json"

# Empreinte du bareme ayant servi a noter. Elle est ecrite dans chaque juge_vlm.json
# pour que --resume refuse de melanger deux baremes : une note reprise d'un bareme
# precedent se confondrait avec une note du bareme courant, et la correlation
# comparerait deux mesures differentes sans que rien ne le signale. C'est la meme
# famille de piege que le Structured Output que LM Studio reinjecte en silence.
EMPREINTE_BAREME = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]


def disponible():
    """Vrai si un modele vision est configure. Faux = le juge ne tourne pas."""
    return bool(config.LM_STUDIO_VLM_MODEL)


def noter(prompt, image_path, log_dir, model=None, timeout=300):
    """Note la ressemblance de image_path au prompt. Retourne le dict ecrit sur disque.

    Ecrit log_dir/juge_vlm.json : {note, raison, model, image, duree_s}. Fichier a
    part, et non une cle ajoutee a report.json : report.json est le rapport du
    validateur, ecrit au moment du design — donc avant que le rendu existe — et il ne
    doit contenir que des mesures exactes. Une opinion n'y a pas sa place.

    Leve FileNotFoundError si l'image manque, LLMCallError sur panne reseau, et
    ValueError si la reponse n'est pas conforme au schema (ne devrait pas arriver :
    le decodage est contraint, mais un modele servi sans support du schema le ferait).
    """
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Rendu introuvable, rien a noter : {image_path}")

    t0 = time.time()
    contenu, _meta = call_llm(
        SYSTEM_PROMPT,
        f"Demande : {prompt}",
        schema=SCHEMA,
        # 0 et non 0,3 : on veut la meme note pour la meme image d'un run a l'autre,
        # sinon la correlation mesurerait en partie le bruit d'echantillonnage.
        temperature=0.0,
        max_tokens=400,
        log_dir=log_dir,
        tag="juge_vlm",
        model=model or config.LM_STUDIO_VLM_MODEL,
        images=[image_path],
        timeout=timeout,
    )

    try:
        resultat = json.loads(contenu)
        note = int(resultat["note"])
    except (ValueError, KeyError, TypeError) as e:
        # Distinguer les deux pannes : un JSON tronque accuse le budget de tokens ou
        # un modele bavard, un JSON malforme accuse le support du schema. Les
        # confondre enverrait chercher au mauvais endroit.
        cause = ("reponse tronquee (max_tokens atteint)"
                 if _meta.get("finish_reason") == "length" else f"JSON invalide : {e}")
        raise ValueError(f"Reponse du juge inexploitable, {cause}\n{contenu[:300]}") from e

    sortie = {
        "note": note,
        "raison": str(resultat.get("raison", "")),
        "model": model or config.LM_STUDIO_VLM_MODEL,
        "image": os.path.basename(image_path),
        "bareme": EMPREINTE_BAREME,
        "duree_s": round(time.time() - t0, 2),
    }
    with open(os.path.join(log_dir, NOM_FICHIER), "w", encoding="utf-8") as f:
        json.dump(sortie, f, indent=2, ensure_ascii=False)
    return sortie


def note_existante(work_dir, bareme=None):
    """Relit la note deja produite dans work_dir, ou None.

    bareme : si fourni, une note produite par un AUTRE bareme est traitee comme
    absente, donc rejouee. Sans ca, --resume apres une modification du prompt
    melangerait deux baremes dans la meme correlation, en silence. Une note anterieure
    a ce champ est aussi rejouee : on ne sait pas d'ou elle vient.
    """
    chemin = os.path.join(work_dir, NOM_FICHIER)
    if not os.path.isfile(chemin):
        return None
    with open(chemin, "r", encoding="utf-8") as f:
        donnees = json.load(f)
    if bareme is not None and donnees.get("bareme") != bareme:
        return None
    return donnees.get("note")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m src.juge_vlm \"<demande>\" <image.png> [dossier_de_trace]")
        sys.exit(1)
    if not disponible():
        print("LEGO_VLM_MODEL n'est pas defini : aucun modele vision configure.")
        sys.exit(1)
    dossier = sys.argv[3] if len(sys.argv) > 3 else os.path.dirname(os.path.abspath(sys.argv[2]))
    r = noter(sys.argv[1], sys.argv[2], dossier)
    print(f"note {r['note']}/10 en {r['duree_s']}s — {r['raison']}")
