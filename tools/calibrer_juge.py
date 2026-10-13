"""Calibration du juge visuel contre l'IoU connu (US V2-01).

LA QUESTION : la note du VLM varie-t-elle dans le meme sens que la ressemblance
reelle ? Le jeu primitives est le seul endroit du projet ou on connait la reponse
exacte — l'IoU se calcule analytiquement contre une reference (src.primitives). On
fait donc noter au juge des rendus dont on sait deja ce qu'ils valent, et on mesure
l'accord.

CE QUI EST MESURE : une correlation de RANG (Spearman), pas Pearson. Une note entiere
de 0 a 10 contre un IoU continu n'a aucune raison d'etre lineaire ; ce qu'on demande
au juge, c'est de classer les rendus dans le bon ordre, pas de predire l'IoU. C'est
statistics.correlation(method="ranked"), en bibliotheque standard : aucune dependance
nouvelle pour ca.

CE QUI SE PASSE ENSUITE :
  correlation forte   -> le juge est exploitable, on ajoute la colonne note_vlm
  correlation faible  -> le juge est jete, et on l'aura su sans depenser une seule
                         generation. C'est un resultat, pas un echec : un juge faible
                         sur une tache que le generateur rate deja donne un signal
                         bruite sans dire lequel des deux est casse.
Dans les deux cas le chiffre va au README. Publier une mauvaise mesure coute moins
cher que de la refaire dans six mois.

PIEGE A REGARDER AVANT DE CONCLURE : la sphere du projet est ronde dans la grille mais
aplatie ~2,5x a l'ecran (un tenon vaut 20 LDU, une plate 8). La reference d'IoU assume
ce choix, pas un oeil. Sur prim_sphere, juge et IoU peuvent donc diverger pour une
raison qui n'est pas la faute du juge — d'ou la correlation rapportee AVEC et SANS
cette forme.

Prerequis : les render_iso.png doivent exister (build_from_design puis render_batch).

Usage:
    python -m tools.calibrer_juge --model qwen/qwen3-vl-4b
    python -m tools.calibrer_juge --model qwen/qwen3-vl-4b --resume
"""

import argparse
import csv
import glob as globlib
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import juge_vlm
from src.compiler import compile_design
from src.primitives import build_reference, voxel_iou, voxel_iou_oriente

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTSET = os.path.join(BASE_DIR, "eval", "primitives_testset.json")
GLOB_DEFAUT = "eval/results/_work/*prim*/*/attempt_*"


def charger_testset(chemin):
    """id -> {prompt, reference}. Les dossiers de travail sont nommes par cet id."""
    with open(chemin, "r", encoding="utf-8") as f:
        return {cas["id"]: cas for cas in json.load(f)}


def iou_du_dossier(work_dir, reference):
    """Recalcule les DEUX IoU depuis design.json. Deterministe, et gratuit : aucun
    appel au juge, donc on peut relancer la calibration avec --resume pour corriger
    la verite terrain sans redepenser une seule note.

    Pourquoi les deux : voxel_iou ne tourne pas les grilles, donc il punit un escalier
    parfaitement correct construit dans l'autre sens (prim_escalier, diagnostique dans
    le README : 0,429 tel quel, 1,000 a rotation autorisee). Or le juge regarde une
    IMAGE — il n'a aucun moyen de savoir quelle orientation etait attendue, et le prompt
    du jeu de test ne la dit pas. Correler sa note contre voxel_iou mesurerait donc en
    partie un defaut connu de la verite terrain, pas la qualite du juge.
    """
    with open(os.path.join(work_dir, "design.json"), "r", encoding="utf-8") as f:
        design = json.load(f)
    ref, grille = build_reference(reference), compile_design(design)
    return (round(voxel_iou(ref, grille), 3), round(voxel_iou_oriente(ref, grille), 3))


def collecter(motif, testset):
    """Dossiers calibrables : un design, un rendu, et une forme de reference connue.

    Un dossier incomplet est ignore en silence — un run interrompu en laisse toujours,
    ce n'est pas une anomalie a signaler.
    """
    cas = []
    for work_dir in sorted(d for d in globlib.glob(motif) if os.path.isdir(d)):
        case_id = os.path.basename(os.path.dirname(work_dir))
        fiche = testset.get(case_id)
        if fiche is None or fiche.get("reference") is None:
            continue
        if not (os.path.isfile(os.path.join(work_dir, "design.json"))
                and os.path.isfile(os.path.join(work_dir, "render_iso.png"))):
            continue
        cas.append((case_id, work_dir, fiche))
    return cas


def spearman(x, y):
    """Correlation de rang, ou None si elle n'est pas definie (moins de 2 points, ou
    une des deux series constante — un juge qui note tout pareil, justement)."""
    if len(x) < 2:
        return None
    try:
        return round(statistics.correlation(x, y, method="ranked"), 3)
    except statistics.StatisticsError:
        return None


