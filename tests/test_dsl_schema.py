import itertools
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from src.dsl_schema import SCHEMA, validate_design


def test_schema_itself_is_valid():
    Draft202012Validator.check_schema(SCHEMA)


VALID_DESIGNS = [
    {
        "name": "cube_simple",
        "bbox": [4, 4, 4],
        "ops": [
            {"op": "box", "from": [0, 0, 0], "to": [3, 3, 3], "color": "red"},
        ],
    },
    {
        "name": "maison_creuse",
        "bbox": [10, 8, 6],
        "ops": [
            {"op": "box", "from": [0, 0, 0], "to": [9, 7, 5], "color": "tan"},
            {"op": "carve", "from": [1, 1, 1], "to": [8, 6, 5]},
            {"op": "column", "at": [0, 0, 0], "height": 6, "color": "reddish_brown"},
        ],
    },
    {
        "name": "voilier",
        "bbox": [28, 10, 16],
        "ops": [
            {"op": "box", "from": [0, 0, 0], "to": [27, 9, 5], "color": "reddish_brown"},
            {"op": "carve", "from": [2, 2, 2], "to": [25, 7, 5]},
            {"op": "box", "from": [0, 0, 6], "to": [27, 9, 6], "color": "tan"},
            {"op": "box", "from": [8, 2, 7], "to": [15, 7, 11], "color": "white"},
            {"op": "column", "at": [12, 5, 7], "height": 9, "color": "reddish_brown"},
            {
                "op": "layer",
                "z": 12,
                "origin": [8, 2],
                "grid": ["..##..", ".####.", "..##.."],
                "color": "white",
            },
            {"op": "mirror", "axis": "y"},
        ],
    },
]


@pytest.mark.parametrize("design", VALID_DESIGNS, ids=[d["name"] for d in VALID_DESIGNS])
def test_valid_designs_accepted(design):
    validate_design(design)  # ne doit pas lever


def _base_design():
    return {
        "name": "base",
        "bbox": [10, 10, 10],
        "ops": [{"op": "box", "from": [0, 0, 0], "to": [3, 3, 3], "color": "red"}],
    }


def test_rejects_float_coordinate():
    design = _base_design()
    design["ops"][0]["to"] = [3.5, 3, 3]
    with pytest.raises(ValidationError):
        validate_design(design)


def test_rejects_color_outside_palette():
    design = _base_design()
    design["ops"][0]["color"] = "fuchsia"
    with pytest.raises(ValidationError):
        validate_design(design)


def test_rejects_coordinate_outside_global_bbox_max():
    design = _base_design()
    design["ops"][0]["to"] = [999, 3, 3]
    with pytest.raises(ValidationError):
        validate_design(design)


def test_rejects_unknown_op():
    design = _base_design()
    design["ops"].append({"op": "teleport", "from": [0, 0, 0], "to": [1, 1, 1]})
    with pytest.raises(ValidationError):
        validate_design(design)


def test_rejects_too_many_ops():
    design = _base_design()
    op = {"op": "box", "from": [0, 0, 0], "to": [1, 1, 1], "color": "red"}
    design["ops"] = [op] * 61
    with pytest.raises(ValidationError):
        validate_design(design)


def test_build_schema_resserre_toutes_les_bornes():
    """Resserrer le bbox doit resserrer AUSSI les coordonnees, pas seulement la boite.

    Sans ca le modele declarerait une boite de 16 et y poserait des coordonnees de 48 :
    la borne ne garantirait rien.
    """
    from src.dsl_schema import build_schema

    s = build_schema([16, 16, 20])
    assert s["properties"]["bbox"]["prefixItems"][0]["maximum"] == 16
    assert s["properties"]["bbox"]["prefixItems"][2]["maximum"] == 20

    box = s["properties"]["ops"]["items"]["anyOf"][0]
    assert box["properties"]["from"]["prefixItems"][0]["maximum"] == 16
    assert box["properties"]["to"]["prefixItems"][2]["maximum"] == 20

    cylindre = next(o for o in s["properties"]["ops"]["items"]["anyOf"]
                    if o["properties"]["op"].get("const") == "cylinder")
    assert cylindre["properties"]["diameter"]["maximum"] == 16
    assert cylindre["properties"]["height"]["maximum"] == 20


def test_schema_resserre_refuse_un_design_trop_grand():
    import pytest
    from jsonschema import ValidationError

    from src.dsl_schema import build_schema

    trop_grand = {"name": "t", "bbox": [30, 30, 30],
                  "ops": [{"op": "box", "from": [0, 0, 0], "to": [29, 29, 29],
                           "color": "red"}]}
    validate_design(trop_grand)                       # accepte aux bornes globales
    with pytest.raises(ValidationError):
        validate_design(trop_grand, build_schema([16, 16, 20]))


