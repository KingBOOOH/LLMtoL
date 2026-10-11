"""Configuration locale : tous les chemins et URL machine-dependants du projet.

Centralises ici pour qu'un changement de poste ne demande qu'une seule edition.
La bibliotheque LDraw fait exception : elle est cherchee automatiquement dans les
emplacements usuels par src.catalog (voir LDRAW_CANDIDATE_DIRS).

Verifier que ces valeurs correspondent a la machine : python tools/check_env.py
"""

# Executable Blender, appele en mode --background par pipeline.py.
BLENDER_EXE = r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"

# Navigateur utilise en mode headless pour convertir la notice HTML en PDF.
# Pourquoi un navigateur : notice.html et bom.html existent deja, avec leur mise en page
# et leurs images. Les reecrire dans reportlab serait la meme chose en double, qui
# divergerait. Edge est livre avec Windows, Chrome fait aussi bien : zero dependance
# nouvelle. Le premier chemin existant est retenu ; dans un conteneur, remplacer par
# /usr/bin/chromium suffit.
NAVIGATEURS_PDF = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
]

# Serveur local LM Studio, API compatible OpenAI (endpoint chat completions).
LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"

# Modele a utiliser. Doit etre CHARGE dans LM Studio, pas seulement telecharge.
# Attention : le system prompt et le "Structured Output" configures sur ce modele
# dans l'interface LM Studio s'appliquent en fallback silencieux aux requetes API
# qui n'envoient pas leur propre response_format (voir README).
LM_STUDIO_MODEL = "qwen/qwen3-coder-30b"
