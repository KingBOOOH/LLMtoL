"""Schema JSON du DSL d'operations volumetriques (Phase 1, tache 1.3).

Le DSL est un programme d'operations, pas une liste de briques : voir section 4.5/5
du plan. Compile ensuite par src/compiler.py en grille de voxels (0 = vide, n = index
couleur), puis pave en vraies pieces par src/tiler.py.

PALETTE_NAMES est notre vocabulaire DSL interne (noms lisibles, pas des codes LDraw).
La correspondance nom -> code/RGB LDraw officiels est dans src/catalog.py (PALETTE),
verifiee contre LDConfig.ldr par verify_palette() — ne pas coder de codes LDraw ici.
"""

PALETTE_NAMES = [
    "black", "dark_gray", "light_gray", "white",
    "red", "reddish_brown", "brown", "tan",
    "yellow", "orange", "green", "blue",
]

# bbox borne max en v1 (section 4.3) : [X, Y, Z] = [48, 48, 60]
BBOX_MAX = [48, 48, 60]

OPS = ["box", "carve", "column", "layer", "cylinder", "sphere", "mirror",
       "translate_group"]


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


POINT = _xyz_schema(BBOX_MAX)
# delta autorise des valeurs negatives (translation dans les deux sens) ; le clamp
# reel contre le bbox de CE design a lieu au compile (tache 1.4), pas ici.
DELTA = {
    "type": "array",
    "prefixItems": [
        {"type": "integer", "minimum": -BBOX_MAX[0], "maximum": BBOX_MAX[0]},
        {"type": "integer", "minimum": -BBOX_MAX[1], "maximum": BBOX_MAX[1]},
        {"type": "integer", "minimum": -BBOX_MAX[2], "maximum": BBOX_MAX[2]},
    ],
    "minItems": 3,
    "maxItems": 3,
}

COLOR = {"type": "string", "enum": PALETTE_NAMES}

BBOX_SCHEMA = {
    "type": "array",
    "prefixItems": [
        {"type": "integer", "minimum": 1, "maximum": BBOX_MAX[0]},
        {"type": "integer", "minimum": 1, "maximum": BBOX_MAX[1]},
        {"type": "integer", "minimum": 1, "maximum": BBOX_MAX[2]},
    ],
    "minItems": 3,
    "maxItems": 3,
}

BOX_OP = {
    "type": "object",
    "required": ["op", "from", "to", "color"],
    "properties": {
        "op": {"const": "box"},
        "from": POINT,
        "to": POINT,
        "color": COLOR,
    },
    "additionalProperties": False,
}

CARVE_OP = {
    "type": "object",
    "required": ["op", "from", "to"],
    "properties": {
        "op": {"const": "carve"},
        "from": POINT,
        "to": POINT,
    },
    "additionalProperties": False,
}

COLUMN_OP = {
    "type": "object",
    "required": ["op", "at", "height", "color"],
    "properties": {
        "op": {"const": "column"},
        "at": POINT,
        "height": {"type": "integer", "minimum": 1, "maximum": BBOX_MAX[2]},
        "color": COLOR,
    },
    "additionalProperties": False,
}

LAYER_OP = {
    "type": "object",
    "required": ["op", "z", "origin", "grid", "color"],
    "properties": {
        "op": {"const": "layer"},
        "z": {"type": "integer", "minimum": 0, "maximum": BBOX_MAX[2]},
        "origin": {
            "type": "array",
            "prefixItems": [
                {"type": "integer", "minimum": 0, "maximum": BBOX_MAX[0]},
                {"type": "integer", "minimum": 0, "maximum": BBOX_MAX[1]},
            ],
            "minItems": 2,
            "maxItems": 2,
        },
        "grid": {
            "type": "array",
            "items": {"type": "string", "pattern": "^[#.]+$"},
            "minItems": 1,
            "maxItems": BBOX_MAX[1],
        },
        "color": COLOR,
    },
    "additionalProperties": False,
}

# cylinder/sphere : le modele DECLARE un volume courbe, il ne le rasterise pas.
# Mesure qui justifie ces deux operations : sur le jeu primitives, les formes
# exprimables en `box` atteignent 0.78 d'IoU, les formes courbes 0.39 pour un temps de
# generation 9x superieur — parce que decrire un cercle en `layer` oblige le modele a
# faire de l'arithmetique voxel de tete. Le DSL pouvait deja les exprimer exactement
# (test aller-retour verrouille a IoU 1.0) : c'est le modele qui echoue, donc on lui
# retire le rasterisage et src.compiler le fait avec src.primitives.
CYLINDER_OP = {
    "type": "object",
    "required": ["op", "at", "diameter", "height", "color"],
    "properties": {
        "op": {"const": "cylinder"},
        "at": POINT,
        "diameter": {"type": "integer", "minimum": 1, "maximum": min(BBOX_MAX[0], BBOX_MAX[1])},
        "height": {"type": "integer", "minimum": 1, "maximum": BBOX_MAX[2]},
        "color": COLOR,
    },
    "additionalProperties": False,
}

SPHERE_OP = {
    "type": "object",
    "required": ["op", "at", "diameter", "color"],
    "properties": {
        "op": {"const": "sphere"},
        "at": POINT,
        "diameter": {"type": "integer", "minimum": 1, "maximum": min(BBOX_MAX)},
        "color": COLOR,
    },
    "additionalProperties": False,
}

MIRROR_OP = {
    "type": "object",
    "required": ["op", "axis"],
    "properties": {
        "op": {"const": "mirror"},
        "axis": {"type": "string", "enum": ["x", "y", "z"]},
    },
    "additionalProperties": False,
}

TRANSLATE_GROUP_OP = {
    "type": "object",
    "required": ["op", "ops", "delta", "count"],
    "properties": {
        "op": {"const": "translate_group"},
        # Volontairement SANS cylinder/sphere : chaque variante ajoutee ici grossit la
        # grammaire que LM Studio compile, et l'inference est deja limitee par le CPU.
        # A ouvrir seulement si une mesure montre qu'une serie de cylindres manque.
        "ops": {
            "type": "array",
            "items": {"anyOf": [BOX_OP, CARVE_OP, COLUMN_OP, LAYER_OP]},
            "minItems": 1,
            "maxItems": 20,
        },
        "delta": DELTA,
        "count": {"type": "integer", "minimum": 1, "maximum": 20},
    },
    "additionalProperties": False,
}

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["name", "bbox", "ops"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 60},
        "bbox": BBOX_SCHEMA,
        "ops": {
            "type": "array",
            "items": {
                "anyOf": [BOX_OP, CARVE_OP, COLUMN_OP, LAYER_OP, CYLINDER_OP, SPHERE_OP,
                          MIRROR_OP, TRANSLATE_GROUP_OP]
            },
            "minItems": 1,
            "maxItems": 60,
        },
    },
    "additionalProperties": False,
}


def validate_design(data):
    """Valide un design DSL contre SCHEMA. Leve jsonschema.ValidationError si invalide."""
    from jsonschema import validate
    validate(instance=data, schema=SCHEMA)
    return data


if __name__ == "__main__":
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(SCHEMA)
    print("OK — schema DSL valide (Draft 2020-12)")