def test_schemas_de_toutes_les_tailles_sont_compilables():
    """Un schema invalide ne serait decouvert qu'au moment de l'appel LLM, trop tard."""
    from jsonschema import Draft202012Validator

    from src.dsl_schema import TAILLES, build_schema

    for bornes in TAILLES.values():
        Draft202012Validator.check_schema(build_schema(bornes))


def test_resoudre_bornes():
    import pytest

    from src.dsl_schema import BBOX_MAX, resoudre_bornes

    assert resoudre_bornes() == BBOX_MAX
    assert resoudre_bornes("petite") == [16, 16, 20]
    assert resoudre_bornes(None, [10, 10, 10]) == [10, 10, 10]
    # bbox_max l'emporte sur taille
    assert resoudre_bornes("petite", [10, 10, 10]) == [10, 10, 10]
    # Un echec net vaut mieux qu'un modele genere a la mauvaise echelle.
    with pytest.raises(ValueError):
        resoudre_bornes("enorme")
    with pytest.raises(ValueError):
        resoudre_bornes(None, [999, 10, 10])
    with pytest.raises(ValueError):
        resoudre_bornes(None, [10, 10])


def test_tailles_de_lapi_et_du_schema_restent_alignees():
    """L'API duplique la liste des tailles (le conteneur ne voit pas src/).

    Ce test garde les deux listes synchronisees : sans lui, ajouter une taille dans
    src/dsl_schema.py donnerait un 400 inexplicable cote web.
    """
    import re

    from src.dsl_schema import TAILLES

    source = Path("server/app.py").read_text(encoding="utf-8")
    bloc = re.search(r"^TAILLES = \{(.+?)\}", source, re.MULTILINE | re.DOTALL).group(1)
    cote_api = set(re.findall(r'"(\w+)"', bloc))
    assert cote_api == set(TAILLES), (
        f"server/app.py connait {sorted(cote_api)}, src/dsl_schema.py {sorted(TAILLES)}")


# ---------- plancher de taille (US V2-03) ----------

def test_sans_taille_nommee_aucun_plancher():
    """Critere d'acceptation : sans option, le comportement est STRICTEMENT inchange."""
    from src.dsl_schema import build_schema, resoudre_plancher

    assert resoudre_plancher() == [1, 1, 1]
    assert resoudre_plancher(None, None, [12, 12, 12]) == [1, 1, 1]
    minima = [p["minimum"] for p in build_schema()["properties"]["bbox"]["prefixItems"]]
    assert minima == [1, 1, 1]


def test_les_intervalles_de_taille_ne_se_recouvrent_pas():
    """Le plancher d'une taille est le plafond de la precedente : « grande » ne peut
    donc plus rendre un modele plus petit que « moyenne ». Mesure qui l'a motive :
    « grande » rendait [16,16,24], [24,24,24], [16,16,12] — jamais plus que « petite »."""
    from src.dsl_schema import TAILLES, resoudre_bornes, resoudre_plancher

    noms = list(TAILLES)
    assert resoudre_plancher(noms[0]) == [1, 1, 1], "la plus petite n'a pas de plancher"
    for precedent, courant in itertools.pairwise(noms):
        assert resoudre_plancher(courant) == resoudre_bornes(precedent)
        assert all(p <= m for p, m in
                   zip(resoudre_plancher(courant), resoudre_bornes(courant)))


def test_le_plancher_se_retrouve_dans_le_schema():
    """C'est une GARANTIE : le decodage contraint doit rendre le modele incapable de
    declarer une boite plus petite, pas seulement le lui deconseiller."""
    from src.dsl_schema import build_schema, resoudre_bornes, resoudre_plancher

    schema = build_schema(resoudre_bornes("grande"), resoudre_plancher("grande"))
    bornes = schema["properties"]["bbox"]["prefixItems"]
    assert [p["minimum"] for p in bornes] == [28, 28, 36]
    assert [p["maximum"] for p in bornes] == [48, 48, 60]


def test_un_bbox_max_plus_petit_rabote_le_plancher():
    """Sinon le schema serait insatisfiable (minimum > maximum) et LM Studio ne
    pourrait pas le compiler en grammaire. Cas reel : --taille grande --bbox-max petit,
    pour une forme elancee."""
    from src.dsl_schema import build_schema, resoudre_plancher

    plancher = resoudre_plancher("grande", bbox_max=[10, 10, 60])
    assert plancher == [10, 10, 36]
    bornes = build_schema([10, 10, 60], plancher)["properties"]["bbox"]["prefixItems"]
    for p in bornes:
        assert p["minimum"] <= p["maximum"]


def test_un_plancher_explicite_prime_sur_la_taille():
    from src.dsl_schema import resoudre_plancher

    assert resoudre_plancher("grande", bbox_min=[4, 4, 40]) == [4, 4, 40]
    with pytest.raises(ValueError, match="bbox_min"):
        resoudre_plancher(bbox_min=[4, 4])
