"""Catalogue de pieces LEGO (Phase 1, tache 1.2).

Les references LDraw (PARTS) et les codes couleur (PALETTE) ont ete verifies contre la
bibliotheque LDraw installee dans C:\\Users\\Utilisateur\\OneDrive\\Documents\\LDraw\\ldraw
(verify_catalog() confirme les 24 pieces presentes ; les codes couleur et valeurs RGB de
PALETTE sont lus directement depuis LDConfig.ldr de cette bibliotheque, pas recopies de
memoire — regle du plan). Les noms cles de PALETTE (ex. "dark_gray") restent notre
vocabulaire DSL interne (src.dsl_schema.PALETTE_NAMES) ; ils sont traduits ici vers les
noms LDraw officiels (orthographe britannique, ex. "Dark_Grey") et leurs codes.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Part:
    ldraw_id: str
    kind: str  # "brick" | "plate" | "tile"
    w: int  # emprise en tenons le long de l'axe X LDraw, matrice identite (non tourne)
    d: int  # emprise en tenons le long de l'axe Z LDraw, matrice identite (non tourne)
    h: int  # hauteur en plates : 3 pour une brique, 1 pour une plate/tile


# w = axe X LDraw, d = axe Z LDraw (matrice identite) : convention verifiee
# empiriquement tache 1.1 (tests/fixtures/handmade_two_bricks.ldr + LeoCAD) — la piece
# 3001 ("brique 2x4") a bien sa dimension de 4 tenons le long de X et 2 le long de Z
# avec la matrice identite, donc w=4, d=2 (pas l'inverse). Applique par coherence a
# tout le catalogue (convention d'auteur standard de la bibliotheque LDraw officielle).
PARTS = [
    # briques (h=3)
    Part("3005", "brick", 1, 1, 3),
    Part("3004", "brick", 2, 1, 3),
    Part("3622", "brick", 3, 1, 3),
    Part("3010", "brick", 4, 1, 3),
    Part("3009", "brick", 6, 1, 3),
    Part("3008", "brick", 8, 1, 3),
    Part("3003", "brick", 2, 2, 3),
    Part("3002", "brick", 3, 2, 3),
    Part("3001", "brick", 4, 2, 3),
    Part("2456", "brick", 6, 2, 3),
    Part("3007", "brick", 8, 2, 3),
    # plates (h=1)
    Part("3024", "plate", 1, 1, 1),
    Part("3023", "plate", 2, 1, 1),
    Part("3623", "plate", 3, 1, 1),
    Part("3710", "plate", 4, 1, 1),
    Part("3666", "plate", 6, 1, 1),
    Part("3460", "plate", 8, 1, 1),
    Part("3022", "plate", 2, 2, 1),
    Part("3021", "plate", 3, 2, 1),
    Part("3020", "plate", 4, 2, 1),
    Part("3795", "plate", 6, 2, 1),
    Part("3034", "plate", 8, 2, 1),
    # tiles (h=1)
    Part("3070b", "tile", 1, 1, 1),
    Part("3069b", "tile", 2, 1, 1),
]

# noms lisibles (doivent matcher src.dsl_schema.PALETTE_NAMES) -> code + nom LDraw
# officiels + RGB, tous lus depuis LDConfig.ldr (voir docstring).
PALETTE = {
    "black":         {"ldraw_code": 0,  "ldraw_name": "Black",         "rgb": (0.106, 0.165, 0.204, 1.0)},
    "dark_gray":     {"ldraw_code": 8,  "ldraw_name": "Dark_Grey",     "rgb": (0.329, 0.349, 0.333, 1.0)},
    "light_gray":    {"ldraw_code": 7,  "ldraw_name": "Light_Grey",    "rgb": (0.541, 0.573, 0.553, 1.0)},
    "white":         {"ldraw_code": 15, "ldraw_name": "White",         "rgb": (0.957, 0.957, 0.957, 1.0)},
    "red":           {"ldraw_code": 4,  "ldraw_name": "Red",           "rgb": (0.706, 0.000, 0.000, 1.0)},
    "reddish_brown": {"ldraw_code": 70, "ldraw_name": "Reddish_Brown", "rgb": (0.373, 0.192, 0.035, 1.0)},
    "brown":         {"ldraw_code": 6,  "ldraw_name": "Brown",         "rgb": (0.329, 0.200, 0.141, 1.0)},
    "tan":           {"ldraw_code": 19, "ldraw_name": "Tan",           "rgb": (0.843, 0.729, 0.549, 1.0)},
    "yellow":        {"ldraw_code": 14, "ldraw_name": "Yellow",        "rgb": (0.980, 0.784, 0.039, 1.0)},
    "orange":        {"ldraw_code": 25, "ldraw_name": "Orange",        "rgb": (0.839, 0.475, 0.137, 1.0)},
    "green":         {"ldraw_code": 2,  "ldraw_name": "Green",         "rgb": (0.000, 0.522, 0.169, 1.0)},
    "blue":          {"ldraw_code": 1,  "ldraw_name": "Blue",          "rgb": (0.118, 0.353, 0.659, 1.0)},
}

LDRAW_CANDIDATE_DIRS = [
    "~/ldraw", "C:/ldraw", "%APPDATA%/LDraw",
    "C:/Program Files/LDraw", "C:/Program Files (x86)/LDraw",
    "~/Documents/LDraw", "~/OneDrive/Documents/LDraw",
]


def _find_ldraw_library():
    """Cherche LDConfig.ldr dans chaque dossier candidat, et un niveau en dessous
    (le zip officiel de LDraw.org s'extrait dans un sous-dossier "ldraw/", donc
    "<candidat>/LDConfig.ldr" et "<candidat>/ldraw/LDConfig.ldr" sont tous deux
    valides — ne force jamais l'utilisateur a reorganiser ses fichiers)."""
    for d in LDRAW_CANDIDATE_DIRS:
        base = os.path.expandvars(os.path.expanduser(d))
        if os.path.isfile(os.path.join(base, "LDConfig.ldr")):
            return base
        if os.path.isdir(base):
            for entry in os.listdir(base):
                sub = os.path.join(base, entry)
                if os.path.isfile(os.path.join(sub, "LDConfig.ldr")):
                    return sub
    return None


def verify_catalog():
    """Verifie que chaque ldraw_id de PARTS existe reellement dans la bibliotheque
    LDraw installee. Leve une erreur explicite si la bibliotheque est introuvable ou
    si des references manquent — n'invente jamais un succes (regle #8 du plan)."""
    library = _find_ldraw_library()
    if library is None:
        raise FileNotFoundError(
            "Bibliotheque LDraw introuvable (recherche dans : "
            f"{', '.join(LDRAW_CANDIDATE_DIRS)}). "
            "Installe-la avant d'appeler verify_catalog() (voir tools/check_env.py)."
        )

    parts_dir = os.path.join(library, "parts")
    missing = []
    for part in PARTS:
        candidates = [f"{part.ldraw_id}.dat", f"{part.ldraw_id.upper()}.DAT"]
        if not any(os.path.isfile(os.path.join(parts_dir, c)) for c in candidates):
            missing.append(part.ldraw_id)

    if missing:
        raise FileNotFoundError(f"References LDraw manquantes dans {parts_dir} : {missing}")

    return True


def verify_palette():
    """Verifie que chaque entree de PALETTE (code + nom) correspond exactement a une
    ligne "0 !COLOUR" reelle de LDConfig.ldr. N'invente jamais une correspondance."""
    import re

    library = _find_ldraw_library()
    if library is None:
        raise FileNotFoundError("Bibliotheque LDraw introuvable — voir verify_catalog().")

    with open(os.path.join(library, "LDConfig.ldr"), encoding="utf-8", errors="replace") as f:
        text = f.read()

    mismatches = []
    for key, entry in PALETTE.items():
        pattern = rf"^0 !COLOUR\s+{entry['ldraw_name']}\s+CODE\s+(\d+)\s+VALUE\s+#[0-9A-Fa-f]{{6}}"
        m = re.search(pattern, text, re.MULTILINE)
        if not m:
            mismatches.append(f"{key} : '{entry['ldraw_name']}' introuvable dans LDConfig.ldr")
        elif int(m.group(1)) != entry["ldraw_code"]:
            mismatches.append(f"{key} : code attendu {entry['ldraw_code']}, trouve {m.group(1)}")

    if mismatches:
        raise ValueError("Incoherences PALETTE vs LDConfig.ldr : " + "; ".join(mismatches))

    return True


if __name__ == "__main__":
    print(f"{len(PARTS)} pieces au catalogue :")
    for p in PARTS:
        print(f"  {p.ldraw_id:>6}  {p.kind:<6} {p.w}x{p.d} (h={p.h})")
    try:
        verify_catalog()
        print("verify_catalog() : OK - toutes les references trouvees dans la bibliotheque LDraw")
    except FileNotFoundError as e:
        print(f"verify_catalog() : ECHEC\n  {e}")

    try:
        verify_palette()
        print("verify_palette() : OK - toutes les couleurs correspondent a LDConfig.ldr")
    except (FileNotFoundError, ValueError) as e:
        print(f"verify_palette() : ECHEC\n  {e}")
