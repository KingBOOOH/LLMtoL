"""Harnais d'evaluation (Phase 2 tache 2.3 ; etendu Phase 3 tache 3.3 pour v1).

Mesure une version de la pipeline sur eval/testset.json (n generations par prompt,
seeds fixes et journalises) -> CSV + resume Markdown dans eval/results/.

Deux versions generables :
- "v0" : pipeline d'origine (src/prompt.py, liste de cubes bruts, pas de vraies pieces
  LEGO). Notee avec un SOUS-ENSEMBLE des controles de src.validator qui ont un sens
  sans catalogue de pieces reel (connexite, taux de remplissage, centre de masse) —
  collisions/pieces-flottantes/joints ne s'appliquent qu'a une sortie DSL+placements.
- "v1" : planificateur + designer contraint par le DSL (src.planner + src.designer,
  Phase 3), avec boucle de reparation (max 2). Notee avec la validation COMPLETE de
  src.validator (tous les controles).

ATTENTION a l'interpretation d'un score v1 < score v0 : v1 est jugee par un validateur
strictement plus severe (colisions/flottement/joints en plus), donc les scores ne sont
PAS directement comparables terme a terme — c'est le TAUX DE VALIDITE (errors == [])
et le taux de reparation qui mesurent le mieux le progres reel.

Usage:
    python -m eval.run_eval --version v0 --n 3
    python -m eval.run_eval --version v1 --n 3
    python -m eval.run_eval --compare eval/results/v0_<date>.csv eval/results/v1_<date>.csv
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import zlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.llm_client import LLMCallError
from src.primitives import build_reference, voxel_iou
from src.validator import check_center_of_mass, check_connectivity, check_fill_rate

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_SCRIPT = os.path.join(BASE_DIR, "src", "prompt.py")
EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(EVAL_DIR, "results")
DEFAULT_TESTSET = os.path.join(EVAL_DIR, "testset.json")


def _deterministic_seed(case_id, attempt):
    """crc32 plutot que hash() integre : hash() est aleatoire d'un run a l'autre
    (PYTHONHASHSEED), ce qui casserait la reproductibilite des seeds journalisees."""
    return (zlib.crc32(case_id.encode("utf-8")) % 1_000_000) + attempt


def _rasterize_bricks(bricks):
    """Convertit une liste de bricks v0 (position/size entiers) en grille voxel pour
    reutiliser les controles generiques de src.validator qui ne dependent pas d'un
    catalogue de pieces reelles (pas de collisions/flottement/joints en v0)."""
    if not bricks:
        return np.zeros((1, 1, 1), dtype=np.int16)

    xs, ys, zs = [], [], []
    for b in bricks:
        px, py, pz = b["position"]
        sx, sy, sz = b["size"]
        xs += [px, px + sx]
        ys += [py, py + sy]
        zs += [pz, pz + sz]

    min_x, min_y, min_z = min(xs), min(ys), min(zs)
    shape = (max(1, max(xs) - min_x), max(1, max(ys) - min_y), max(1, max(zs) - min_z))
    grid = np.zeros(shape, dtype=np.int16)
    for b in bricks:
        px, py, pz = b["position"]
        sx, sy, sz = b["size"]
        x0, y0, z0 = px - min_x, py - min_y, pz - min_z
        grid[x0:x0 + sx, y0:y0 + sy, z0:z0 + sz] = 1
    return grid


def _score_v0(bricks):
    """Note une sortie v0 avec le sous-ensemble de controles applicable a des cubes.

    Pas de pieces reelles en v0, donc ni collisions, ni pieces flottantes, ni joints :
    seuls connexite, taux de remplissage et centre de masse ont un sens. Le score
    n'est donc PAS comparable terme a terme a celui de v1, juge plus severement.
    """
    grid = _rasterize_bricks(bricks)
    n_components, _ = check_connectivity(grid)
    fill_rate, _ = check_fill_rate(grid)
    com_warnings = check_center_of_mass(grid)

    score = 100.0
    score -= 25 if n_components > 1 else 0
    score -= 10 if fill_rate > 0.85 else 0
    score -= 5 if com_warnings else 0
    return max(0.0, min(100.0, score)), n_components, fill_rate


def run_one(prompt_text, seed, work_dir, reference=None):
    """Genere une sortie v0 via src/prompt.py (sous-processus) et la mesure.

    Sous-processus plutot qu'import : prompt.py est un script a code de haut niveau,
    l'importer declencherait son execution.
    """
    os.makedirs(work_dir, exist_ok=True)
    env = os.environ.copy()
    env["LEGO_WORK_DIR"] = work_dir
    env["PYTHONIOENCODING"] = "utf-8"

    t0 = time.time()
    result = subprocess.run(
        ["python", PROMPT_SCRIPT, prompt_text, str(seed)],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    duration = time.time() - t0

    model_path = os.path.join(work_dir, "model.json")
    valid_json = result.returncode == 0 and os.path.isfile(model_path)

    row = {"valid_json": valid_json, "duration_s": round(duration, 2),
           "n_pieces": 0, "score": 0.0, "n_components": 0, "fill_rate": 0.0,
           "repairs_used": 0, "iou": ""}

    if valid_json:
        with open(model_path, "r", encoding="utf-8") as f:
            bricks = json.load(f).get("bricks", [])
        score, n_components, fill_rate = _score_v0(bricks)
        row.update({"n_pieces": len(bricks), "score": round(score, 1),
                    "n_components": n_components, "fill_rate": round(fill_rate, 3)})
        if reference is not None:
            row["iou"] = round(voxel_iou(build_reference(reference), _rasterize_bricks(bricks)), 3)

    return row


def run_one_v1(prompt_text, seed, work_dir, reference=None):
    """Genere via src.planner + src.designer (Phase 3). Appels directs (pas de
    subprocess) : ces deux modules sont des fonctions pures sans etat global
    mutable pertinent entre generations."""
    os.makedirs(work_dir, exist_ok=True)
    t0 = time.time()

    row = {"valid_json": False, "n_pieces": 0, "score": 0.0,
           "n_components": 0, "fill_rate": 0.0, "repairs_used": 0, "iou": ""}

    try:
        from src.designer import design as designer_design
        from src.planner import plan as planner_plan

        plan_text = planner_plan(prompt_text, work_dir, seed=seed)
        result = designer_design(prompt_text, plan_text, work_dir, seed=seed)
        metrics = result["report"]["metrics"]
        row.update({
            "valid_json": True,
            "n_pieces": metrics["n_pieces"],
            "score": result["report"]["score"],
            "n_components": metrics["n_components"],
            "fill_rate": metrics["fill_rate"],
            "repairs_used": result["repairs_used"],
        })
        if reference is not None:
            row["iou"] = round(voxel_iou(build_reference(reference), result["voxels"]), 3)
    except (LLMCallError, RuntimeError):
        pass  # row reste invalid_json=False, score 0 ; error.txt deja ecrit par designer.py

    row["duration_s"] = round(time.time() - t0, 2)
    return row


def run_eval(version, testset_path, n_per_prompt, out_dir):
    """Execute le jeu de test complet et ecrit CSV + resume Markdown.

    La version choisit le generateur : "v1*" passe par planner+designer, tout le
    reste par la chaine v0. Chaque generation a sa propre seed deterministe et son
    propre dossier de travail, pour etre rejouable isolement.
    """
    with open(testset_path, "r", encoding="utf-8") as f:
        testset = json.load(f)

    os.makedirs(out_dir, exist_ok=True)
    date_tag = time.strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(out_dir, f"{version}_{date_tag}.csv")
    fieldnames = ["id", "category", "complexity", "attempt", "seed", "valid_json",
                  "duration_s", "n_pieces", "score", "n_components", "fill_rate",
                  "repairs_used", "iou"]

    generator = run_one_v1 if version.startswith("v1") else run_one

    rows = []
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for case in testset:
            for attempt in range(1, n_per_prompt + 1):
                seed = _deterministic_seed(case["id"], attempt)
                work_dir = os.path.join(out_dir, "_work", version, case["id"], f"attempt_{attempt:02d}")
                print(f"[{version}] {case['id']} tentative {attempt}/{n_per_prompt} (seed={seed})...")
                row = {"id": case["id"], "category": case["category"],
                       "complexity": case["complexity"], "attempt": attempt, "seed": seed,
                       **generator(case["prompt"], seed, work_dir, case.get("reference"))}
                writer.writerow(row)
                f.flush()  # un run interrompu (OOM, Ctrl+C) garde les lignes deja faites
                rows.append(row)

    summary_path = os.path.join(out_dir, f"{version}_{date_tag}_summary.md")
    _write_summary(rows, version, summary_path)
    print(f"\nResultats : {csv_path}\nResume    : {summary_path}")
    return csv_path


def _write_summary(rows, version, path):
    """Redige le resume Markdown : agregats globaux, par categorie et par forme."""
    n = len(rows)
    valid_rows = [r for r in rows if r["valid_json"]]
    valid = len(valid_rows)
    avg_score = sum(r["score"] for r in valid_rows) / valid if valid else 0.0
    avg_pieces = sum(r["n_pieces"] for r in valid_rows) / valid if valid else 0.0
    avg_duration = sum(r["duration_s"] for r in rows) / n if n else 0.0

    avg_repairs = sum(r.get("repairs_used", 0) for r in valid_rows) / valid if valid else 0.0
    iou_rows = [r for r in valid_rows if r.get("iou") not in (None, "")]
    avg_iou = sum(float(r["iou"]) for r in iou_rows) / len(iou_rows) if iou_rows else None

    lines = [
        f"# Resultats eval — {version}", "",
        f"- Generations : {n}",
        f"- Taux JSON valide : {valid}/{n} ({valid / n:.0%})" if n else "- Generations : 0",
        f"- Score moyen (generations valides) : {avg_score:.1f}/100",
        f"- Pieces moyennes : {avg_pieces:.1f}",
        f"- Reparations moyennes (v1 seulement, 0 sinon) : {avg_repairs:.2f}",
        f"- Duree moyenne par generation : {avg_duration:.1f}s",
    ]
    if avg_iou is not None:
        lines.append(f"- **IoU moyen vs reference : {avg_iou:.3f}** (1.0 = forme exacte)")
    lines += ["", "## Par categorie", "", "| categorie | valide | score moyen | IoU moyen |",
              "|---|---|---|---|"]

    for cat in sorted({r["category"] for r in rows}):
        cat_valid = [r for r in rows if r["category"] == cat and r["valid_json"]]
        cat_total = [r for r in rows if r["category"] == cat]
        cat_score = sum(r["score"] for r in cat_valid) / len(cat_valid) if cat_valid else 0.0
        cat_iou_rows = [r for r in cat_valid if r.get("iou") not in (None, "")]
        cat_iou = (f"{sum(float(r['iou']) for r in cat_iou_rows) / len(cat_iou_rows):.3f}"
                   if cat_iou_rows else "-")
        lines.append(f"| {cat} | {len(cat_valid)}/{len(cat_total)} | {cat_score:.1f} | {cat_iou} |")

    lines += ["", "## Par forme", "", "| id | valide | IoU moyen |", "|---|---|---|"]
    for case_id in sorted({r["id"] for r in rows}):
        id_rows = [r for r in rows if r["id"] == case_id]
        id_valid = [r for r in id_rows if r["valid_json"]]
        id_iou_rows = [r for r in id_valid if r.get("iou") not in (None, "")]
        id_iou = (f"{sum(float(r['iou']) for r in id_iou_rows) / len(id_iou_rows):.3f}"
                  if id_iou_rows else "-")
        lines.append(f"| {case_id} | {len(id_valid)}/{len(id_rows)} | {id_iou} |")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def compare(csv_a, csv_b):
    """Affiche cote a cote deux runs (validite, score, IoU).

    Attention a l'interpretation : entre v0 et v1, seuls la validite et l'IoU sont
    directement comparables — les scores viennent de validateurs de severite
    differente.
    """
    def load_stats(path):
        """Agrege un CSV : total, generations valides, score moyen, IoU moyen."""
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        valid_rows = [r for r in rows if r["valid_json"] == "True"]
        scores = [float(r["score"]) for r in valid_rows]
        ious = [float(r["iou"]) for r in valid_rows if r.get("iou") not in (None, "")]
        return {
            "n": len(rows), "valid": len(valid_rows),
            "score": sum(scores) / len(scores) if scores else 0.0,
            "iou": sum(ious) / len(ious) if ious else None,
        }

    a, b = load_stats(csv_a), load_stats(csv_b)
    label_a, label_b = os.path.basename(csv_a), os.path.basename(csv_b)

    def fmt_valid(stats):
        """Formate le taux de validite en 'n/total (pourcentage)'."""
        ratio = stats["valid"] / stats["n"] if stats["n"] else 0.0
        return f"{stats['valid']}/{stats['n']} ({ratio:.0%})"

    def fmt_iou(stats):
        """Formate l'IoU, ou '-' si le jeu de test n'a pas de reference."""
        return f"{stats['iou']:.3f}" if stats["iou"] is not None else "-"

    print(f"{'':22}{label_a:30}{label_b:30}")
    print(f"{'taux JSON valide':22}{fmt_valid(a):30}{fmt_valid(b):30}")
    print(f"{'score moyen':22}{a['score']:<30.1f}{b['score']:<30.1f}")
    print(f"{'IoU moyen':22}{fmt_iou(a):30}{fmt_iou(b):30}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="v0")
    parser.add_argument("--n", type=int, default=3)
    parser.add_argument("--testset", default=DEFAULT_TESTSET)
    parser.add_argument("--out", default=RESULTS_DIR)
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"))
    args = parser.parse_args()

    if args.compare:
        compare(*args.compare)
    else:
        run_eval(args.version, args.testset, args.n, args.out)
