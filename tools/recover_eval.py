"""Reconstruit un CSV d'evaluation depuis les design.json deja ecrits sur disque.

Utile quand un run est interrompu (la pression memoire a tue trois runs dans cette
session) : les appels LLM sont la partie couteuse et ils sont deja faits, seules les
metriques agregees sont perdues. Tout ce qui est recalcule ici est deterministe
(compile + pave + valide), aucun appel LLM.

Les colonnes non reconstructibles sont marquees comme telles : duree reconstituee
depuis les *_meta.json, repairs_used deduit de la presence des fichiers de reparation.

Usage: python -m tools.recover_eval eval/results/_work/v1 v1_recovered
"""

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.compiler import compile_design
from src.primitives import build_reference, voxel_iou
from src.tiler import tile
from src.validator import validate

EVAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval")
RESULTS_DIR = os.path.join(EVAL_DIR, "results")

FIELDNAMES = ["id", "category", "complexity", "attempt", "seed", "valid_json",
              "duration_s", "n_pieces", "score", "n_components", "fill_rate",
              "repairs_used", "iou"]


def _load_testset_index():
    """Indexe les deux jeux de test par id, pour retrouver categorie et reference."""
    index = {}
    for name in ("testset.json", "primitives_testset.json"):
        path = os.path.join(EVAL_DIR, name)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            for case in json.load(f):
                index[case["id"]] = case
    return index


def _sum_durations(attempt_dir):
    """Reconstitue la duree totale en sommant les *_meta.json des appels LLM."""
    total = 0.0
    for entry in os.listdir(attempt_dir):
        if entry.endswith("_meta.json"):
            with open(os.path.join(attempt_dir, entry), "r", encoding="utf-8") as f:
                total += json.load(f).get("duration_s", 0.0)
    return round(total, 2)


def _count_repairs(attempt_dir):
    """Deduit le nombre de reparations du nombre de fichiers designer_repair*."""
    return sum(1 for e in os.listdir(attempt_dir) if e.startswith("designer_repair") and e.endswith("_meta.json"))


def recover(work_dir, version):
    """Reconstruit un CSV depuis les design.json survivants.

    Recompile, repave et revalide chaque design : tout est deterministe, donc les
    metriques obtenues sont identiques a celles du run d'origine.
    """
    cases = _load_testset_index()
    rows = []

    for case_id in sorted(os.listdir(work_dir)):
        case_dir = os.path.join(work_dir, case_id)
        if not os.path.isdir(case_dir):
            continue
        case = cases.get(case_id, {})

        for attempt_name in sorted(os.listdir(case_dir)):
            attempt_dir = os.path.join(case_dir, attempt_name)
            design_path = os.path.join(attempt_dir, "design.json")
            if not os.path.isfile(design_path):
                continue  # generation echouee ou interrompue avant d'ecrire le design

            with open(design_path, "r", encoding="utf-8") as f:
                design_data = json.load(f)

            voxels = compile_design(design_data)
            placements = tile(voxels)
            report = validate(voxels, placements, design_data)

            iou = ""
            if case.get("reference"):
                iou = round(voxel_iou(build_reference(case["reference"]), voxels), 3)

            rows.append({
                "id": case_id,
                "category": case.get("category", ""),
                "complexity": case.get("complexity", ""),
                "attempt": int(attempt_name.split("_")[-1]),
                "seed": "",
                "valid_json": True,
                "duration_s": _sum_durations(attempt_dir),
                "n_pieces": report["metrics"]["n_pieces"],
                "score": report["score"],
                "n_components": report["metrics"]["n_components"],
                "fill_rate": report["metrics"]["fill_rate"],
                "repairs_used": _count_repairs(attempt_dir),
                "iou": iou,
            })

    os.makedirs(RESULTS_DIR, exist_ok=True)
    csv_path = os.path.join(RESULTS_DIR, f"{version}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{len(rows)} generations reconstruites -> {csv_path}")
    print("ATTENTION : ne contient QUE les generations reussies (un design.json existe).")
    print("Le taux de validite d'un run interrompu n'est donc pas mesurable ainsi.")
    return csv_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m tools.recover_eval <work_dir> <version>")
        sys.exit(1)
    recover(sys.argv[1], sys.argv[2])
