"""Harnais d'evaluation (Phase 2 tache 2.3 ; etendu Phase 3 tache 3.3).

Mesure la pipeline sur un jeu de test (n generations par prompt, seeds fixes et
journalisees) -> CSV + resume Markdown dans eval/results/.

Genere via src.planner + src.designer (plan en texte libre, puis design contraint par
le schema DSL avec boucle de reparation), puis note avec la validation complete de
src.validator. Sur un jeu de test primitives, la ressemblance a la forme demandee est
mesuree en plus par l'IoU contre une reference calculee analytiquement.

La chaine d'origine ("v0", le LLM produisait directement une liste de cubes) a ete
retiree du projet. Ses mesures de reference restent figees dans eval/results/ : elles
ne sont plus regenerables, mais restent lisibles par --compare. Attention en les
relisant, les scores ne sont PAS comparables terme a terme — v0 etait notee par un
sous-ensemble des controles, faute de vraies pieces. Ce sont le taux de validite et la
connexite qui mesurent le progres reel.

Usage:
    python -m eval.run_eval --version v1 --n 3
    python -m eval.run_eval --version v1prim --n 3 --testset eval/primitives_testset.json
    python -m eval.run_eval --compare eval/results/v0_<date>.csv eval/results/v1_<date>.csv
"""

import argparse
import csv
import json
import os
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.llm_client import LLMCallError
from src.primitives import build_reference, voxel_iou, voxel_iou_oriente

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(EVAL_DIR, "results")
DEFAULT_TESTSET = os.path.join(EVAL_DIR, "testset.json")


def _deterministic_seed(case_id, attempt):
    """crc32 plutot que hash() integre : hash() est aleatoire d'un run a l'autre
    (PYTHONHASHSEED), ce qui casserait la reproductibilite des seeds journalisees."""
    return (zlib.crc32(case_id.encode("utf-8")) % 1_000_000) + attempt


def run_one(prompt_text, seed, work_dir, reference=None):
    """Genere via src.planner + src.designer et mesure le resultat.

    Appels directs plutot que sous-processus : ces deux modules sont des fonctions
    pures, sans etat global mutable d'une generation a l'autre.
    """
    os.makedirs(work_dir, exist_ok=True)
    t0 = time.time()

    row = {"valid_json": False, "n_pieces": 0, "score": 0.0,
           "n_components": 0, "fill_rate": 0.0, "repairs_used": 0, "iou": "",
           "z_min": "", "iou_oriente": ""}

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
            # "" plutot que 0 si absent : un z_min inconnu ne doit pas se confondre
            # avec un modele correctement pose sur le sol.
            "z_min": metrics.get("z_min", ""),
        })
        if reference is not None:
            cible = build_reference(reference)
            row["iou"] = round(voxel_iou(cible, result["voxels"]), 3)
            # Metrique secondaire : les prompts n'imposent aucune orientation.
            row["iou_oriente"] = round(voxel_iou_oriente(cible, result["voxels"]), 3)
    except (LLMCallError, RuntimeError):
        pass  # row reste invalid_json=False, score 0 ; error.txt deja ecrit par designer.py

    row["duration_s"] = round(time.time() - t0, 2)
    return row


