import os
import sys

import pytest
from jsonschema import Draft202012Validator, ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
