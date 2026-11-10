"""Validateur geometrique (Phase 2, tache 2.1). Deterministe, aucun appel LLM.

Remplace le jugement subjectif par un score reproductible. Chaque erreur bloquante ou
avertissement nomme la piece/l'operation et les coordonnees concernees, pour pouvoir
etre renvoye tel quel a un LLM dans la boucle de reparation (Phase 3).

Note : les erreurs ne referencent pas systematiquement un numero d'operation DSL — le
compilateur (src.compiler) n'enregistre pas quelle operation a ecrit chaque voxel en
dernier (ajouter cette tracabilite est une extension possible, pas necessaire pour la
validation elle-meme). Les messages referencent piece/position, ce qui reste largement
actionnable pour une boucle de reparation.
"""

import sys

import numpy as np

from src.tiler import joint_alignment_rate

_NEIGHBORS_6 = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]


def _footprint(p):
    """Emprise (w, d) reellement occupee, en tenant compte de la rotation eventuelle."""
    return (p.part.d, p.part.w) if p.rotated else (p.part.w, p.part.d)


def check_collisions(placements, voxels):
    """BLOQUANT — deux pieces qui occupent le meme voxel.

    Le pavage de src.tiler ne peut pas en produire (il marque les voxels couverts au
    fur et a mesure) : ce controle existe en defense en profondeur, pour le jour ou
    des placements viendraient d'ailleurs.
    """
    claim_count = np.zeros(voxels.shape, dtype=np.int16)
    errors = []
    for p in placements:
        w, d = _footprint(p)
        region = claim_count[p.x:p.x + w, p.y:p.y + d, p.z:p.z + p.part.h]
        if np.any(region > 0):
            errors.append(
                f"collision : piece {p.part.ldraw_id} en ({p.x},{p.y},{p.z}) "
                "chevauche une piece deja placee"
            )
        claim_count[p.x:p.x + w, p.y:p.y + d, p.z:p.z + p.part.h] += 1
    return errors


def check_floating(placements, voxels):
    """AVERTISSEMENT — piece n'ayant aucun voxel rempli juste sous son emprise.

    Volontairement non bloquant (voir la justification detaillee dans validate) :
    le controle se declenche aussi sur des constructions parfaitement normales,
    typiquement les plaques centrales d'un toit plat au-dessus d'une piece creuse.
    Une piece posee au sol (z=0) est evidemment exclue.
    """
    return [
        f"piece flottante : {p.part.ldraw_id} en ({p.x},{p.y},{p.z}) — "
        f"aucun voxel rempli juste en-dessous (z={p.z - 1}), rien ne la soutient"
        for p in pieces_flottantes(placements, voxels)
    ]


def pieces_flottantes(placements, voxels):
    """Liste les placements sans rien sous leur emprise. Donnee STRUCTUREE.

    Separe de check_floating pour que la notice puisse rattacher chaque piece a son
    etape de montage : re-extraire des coordonnees depuis des phrases serait fragile.
    """
    flottantes = []
    for p in placements:
        if p.z == 0:
            continue  # repose au sol (couche interne 0)
        w, d = _footprint(p)
        if not np.any(voxels[p.x:p.x + w, p.y:p.y + d, p.z - 1]):
            flottantes.append(p)
    return flottantes


MIN_VOXELS_WARNING = 8


def check_not_empty(voxels):
    """Un modele vide passait tous les autres controles a vide et decrochait 100/100 :
    aucune collision, aucune piece flottante, aucune composante disjointe... parce
    qu'il n'y a rien. C'est bloquant, sinon "ne rien produire" est la strategie
    gagnante triviale pour le generateur."""
    filled = int(np.count_nonzero(voxels))
    if filled == 0:
        return ["modele vide : aucun voxel rempli, il n'y a rien a construire"], []
    if filled < MIN_VOXELS_WARNING:
        return [], [f"modele minuscule : seulement {filled} voxels remplis"]
    return [], []


def check_connectivity(voxels):
    """Retourne (nb_composantes, erreurs). union-find (BFS 6-connexe) sur les voxels
    pleins ; 0 ou 1 composante = OK, plus de 1 = structure disjointe (bloquant)."""
    filled = {tuple(v) for v in np.argwhere(voxels != 0).tolist()}
    if not filled:
        return 0, []

    visited = set()
    components = []
    for start in filled:
        if start in visited:
            continue
        stack = [start]
        visited.add(start)
        comp = {start}
        while stack:
            cx, cy, cz = stack.pop()
            for dx, dy, dz in _NEIGHBORS_6:
                nb = (cx + dx, cy + dy, cz + dz)
                if nb in filled and nb not in visited:
                    visited.add(nb)
                    comp.add(nb)
                    stack.append(nb)
        components.append(comp)

    errors = []
    if len(components) > 1:
        sizes = sorted((len(c) for c in components), reverse=True)
        errors.append(
            f"structure disjointe : {len(components)} composantes non connectees "
            f"(tailles {sizes}) — au moins une partie du modele n'est reliee a rien"
        )
    return len(components), errors


