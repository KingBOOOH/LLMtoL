"""Schema JSON du DSL d'operations volumetriques (Phase 1, tache 1.3).

Le DSL est un programme d'operations, pas une liste de briques : voir section 4.5/5
du plan. Compile ensuite par src/compiler.py en grille de voxels (0 = vide, n = index
couleur), puis pave en vraies pieces par src/tiler.py.

PALETTE_NAMES est notre vocabulaire DSL interne (noms lisibles, pas des codes LDraw).
La correspondance nom -> code/RGB LDraw officiels est dans src/catalog.py (PALETTE),
verifiee contre LDConfig.ldr par verify_palette() — ne pas coder de codes LDraw ici.

Le schema est CONSTRUIT (build_schema) plutot que fige, pour pouvoir resserrer les
bornes du bbox par requete : c'est ainsi qu'on borne la taille d'un modele. Demander
"fais environ 200 pieces" dans le prompt ne marche pas — la chaine v0 l'a prouve
("grille 10x10" -> 49 cubes). Un schema resserre, lui, rend le modele INCAPABLE
d'emettre une coordonnee hors bornes : c'est une garantie, pas une consigne.
"""

PALETTE_NAMES = [
    "black", "dark_gray", "light_gray", "white",
    "red", "reddish_brown", "brown", "tan",
    "yellow", "orange", "green", "blue",
]

# bbox borne max en v1 (section 4.3) : [X, Y, Z] = [48, 48, 60]
BBOX_MAX = [48, 48, 60]

# Tailles nommees exposees a l'utilisateur. Valeurs de depart, a ajuster une fois le
# nombre de pieces reellement obtenu mesure pour chacune (cf. README).
TAILLES = {
    "petite": [16, 16, 20],
    "moyenne": [28, 28, 36],
    "grande": BBOX_MAX,
}

OPS = ["box", "carve", "column", "layer", "cylinder", "sphere", "mirror",
       "translate_group"]

COLOR = {"type": "string", "enum": PALETTE_NAMES}


def _xyz_schema(maxima, minimum=0):
    """Schema d'un triplet [x, y, z] d'entiers, borne axe par axe.

    prefixItems fixe le type de chaque position ; minItems=maxItems=3 verrouille la
    longueur. Pas de "items": false ici bien que ce soit la forme canonique en
    Draft 2020-12 : LM Studio echoue a compiler un schema booleen en grammaire
    ("Cannot use 'in' operator to search for 'const' in false"). C'est de toute
    facon redondant, la longueur etant deja figee a 3.
    """
    return {
        "type": "array",
        "prefixItems": [
            {"type": "integer", "minimum": minimum, "maximum": maxima[0]},
            {"type": "integer", "minimum": minimum, "maximum": maxima[1]},
            {"type": "integer", "minimum": minimum, "maximum": maxima[2]},
        ],
        "minItems": 3,
        "maxItems": 3,
    }


def resoudre_bornes(taille=None, bbox_max=None):
    """Retourne les bornes [X, Y, Z] a appliquer. bbox_max l'emporte sur taille.

    Leve ValueError sur une taille inconnue ou une borne aberrante : mieux vaut un
    echec net qu'un modele silencieusement genere a la mauvaise echelle.
    """
    if bbox_max is not None:
        bornes = list(bbox_max)
        if len(bornes) != 3 or any(not isinstance(v, int) or v < 1 for v in bornes):
            raise ValueError(f"bbox_max doit etre 3 entiers >= 1, recu {bbox_max!r}")
        if any(v > m for v, m in zip(bornes, BBOX_MAX)):
            raise ValueError(f"bbox_max {bornes} depasse le maximum global {BBOX_MAX}")
        return bornes
    if taille is None:
        return list(BBOX_MAX)
    if taille not in TAILLES:
        raise ValueError(f"taille inconnue {taille!r}, attendu une de {sorted(TAILLES)}")
    return list(TAILLES[taille])


