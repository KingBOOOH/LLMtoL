"""Configuration locale : tous les chemins et URL machine-dependants du projet.

Centralises ici pour qu'un changement de poste ne demande qu'une seule edition.
La bibliotheque LDraw fait exception : elle est cherchee automatiquement dans les
emplacements usuels par src.catalog (voir LDRAW_CANDIDATE_DIRS).

Chaque valeur peut etre surchargee par une VARIABLE D'ENVIRONNEMENT, les valeurs
ecrites ici restant les defauts de l'hote Windows. C'est ce qui permet au meme code de
tourner sur l'hote et dans le conteneur de generation (Dockerfile.agent), ou Blender est
sous /opt et LM Studio reste sur l'hote, joint par host.docker.internal.

Verifier que ces valeurs correspondent a la machine : python tools/check_env.py
"""

import os

# Executable Blender, appele en mode --background par pipeline.py.
BLENDER_EXE = os.environ.get(
    "LEGO_BLENDER_EXE",
    r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")

# Navigateur utilise en mode headless pour convertir la notice HTML en PDF.
# Pourquoi un navigateur : notice.html et bom.html existent deja, avec leur mise en page
# et leurs images. Les reecrire dans reportlab serait la meme chose en double, qui
# divergerait. Edge est livre avec Windows, Chrome fait aussi bien : zero dependance
# nouvelle. Le premier chemin existant est retenu.
NAVIGATEURS_PDF = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
]
if os.environ.get("LEGO_NAVIGATEUR_PDF"):
    # En tete de liste, pas a la place : le reste sert encore de secours.
    NAVIGATEURS_PDF.insert(0, os.environ["LEGO_NAVIGATEUR_PDF"])

# Serveur local LM Studio, API compatible OpenAI (endpoint chat completions).
# Depuis un conteneur, "localhost" designerait le conteneur lui-meme : la valeur est
# alors host.docker.internal, fournie par l'environnement (voir docker-compose.yml).
LM_STUDIO_URL = os.environ.get("LM_STUDIO_URL",
                               "http://localhost:1234/v1/chat/completions")

# Modele a utiliser. Doit etre CHARGE dans LM Studio, pas seulement telecharge.
# Attention : le system prompt et le "Structured Output" configures sur ce modele
# dans l'interface LM Studio s'appliquent en fallback silencieux aux requetes API
# qui n'envoient pas leur propre response_format (voir docs/guide-complet.md).
LM_STUDIO_MODEL = os.environ.get("LM_STUDIO_MODEL", "qwen/qwen3-coder-30b")

# Modele VISION du juge visuel (US V2-01), interroge sur la MEME URL que le
# generateur : LM Studio distingue les modeles par le champ `model` de la requete.
# VIDE PAR DEFAUT = juge desactive. C'est voulu : aucune generation ne doit dependre
# d'un modele optionnel, et la valeur n'est pas devinable (elle depend de ce qui est
# telecharge sur le poste).
# Le juge tourne en passe SEPAREE, apres les rendus : les deux modeles n'ont jamais
# besoin d'etre charges en meme temps, ce qui compte sur une machine ou le generateur
# retient deja ~18 Go.
LM_STUDIO_VLM_MODEL = os.environ.get("LEGO_VLM_MODEL", "")