def check_bbox_respect(design):
    """AVERTISSEMENT — operation dont les coordonnees sortent du bbox declare.

    Analyse le DSL source, pas la grille : le compilateur ayant deja clampe, l'ecart
    serait invisible sur le resultat. Un debordement signale surtout un modele qui
    s'est trompe dans ses propres dimensions.
    """
    warnings = []
    bbox = design["bbox"]

    def check_point(op_idx, key, point):
        """Signale chaque composante hors des bornes du bbox."""
        for axis, v in enumerate(point):
            if v < 0 or v >= bbox[axis]:
                warnings.append(
                    f"op #{op_idx} ({key}) : coordonnee {v} (axe {axis}) hors du "
                    f"bbox declare {bbox} — sera clampee au compile"
                )

    for i, op in enumerate(design["ops"]):
        kind = op["op"]
        if kind in ("box", "carve"):
            check_point(i, "from", op["from"])
            check_point(i, "to", op["to"])
        elif kind == "column":
            check_point(i, "at", op["at"])
            if op["at"][2] + op["height"] > bbox[2]:
                warnings.append(f"op #{i} (column) : depasse le bbox en Z, sera clampee")
        elif kind == "layer":
            if not (0 <= op["z"] < bbox[2]):
                warnings.append(f"op #{i} (layer) : z={op['z']} hors bbox {bbox}")

    return warnings


def check_symmetry(voxels, design):
    """AVERTISSEMENT — symetrie annoncee par un `mirror` mais non verifiee au final.

    Ne se declenche que si le design utilise effectivement mirror. Une operation
    posee APRES le mirror n'est pas dupliquee : c'est la cause habituelle de l'ecart.
    Tolerance de 5 % pour ne pas signaler un detail isole.
    """
    warnings = []
    axes = {op["axis"] for op in design["ops"] if op["op"] == "mirror"}
    for axis in axes:
        axis_idx = {"x": 0, "y": 1, "z": 2}[axis]
        mirrored = np.flip(voxels, axis=axis_idx)
        total = np.count_nonzero(voxels)
        mismatch = int(np.count_nonzero((voxels != 0) != (mirrored != 0)))
        if total and mismatch / total > 0.05:
            warnings.append(
                f"symetrie declaree sur l'axe {axis} mais {mismatch} voxels "
                f"({mismatch / total:.0%}) ne correspondent pas a leur miroir"
            )
    return warnings


def check_fill_rate(voxels):
    """AVERTISSEMENT au-dela de 85 % — volume plein / volume du bbox.

    Un taux tres eleve veut dire que le modele a rempli sa boite englobante : il a
    produit un pave, pas un objet. Retourne (taux, avertissements) car le taux sert
    aussi de metrique dans le rapport.
    """
    bbox_volume = voxels.size
    filled = int(np.count_nonzero(voxels))
    rate = filled / bbox_volume if bbox_volume else 0.0
    warnings = []
    if rate > 0.85:
        warnings.append(f"taux de remplissage {rate:.0%} > 85% — pave plein, pas un objet")
    return rate, warnings


def check_center_of_mass(voxels):
    """Verifie que le centre de masse (projete sur X/Y) tombe au-dessus d'un voxel
    rempli de la couche du sol (z=0) — indice informel de stabilite, pas une physique
    reelle."""
    filled = np.argwhere(voxels != 0)
    if len(filled) == 0:
        return []
    cx = round(filled[:, 0].mean())
    cy = round(filled[:, 1].mean())
    cx = min(max(cx, 0), voxels.shape[0] - 1)
    cy = min(max(cy, 0), voxels.shape[1] - 1)
    if voxels[cx, cy, 0] == 0:
        return [(
            f"centre de masse projete en ({cx},{cy}) ne repose sur aucun voxel du sol "
            "(z=0) — la structure pourrait etre instable/desequilibree"
        )]
    return []


def check_ground_contact(voxels):
    """Verifie que le modele touche le sol (z=0). Retourne (z_min, avertissements).

    Defaut mesure : le modele decale ses indices et demarre a z=1 (8 designs sur 26 du
    jeu primitives). check_center_of_mass le signalait deja, mais en parlant
    d'instabilite — un diagnostic trompeur qui envoyait la boucle de reparation
    corriger l'equilibre au lieu de l'origine. Ici le message nomme la vraie cause.

    Volontairement SANS penalite de score propre : le meme defaut coute deja -5 via
    check_center_of_mass, et double-penaliser casserait la comparaison avec les CSV
    d'evaluation deja mesures.
    """
    filled = np.argwhere(voxels != 0)
    if len(filled) == 0:
        return None, []
    z_min = int(filled[:, 2].min())
    if z_min == 0:
        return 0, []
    return z_min, [(
        f"le modele ne touche pas le sol : sa partie la plus basse est a z={z_min} "
        f"au lieu de z=0 (decale tout de {z_min} plate(s) vers le bas)"
    )]