def build_schema(bbox_max=None):
    """Construit le schema DSL avec les bornes de bbox donnees (defaut : BBOX_MAX).

    Tout est reconstruit a chaque appel parce que les sous-schemas se referencent :
    resserrer POINT sans resserrer BBOX_SCHEMA laisserait le modele declarer une boite
    de 48 et y poser des coordonnees de 48. La borne doit etre coherente partout.
    """
    bornes = list(bbox_max) if bbox_max is not None else list(BBOX_MAX)
    point = _xyz_schema(bornes)

    # delta autorise des valeurs negatives (translation dans les deux sens) ; le clamp
    # reel contre le bbox de CE design a lieu au compile (tache 1.4), pas ici.
    delta = {
        "type": "array",
        "prefixItems": [
            {"type": "integer", "minimum": -bornes[0], "maximum": bornes[0]},
            {"type": "integer", "minimum": -bornes[1], "maximum": bornes[1]},
            {"type": "integer", "minimum": -bornes[2], "maximum": bornes[2]},
        ],
        "minItems": 3,
        "maxItems": 3,
    }

    bbox_schema = {
        "type": "array",
        "prefixItems": [
            {"type": "integer", "minimum": 1, "maximum": bornes[0]},
            {"type": "integer", "minimum": 1, "maximum": bornes[1]},
            {"type": "integer", "minimum": 1, "maximum": bornes[2]},
        ],
        "minItems": 3,
        "maxItems": 3,
    }

    box_op = {
        "type": "object",
        "required": ["op", "from", "to", "color"],
        "properties": {
            "op": {"const": "box"},
            "from": point,
            "to": point,
            "color": COLOR,
        },
        "additionalProperties": False,
    }

    carve_op = {
        "type": "object",
        "required": ["op", "from", "to"],
        "properties": {
            "op": {"const": "carve"},
            "from": point,
            "to": point,
        },
        "additionalProperties": False,
    }

    column_op = {
        "type": "object",
        "required": ["op", "at", "height", "color"],
        "properties": {
            "op": {"const": "column"},
            "at": point,
            "height": {"type": "integer", "minimum": 1, "maximum": bornes[2]},
            "color": COLOR,
        },
        "additionalProperties": False,
    }

    layer_op = {
        "type": "object",
        "required": ["op", "z", "origin", "grid", "color"],
        "properties": {
            "op": {"const": "layer"},
            "z": {"type": "integer", "minimum": 0, "maximum": bornes[2]},
            "origin": {
                "type": "array",
                "prefixItems": [
                    {"type": "integer", "minimum": 0, "maximum": bornes[0]},
                    {"type": "integer", "minimum": 0, "maximum": bornes[1]},
                ],
                "minItems": 2,
                "maxItems": 2,
            },
            "grid": {
                "type": "array",
                "items": {"type": "string", "pattern": "^[#.]+$"},
                "minItems": 1,
                "maxItems": bornes[1],
            },
            "color": COLOR,
        },
        "additionalProperties": False,
    }

    # cylinder/sphere : le modele DECLARE un volume courbe, il ne le rasterise pas.
    # Mesure qui justifie ces deux operations : sur le jeu primitives, les formes
    # courbes passent de 0.391 a 0.619 d'IoU et de 339 s a 131 s de generation —
    # decrire un cercle en `layer` obligeait le modele a faire de l'arithmetique voxel
    # de tete. Le DSL pouvait deja les exprimer exactement (test aller-retour
    # verrouille a IoU 1.0) : c'est le modele qui echouait, donc on lui retire le
    # rasterisage et src.compiler le fait avec src.primitives.
    cylinder_op = {
        "type": "object",
        "required": ["op", "at", "diameter", "height", "color"],
        "properties": {
            "op": {"const": "cylinder"},
            "at": point,
            "diameter": {"type": "integer", "minimum": 1,
                         "maximum": min(bornes[0], bornes[1])},
            "height": {"type": "integer", "minimum": 1, "maximum": bornes[2]},
            "color": COLOR,
        },
        "additionalProperties": False,
    }

    sphere_op = {
        "type": "object",
        "required": ["op", "at", "diameter", "color"],
        "properties": {
            "op": {"const": "sphere"},
            "at": point,
            "diameter": {"type": "integer", "minimum": 1, "maximum": min(bornes)},
            "color": COLOR,
        },
        "additionalProperties": False,
    }

    mirror_op = {
        "type": "object",
        "required": ["op", "axis"],
        "properties": {
            "op": {"const": "mirror"},
            "axis": {"type": "string", "enum": ["x", "y", "z"]},
        },
        "additionalProperties": False,
    }

    translate_group_op = {
        "type": "object",
        "required": ["op", "ops", "delta", "count"],
        "properties": {
            "op": {"const": "translate_group"},
            # Volontairement SANS cylinder/sphere : chaque variante ajoutee ici grossit
            # la grammaire que LM Studio compile, et l'inference est deja limitee par le
            # CPU. A ouvrir seulement si une mesure montre qu'une serie de cylindres
            # manque.
            "ops": {
                "type": "array",
                "items": {"anyOf": [box_op, carve_op, column_op, layer_op]},
                "minItems": 1,
                "maxItems": 20,
            },
            "delta": delta,
            "count": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "additionalProperties": False,
    }

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["name", "bbox", "ops"],
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 60},
            "bbox": bbox_schema,
            "ops": {
                "type": "array",
                "items": {
                    "anyOf": [box_op, carve_op, column_op, layer_op, cylinder_op,
                              sphere_op, mirror_op, translate_group_op]
                },
                "minItems": 1,
                "maxItems": 60,
            },
        },
        "additionalProperties": False,
    }


# Schema par defaut, bornes globales. Reste expose tel quel : c'est ce qu'importent
# src/designer.py et les tests.
SCHEMA = build_schema()


def validate_design(data, schema=None):
    """Valide un design DSL. Leve jsonschema.ValidationError si invalide.

    schema : schema resserre renvoye par build_schema, ou None pour les bornes
    globales. Valider contre le schema resserre est ce qui transforme la taille
    demandee en garantie plutot qu'en voeu.
    """
    from jsonschema import validate
    validate(instance=data, schema=schema if schema is not None else SCHEMA)
    return data


if __name__ == "__main__":
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(SCHEMA)
    print("OK — schema DSL par defaut valide (Draft 2020-12)")
    for nom, bornes in TAILLES.items():
        Draft202012Validator.check_schema(build_schema(bornes))
        print(f"OK — schema '{nom}' {bornes} valide")