def calibrer(motif, testset_path, model, resume=False, limite=None):
    """Note chaque rendu, confronte a l'IoU connu, ecrit le CSV et affiche le bilan."""
    testset = charger_testset(testset_path)
    cas = collecter(motif, testset)
    if limite:
        cas = cas[:limite]
    if not cas:
        print(f"Aucun dossier calibrable pour {motif}.")
        print("Les render_iso.png existent-ils ? (build_from_design puis render_batch)")
        return None

    print(f"{len(cas)} rendus a noter, modele {model}\n")
    lignes, echecs, t0 = [], [], time.time()

    for i, (case_id, work_dir, fiche) in enumerate(cas, 1):
        iou, iou_or = iou_du_dossier(work_dir, fiche["reference"])
        note = juge_vlm.note_existante(work_dir, juge_vlm.EMPREINTE_BAREME) if resume else None
        raison, duree = "", 0.0

        if note is None:
            try:
                r = juge_vlm.noter(fiche["prompt"], os.path.join(work_dir, "render_iso.png"),
                                   work_dir, model=model)
                note, raison, duree = r["note"], r["raison"], r["duree_s"]
            except (ValueError, OSError) as e:
                # Une note perdue n'invalide pas le lot, mais elle est comptee : un
                # taux d'echec eleve EST un resultat sur le modele teste.
                echecs.append((work_dir, str(e).split("\n")[0]))
                print(f"[{i}/{len(cas)}] {case_id:14} ECHEC {str(e)[:70]}", flush=True)
                continue

        lignes.append({"id": case_id, "work_dir": work_dir, "iou": iou,
                       "iou_oriente": iou_or, "note_vlm": note, "duree_s": duree,
                       "raison": raison})
        print(f"[{i}/{len(cas)}] {case_id:14} IoU {iou:.3f}/{iou_or:.3f}  "
              f"note {note}/10  {raison[:60]}", flush=True)

    _bilan(lignes, echecs, model, time.time() - t0)
    return lignes


def _bilan(lignes, echecs, model, duree_totale):
    """Affiche la correlation et ecrit le CSV. C'est la sortie qui decide de l'US."""
    if not lignes:
        print("\nAucune note obtenue : rien a correler.")
        return

    chemin = os.path.join(BASE_DIR, "eval", "results",
                          f"calibration_juge_{model.replace('/', '_')}.csv")
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "work_dir", "iou", "iou_oriente",
                                          "note_vlm", "duree_s", "raison"])
        w.writeheader()
        w.writerows(lignes)

    notes = [l["note_vlm"] for l in lignes]

    def rho(cle, sous_ensemble=None):
        g = sous_ensemble if sous_ensemble is not None else lignes
        v = spearman([l[cle] for l in g], [l["note_vlm"] for l in g])
        return f"{v:+.3f}" if v is not None else "non definie"

    sans_sphere = [l for l in lignes if l["id"] != "prim_sphere"]
    sans_escalier = [l for l in lignes if l["id"] != "prim_escalier"]

    print(f"\n{'=' * 64}\nCALIBRATION — {model}\n{'=' * 64}")
    print(f"rendus notes          : {len(lignes)}  ({len(echecs)} echecs)")
    # Deux verites terrain, parce qu'elles ne disent pas la meme chose. voxel_iou punit
    # une rotation ; le juge regarde une image et n'a aucun moyen de savoir quelle
    # orientation etait attendue — le prompt du jeu de test ne la dit pas. L'ecart entre
    # les deux colonnes mesure donc un defaut de la verite terrain, pas du juge.
    print(f"\n{'correlation de rang':24}{'vs iou':>12}{'vs iou_oriente':>16}")
    for libelle, groupe in (("tous les rendus", None),
                            ("sans prim_sphere", sans_sphere),
                            ("sans prim_escalier", sans_escalier)):
        print(f"{libelle:24}{rho('iou', groupe):>12}{rho('iou_oriente', groupe):>16}")
    print("  prim_sphere   : aplatie ~2,5x a l'ecran, le juge la voit autrement que la"
          " reference.\n  prim_escalier : rotation non imposee par le prompt, punie par"
          " iou mais pas par iou_oriente.\n")
    distinctes = sorted(set(notes))
    print(f"notes distinctes      : {distinctes}")
    if len(distinctes) <= 3:
        print("  ATTENTION : le juge n'utilise que quelques valeurs. Une echelle qui "
              "s'effondre\n  fait chuter la correlation sans que le juge soit aveugle "
              "pour autant.")
    print(f"duree                 : {duree_totale:.0f}s, "
          f"{duree_totale / max(len(lignes), 1):.1f}s par note")

    print(f"\n{'forme':16}{'n':>4}{'IoU':>9}{'IoU oriente':>13}{'note':>8}")
    for case_id in sorted({l["id"] for l in lignes}):
        g = [l for l in lignes if l["id"] == case_id]

        def moy(cle, g=g):
            return sum(l[cle] for l in g) / len(g)

        print(f"{case_id:16}{len(g):>4}{moy('iou'):>9.3f}{moy('iou_oriente'):>13.3f}"
              f"{moy('note_vlm'):>8.1f}")

    print(f"\nCSV : {chemin}")
    if echecs:
        print(f"\n{len(echecs)} echec(s) :")
        for d, e in echecs[:5]:
            print(f"  {d} — {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None,
                        help="modele vision (defaut : LEGO_VLM_MODEL)")
    parser.add_argument("--glob", default=GLOB_DEFAUT)
    parser.add_argument("--testset", default=TESTSET)
    parser.add_argument("--resume", action="store_true",
                        help="reutilise les juge_vlm.json deja presents")
    parser.add_argument("--limit", type=int, help="s'arreter apres N rendus")
    args = parser.parse_args()

    modele = args.model or (juge_vlm.config.LM_STUDIO_VLM_MODEL or None)
    if not modele:
        print("Aucun modele vision : passer --model ou definir LEGO_VLM_MODEL.")
        sys.exit(1)

    calibrer(args.glob, args.testset, modele, resume=args.resume, limite=args.limit)