def _row_from_existing(work_dir, reference):
    """Recalcule une ligne depuis un design.json deja sur disque, sans appel LLM.

    Support de --resume : tout l'aval de la generation (compile, pave, valide) est
    deterministe, donc les metriques obtenues sont identiques a celles qu'aurait
    produites le run d'origine. Retourne None s'il n'y a rien a reprendre.
    """
    design_path = os.path.join(work_dir, "design.json")
    if not os.path.isfile(design_path):
        return None

    from src.compiler import compile_design
    from src.tiler import tile
    from src.validator import validate

    with open(design_path, "r", encoding="utf-8") as f:
        design_data = json.load(f)

    voxels = compile_design(design_data)
    placements = tile(voxels)
    report = validate(voxels, placements, design_data)
    metrics = report["metrics"]

    n_repairs = sum(1 for e in os.listdir(work_dir)
                    if e.startswith("designer_repair") and e.endswith("_meta.json"))
    duration = 0.0
    for entry in os.listdir(work_dir):
        if entry.endswith("_meta.json"):
            with open(os.path.join(work_dir, entry), "r", encoding="utf-8") as f:
                duration += json.load(f).get("duration_s", 0.0)

    row = {"valid_json": True, "duration_s": round(duration, 2),
           "n_pieces": metrics["n_pieces"], "score": report["score"],
           "n_components": metrics["n_components"], "fill_rate": metrics["fill_rate"],
           "repairs_used": n_repairs, "iou": "",
           "z_min": metrics.get("z_min", "")}
    if reference is not None:
        cible = build_reference(reference)
        row["iou"] = round(voxel_iou(cible, voxels), 3)
        row["iou_oriente"] = round(voxel_iou_oriente(cible, voxels), 3)
    return row


def run_eval(version, testset_path, n_per_prompt, out_dir, resume=False):
    """Execute le jeu de test complet et ecrit CSV + resume Markdown.

    `version` ne sert qu'a nommer les fichiers de sortie et le dossier de travail :
    il n'y a plus qu'un seul generateur depuis le retrait de la chaine en cubes.
    Chaque generation a sa propre seed deterministe et son propre dossier, pour etre
    rejouable isolement.

    resume=True : reprend un run interrompu en reutilisant les design.json deja
    presents au lieu de les regenerer (les appels LLM sont la partie couteuse).
    """
    with open(testset_path, "r", encoding="utf-8") as f:
        testset = json.load(f)

    os.makedirs(out_dir, exist_ok=True)
    date_tag = time.strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(out_dir, f"{version}_{date_tag}.csv")
    fieldnames = ["id", "category", "complexity", "attempt", "seed", "valid_json",
                  "duration_s", "n_pieces", "score", "n_components", "fill_rate",
                  "repairs_used", "iou", "iou_oriente", "z_min"]

    rows = []
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for case in testset:
            for attempt in range(1, n_per_prompt + 1):
                seed = _deterministic_seed(case["id"], attempt)
                work_dir = os.path.join(out_dir, "_work", version, case["id"], f"attempt_{attempt:02d}")
                reused = _row_from_existing(work_dir, case.get("reference")) if resume else None
                if reused is not None:
                    print(f"[{version}] {case['id']} tentative {attempt}/{n_per_prompt} : reprise du design existant")
                    generated = reused
                else:
                    print(f"[{version}] {case['id']} tentative {attempt}/{n_per_prompt} (seed={seed})...")
                    generated = run_one(case["prompt"], seed, work_dir, case.get("reference"))
                row = {"id": case["id"], "category": case["category"],
                       "complexity": case["complexity"], "attempt": attempt, "seed": seed,
                       **generated}
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
        ori = [float(r["iou_oriente"]) for r in valid_rows
               if r.get("iou_oriente") not in (None, "")]
        if ori:
            lines.append(f"- IoU tolerant a l'orientation : {sum(ori) / len(ori):.3f} "
                         f"(les prompts n'imposent aucune orientation)")

    # Mesure du decalage d'indice z : ligne de base avant correction du prompt =
    # 8 designs sur 26 du jeu primitives ne touchaient pas le sol.
    z_rows = [r for r in valid_rows if r.get("z_min") not in (None, "")]
    if z_rows:
        off = [r for r in z_rows if int(r["z_min"]) != 0]
        lines.append(f"- Ne touchent pas le sol (z_min != 0) : {len(off)}/{len(z_rows)}")
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
    parser.add_argument("--resume", action="store_true",
                        help="reutilise les design.json deja presents au lieu de regenerer")
    args = parser.parse_args()

    if args.compare:
        compare(*args.compare)
    else:
        run_eval(args.version, args.testset, args.n, args.out, resume=args.resume)
