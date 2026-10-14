"""Le prompt du jeu figé est-il la cause de la non-adoption de `sphere` ? (Priorité 2)

CE QUI EST COMPARE : le cas figé `prim_sphere` et sa reformulation `prim_sphere_v2`,
qui différent d'une SEULE clause — « et 7 plates de haut ». Tout le reste est identique,
référence comprise, y compris la phrase qui dicte la méthode (« chaque couche horizontale
est un disque... »). Une seule variable change, donc l'écart lui est attribuable.

CE QU'ON REGARDE, et pourquoi ces trois choses :

1. L'OP CHOISIE. Le DSL offre `sphere`, rastérisé par la MEME fonction que la référence
   d'IoU : l'adopter donne 1.000 par construction. Le modèle lui préfère `layer`, qu'il
   empile à la main. C'est le plus gros écart connu du jeu primitives.
2. L'UNITE EN Z DU PLAN. Relevé sur les 9 plans du cas figé : aucun n'écrit « 7 plates
   (H) », six écrivent « 7 tenons (H) » et trois « 14 plates (H) ». Or prompts/
   planner_system.txt donne la consigne DEUX fois, dont une en gabarit de sortie. Le
   modèle recopie donc l'unité du prompt utilisateur plutôt que de suivre son gabarit —
   l'hypothèse testée ici.
3. L'IoU. La conséquence, et la seule chose qui compte pour le jeu de test.

Usage:
    python -m tools.comparer_sphere_v2
"""

import glob
import json
import os
import re
import sys

# Sortie redirigee = cp1252 sous Windows, et ce rapport est plein d'accents et de
# guillemets francais. Meme ligne que agent.py, pipeline.py et lego.py.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.compiler import compile_design
from src.primitives import build_reference, voxel_iou

REFERENCE = {"shape": "sphere", "params": {"diameter": 7}}

# Trois formulations, chacune à UNE clause de la précédente. Changer deux choses à la
# fois rendrait l'écart inattribuable — c'est la règle du projet, et elle a déjà été
# enfreinte une fois sur les barèmes du juge visuel (voir docs/mesures.md).
VERSIONS = [
    ("prim_sphere    (figé)", "eval/results/_work/*/prim_sphere/attempt_*",
     "tel quel"),
    ("prim_sphere_v2 (+ unité)", "eval/results/_work/*/prim_sphere_v2/attempt_*",
     "+ « et 7 plates de haut »"),
    ("prim_sphere_v3 (− méthode)", "eval/results/_work/*/prim_sphere_v3/attempt_*",
     "− « chaque couche horizontale est un disque... »"),
]

# Points de repère calculés une fois, pour situer les scores : une op posée bêtement
# bat souvent les couches dessinées à la main.
REPERES = [
    ("op `sphere`", {"op": "sphere", "at": [0, 0, 0], "diameter": 7}),
    ("cylindre plein 7x7x7", {"op": "cylinder", "at": [0, 0, 0], "diameter": 7, "height": 7}),
    ("boite pleine 7x7x7", {"op": "box", "from": [0, 0, 0], "to": [6, 6, 6]}),
]


def unite_z_du_plan(work_dir):
    """L'unité que le plan écrit pour la hauteur, et le z le plus haut qu'il pose.

    Retourne (texte_bbox, z_max) — ou (None, None) si le plan n'existe pas : un run
    interrompu en laisse, ce n'est pas une anomalie.
    """
    chemin = os.path.join(work_dir, "plan.md")
    if not os.path.isfile(chemin):
        return None, None
    with open(chemin, "r", encoding="utf-8") as f:
        texte = f.read()
    bbox = re.search(r"Boite englobante\s*:\s*(.*)", texte)
    hauteur = None
    if bbox:
        m = re.search(r"x\s*(\d+)\s*(tenons?|plates?)\s*\(H\)", bbox.group(1))
        hauteur = f"{m.group(1)} {m.group(2)}" if m else bbox.group(1).strip()[:30]
    zs = [int(b) for _, b in re.findall(r"z=(\d+)\.\.(\d+)", texte)]
    return hauteur, (max(zs) if zs else None)


def releve(motif):
    """Un tuple par tentative : (dossier, ops utilisées, hauteur du plan, z max, IoU)."""
    reference = build_reference(REFERENCE)
    lignes = []
    for work_dir in sorted(d for d in glob.glob(motif) if os.path.isdir(d)):
        chemin = os.path.join(work_dir, "design.json")
        if not os.path.isfile(chemin):
            continue
        with open(chemin, "r", encoding="utf-8") as f:
            design = json.load(f)
        ops = sorted({o["op"] for o in design["ops"]})
        hauteur, z_max = unite_z_du_plan(work_dir)
        iou = round(voxel_iou(reference, compile_design(design)), 3)
        lignes.append((work_dir, ops, hauteur, z_max, iou))
    return lignes


def _bloc(titre, lignes):
    print(f"\n{titre}  ({len(lignes)} tentatives)")
    if not lignes:
        print("  aucune — le run a-t-il tourné ?")
        return 0, 0.0
    print(f"  {'ops du design':24}{'hauteur au plan':>18}{'z max':>7}{'IoU':>8}")
    for _, ops, hauteur, z_max, iou in lignes:
        print(f"  {', '.join(ops):24}{hauteur or '?':>18}{z_max if z_max is not None else '?':>7}"
              f"{iou:>8.3f}")
    adoptions = sum(1 for _, ops, _, _, _ in lignes if "sphere" in ops)
    moyenne = sum(l[4] for l in lignes) / len(lignes)
    print(f"  -> op `sphere` adoptée {adoptions}/{len(lignes)}   IoU moyen {moyenne:.3f}")
    return adoptions, moyenne


def reperes():
    """IoU de solutions posees sans finesse, pour situer ce que produit le modele."""
    reference = build_reference(REFERENCE)
    print("\nPOINTS DE REPERE")
    for nom, op in REPERES:
        design = {"name": "x", "bbox": [7, 7, 7], "ops": [{**op, "color": "white"}]}
        print(f"  {nom:28}IoU {voxel_iou(reference, compile_design(design)):.3f}")
    # L'op `sphere` et la reference sortent de la MEME fonction de src/primitives.py :
    # son 1.000 n'est pas une performance, c'est une identite.
    print("  (l'op `sphere` vaut 1.000 par construction : meme rastériseur)")


if __name__ == "__main__":
    print("=" * 68)
    print("PRIORITE 2 — l'op `sphere` est-elle bloquee par le prompt du jeu fige ?")
    print("=" * 68)

    resultats = []
    for titre, motif, clause in VERSIONS:
        lignes = releve(motif)
        adoptions, moyenne = _bloc(f"{titre}   [{clause}]", lignes)
        resultats.append((titre, len(lignes), adoptions, moyenne))

    reperes()

    print(f"\n{'=' * 68}\n{'version':30}{'n':>4}{'op sphere':>12}{'IoU moyen':>12}")
    for titre, n, adoptions, moyenne in resultats:
        if n:
            print(f"{titre:30}{n:>4}{f'{adoptions}/{n}':>12}{moyenne:>12.3f}")
