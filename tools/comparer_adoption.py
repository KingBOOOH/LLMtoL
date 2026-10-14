"""Le prompt du jeu figé bloque-t-il l'adoption d'une opération du DSL ? (Priorité 2)

LA QUESTION. Le DSL a des opérations `cylinder` et `sphere`, rastérisées par les MEMES
fonctions que les références d'IoU : les utiliser donne 1,000 par construction. Le modèle
leur préfère souvent `layer`, qu'il dessine à la main — et il le fait moins bien qu'une
boîte posée bêtement. On cherche pourquoi.

LA METHODE. Des formulations qui diffèrent d'UNE SEULE clause, à référence d'IoU
identique. Changer deux choses à la fois rendrait l'écart inattribuable ; la règle a déjà
été enfreinte une fois sur les barèmes du juge visuel, et la mesure a été perdue.

RESULTAT SUR LA SPHERE : la clause qui dicte la METHODE bloquait tout. En la retirant,
l'adoption passe de 0/7 à 3/3 et l'IoU de 0,365 à 1,000. L'unité en Z manquante est un
autre défaut, réel, mais sans effet sur l'adoption.

Usage:
    python -m tools.comparer_adoption            # toutes les formes mesurées
    python -m tools.comparer_adoption sphere
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

GABARIT = "eval/results/_work/*/{cas}/attempt_*"

EXPERIENCES = {
    "sphere": {
        "op_visee": "sphere",
        "reference": {"shape": "sphere", "params": {"diameter": 7}},
        "versions": [
            ("prim_sphere      (figé)", "prim_sphere", "tel quel"),
            ("prim_sphere_v2   (+ unité)", "prim_sphere_v2", "+ « et 7 plates de haut »"),
            ("prim_sphere_v3   (− méthode)", "prim_sphere_v3",
             "− « chaque couche horizontale est un disque... »"),
            ("prim_sphere_libre (au jeu)", "prim_sphere_libre", "le prompt retenu"),
        ],
        # Posees betement, pour situer ce que vaut le dessin a la main.
        "reperes": [
            ("op `sphere`", {"op": "sphere", "at": [0, 0, 0], "diameter": 7}),
            ("cylindre plein 7x7x7",
             {"op": "cylinder", "at": [0, 0, 0], "diameter": 7, "height": 7}),
            ("boite pleine 7x7x7", {"op": "box", "from": [0, 0, 0], "to": [6, 6, 6]}),
        ],
        "bbox": [7, 7, 7],
    },
    "cylindre": {
        "op_visee": "cylinder",
        "reference": {"shape": "cylinder", "params": {"diameter": 9, "height": 4}},
        "versions": [
            ("prim_cylindre    (figé)", "prim_cylindre", "tel quel"),
            ("prim_cylindre_v2 (− forme)", "prim_cylindre_v2",
             "− « la section horizontale est un disque »"),
        ],
        "reperes": [
            ("op `cylinder`", {"op": "cylinder", "at": [0, 0, 0], "diameter": 9, "height": 4}),
            ("boite pleine 9x9x4", {"op": "box", "from": [0, 0, 0], "to": [8, 8, 3]}),
        ],
        "bbox": [9, 9, 4],
    },
}


def unite_z_du_plan(work_dir):
    """L'unité que le plan écrit pour la hauteur, et le z le plus haut qu'il pose.

    Retourne (hauteur, z_max), ou (None, None) si le plan manque — un run interrompu en
    laisse toujours, ce n'est pas une anomalie.
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


def releve(cas, reference):
    """Un tuple par tentative : (dossier, ops, hauteur au plan, z max, IoU)."""
    grille_ref = build_reference(reference)
    lignes = []
    for work_dir in sorted(d for d in glob.glob(GABARIT.format(cas=cas)) if os.path.isdir(d)):
        chemin = os.path.join(work_dir, "design.json")
        if not os.path.isfile(chemin):
            continue
        with open(chemin, "r", encoding="utf-8") as f:
            design = json.load(f)
        ops = sorted({o["op"] for o in design["ops"]})
        hauteur, z_max = unite_z_du_plan(work_dir)
        lignes.append((work_dir, ops, hauteur, z_max,
                       round(voxel_iou(grille_ref, compile_design(design)), 3)))
    return lignes


def _bloc(titre, lignes, op_visee):
    print(f"\n{titre}  ({len(lignes)} tentatives)")
    if not lignes:
        print("  aucune tentative sur disque — ce cas a-t-il déjà tourné ?")
        return None
    print(f"  {'ops du design':24}{'hauteur au plan':>18}{'z max':>7}{'IoU':>8}")
    for _, ops, hauteur, z_max, iou in lignes:
        print(f"  {', '.join(ops):24}{hauteur or '?':>18}"
              f"{z_max if z_max is not None else '?':>7}{iou:>8.3f}")
    adoptions = sum(1 for _, ops, _, _, _ in lignes if op_visee in ops)
    moyenne = sum(l[4] for l in lignes) / len(lignes)
    print(f"  -> op `{op_visee}` adoptée {adoptions}/{len(lignes)}   IoU moyen {moyenne:.3f}")
    return len(lignes), adoptions, moyenne


def comparer(nom, exp):
    print(f"\n{'=' * 70}\n{nom.upper()} — l'op `{exp['op_visee']}` est-elle bloquée "
          f"par le prompt ?\n{'=' * 70}")
    resume = []
    for titre, cas, clause in exp["versions"]:
        bilan = _bloc(f"{titre}   [{clause}]", releve(cas, exp["reference"]), exp["op_visee"])
        if bilan:
            resume.append((titre, *bilan))

    grille_ref = build_reference(exp["reference"])
    print("\nPOINTS DE REPÈRE")
    for libelle, op in exp["reperes"]:
        design = {"name": "x", "bbox": exp["bbox"], "ops": [{**op, "color": "white"}]}
        print(f"  {libelle:28}IoU {voxel_iou(grille_ref, compile_design(design)):.3f}")
    # L'op visee et la reference sortent de la MEME fonction de src/primitives.py :
    # son 1.000 est une identite, pas une performance.
    print(f"  (l'op `{exp['op_visee']}` vaut 1.000 par construction : même rastériseur)")

    if resume:
        print(f"\n{'version':34}{'n':>4}{'adoptée':>10}{'IoU moyen':>12}")
        for titre, n, adoptions, moyenne in resume:
            print(f"{titre:34}{n:>4}{f'{adoptions}/{n}':>10}{moyenne:>12.3f}")


if __name__ == "__main__":
    demandees = sys.argv[1:] or list(EXPERIENCES)
    inconnues = [n for n in demandees if n not in EXPERIENCES]
    if inconnues:
        print(f"Forme inconnue : {', '.join(inconnues)}. "
              f"Connues : {', '.join(EXPERIENCES)}.")
        sys.exit(1)
    for nom in demandees:
        comparer(nom, EXPERIENCES[nom])