def resume_constructibilite(rapport):
    """Traduit les metriques du rapport en phrases lisibles par un humain.

    Le validateur parle en vocabulaire interne — composantes connexes, voxels, z_min —
    qui ne veut rien dire pour quelqu'un qui veut juste savoir si son modele tient
    debout. Aucun calcul ici : uniquement de la mise en mots de ce qui est deja mesure.

    Retourne {"ok": bool, "phrases": [str]}. Une liste vide signifie que tout va bien,
    et l'appelant doit le dire explicitement plutot que d'afficher un encadre vide.

    Note : server/static/index.html refait cette traduction en JavaScript, parce que le
    conteneur de l'API ne contient pas src/. Garder les deux formulations alignees.
    """
    m = (rapport or {}).get("metrics", {})
    phrases = []

    n_comp = m.get("n_components")
    if n_comp and n_comp > 1:
        phrases.append(f"Le modele est en {n_comp} morceaux separes : il ne tiendra pas "
                       f"d'un seul tenant.")

    z_min = m.get("z_min")
    if z_min:
        phrases.append(f"La partie la plus basse flotte a {z_min} plate(s) au-dessus du "
                       f"sol au lieu de reposer dessus.")

    n_flot = len(m.get("flottantes") or [])
    if n_flot:
        phrases.append(f"{n_flot} piece(s) n'ont rien juste en dessous : il faudra les "
                       f"maintenir en place le temps de poser la suite.")

    taux = m.get("fill_rate")
    if taux and taux > 0.85:
        phrases.append(f"Le modele est rempli a {taux:.0%} : c'est quasiment un bloc "
                       f"plein, et il consommera beaucoup de pieces.")

    return {"ok": not phrases, "phrases": phrases}


def validate(voxels, placements, design):
    """Retourne un report dict : {"score": 0-100, "errors": [...], "warnings": [...]}."""
    empty_errors, empty_warnings = check_not_empty(voxels)
    collision_errors = check_collisions(placements, voxels)
    floating_errors = check_floating(placements, voxels)
    n_components, connectivity_errors = check_connectivity(voxels)
    bbox_warnings = check_bbox_respect(design)
    symmetry_warnings = check_symmetry(voxels, design)
    fill_rate, fill_warnings = check_fill_rate(voxels)
    com_warnings = check_center_of_mass(voxels)
    z_min, ground_warnings = check_ground_contact(voxels)
    joints_rate = joint_alignment_rate(placements) if placements else 0.0
    floating_rate = len(floating_errors) / len(placements) if placements else 0.0

    # "piece flottante" est un AVERTISSEMENT, pas un bloquant — ecart assume avec la
    # table du plan. Raison mesuree : le controle par piece ("rien juste en-dessous de
    # SON emprise") se declenche sur des constructions LEGO parfaitement normales, par
    # ex. les plaques d'un toit plat au-dessus d'une piece creuse, alors que la
    # structure est une seule composante connexe. Utilise comme gate bloquant il faisait
    # echouer quasiment toute generation v1 (et declenchait des reparations inutiles,
    # tres couteuses en temps). La connexite (union-find) reste bloquante : c'est elle
    # qui attrape le vrai defaut grave (membre detache flottant loin du corps).
    errors = empty_errors + collision_errors + connectivity_errors
    warnings = (empty_warnings + floating_errors + bbox_warnings + symmetry_warnings
                + fill_warnings + com_warnings + ground_warnings)

    score = 100.0
    score -= 100 if empty_errors else 0
    score -= 15 * min(len(collision_errors), 4)
    # TAUX, et non compte plafonne : a 5 points la piece, un modele de 456 pieces
    # atteignait le plafond de -20 avec 11 flottantes (2,4 %) quand un modele de 7
    # pieces ne pouvait structurellement pas l'atteindre. La penalite croissait donc
    # avec la taille a defaut egal. Mesure sur les 16 rapports archives : +19 points
    # sur les 5 modeles concernes, aucune regression sur les 11 autres.
    # Meme poids maximal que joint_alignment_rate, dont c'est le meme patron.
    score -= 20 * floating_rate
    score -= 25 if n_components > 1 else 0
    score -= 10 if fill_rate > 0.85 else 0
    score -= 10 * min(len(symmetry_warnings), 3)
    score -= 20 * joints_rate
    score -= 5 * min(len(com_warnings), 1)
    score = max(0.0, min(100.0, score))

    return {
        "score": round(score, 1),
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "n_pieces": len(placements),
            "n_components": n_components,
            "fill_rate": round(fill_rate, 3),
            "joint_alignment_rate": round(joints_rate, 3),
            "z_min": z_min,
            # Coordonnees des pieces sans support, pour que la notice puisse nommer
            # les etapes concernees (US-03). Donnee deja calculee, pas un recalcul.
            "flottantes": [[p.x, p.y, p.z] for p in pieces_flottantes(voxels=voxels,
                                                                     placements=placements)],
        },
    }


if __name__ == "__main__":
    import json

    from src.compiler import compile_design
    from src.tiler import tile

    if len(sys.argv) < 2:
        print("Usage: python -m src.validator design.json")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        design_data = json.load(f)

    voxels_data = compile_design(design_data)
    placements_data = tile(voxels_data)
    report = validate(voxels_data, placements_data, design_data)
    print(json.dumps(report, indent=2, ensure_ascii=False))
