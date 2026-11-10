"""Pavage voxels -> vraies pieces LEGO (Phase 1, tache 1.5). Deterministe, aucun appel LLM.

Algorithme glouton, couche par couche (z croissant), decrit dans le plan section
"Tache 1.5" : a chaque voxel plein et non couvert, on cherche la piece du catalogue au
volume maximal (w*d*h) qui tient entierement dans des voxels pleins non couverts de la
meme couleur, avec une penalite pour les pieces dont un bord coincide exactement avec un
bord de la couche z-1 (regle de maconnerie : evite que le mur se fende verticalement).

La piece 3024 (plate 1x1) garantit toujours une couverture : l'algorithme se termine.
"""

from collections import namedtuple

import numpy as np

from src.catalog import DIRECTION_RAMPE, PARTS, est_une_pente

# `retournee` ajoute le demi-tour : avec `rotated` elle donne les QUATRE orientations
# dont les pentes ont besoin, la ou briques et plates n'en demandaient que deux. Valeur
# par defaut, pour que les milliers d'appels existants a 6 arguments restent valides.
# L'empreinte ne depend que de `rotated` : un demi-tour n'echange pas w et d.
Placement = namedtuple("Placement", ["part", "color_index", "x", "y", "z", "rotated",
                                     "retournee"], defaults=(False,))

_SORTED_PARTS = sorted(PARTS, key=lambda p: -(p.w * p.d * p.h))


def _remaining_height(voxels, x, y, z, color, max_h=3):
    """Compte les couches de MEME couleur empilees a partir de (x, y, z).

    Plafonne a max_h=3, hauteur de la plus haute piece du catalogue : au-dela, la
    reponse ne changerait aucun choix. Sert a savoir si une brique (3 plates) est
    envisageable ici, ou s'il faut se rabattre sur une plate (1 plate).
    """
    z_size = voxels.shape[2]
    h = 0
    while z + h < z_size and h < max_h and voxels[x, y, z + h] == color:
        h += 1
    return h


def _fits(voxels, covered, x, y, z, w, d, h, color):
    """Teste si une piece w x d x h tient a (x, y, z).

    Trois conditions : rester dans la grille, ne recouvrir aucun voxel deja pris
    par une autre piece, et n'occuper que des voxels pleins de la MEME couleur
    (une piece est monochrome, elle ne peut pas chevaucher deux couleurs).
    """
    x_size, y_size, z_size = voxels.shape
    if x + w > x_size or y + d > y_size or z + h > z_size:
        return False
    region_voxels = voxels[x:x + w, y:y + d, z:z + h]
    region_covered = covered[x:x + w, y:y + d, z:z + h]
    if np.any(region_covered):
        return False
    return bool(np.all(region_voxels == color))


def _interior_edges(x, y, w, d, x_size, y_size):
    """Bords d'une piece, en excluant les bords qui coincident avec le contour exterieur
    de la grille (ce ne sont pas des joints internes, ils sont toujours "alignes" d'une
    couche a l'autre par construction et n'indiquent aucune fissure)."""
    xs = {v for v in (x, x + w) if 0 < v < x_size}
    ys = {v for v in (y, y + d) if 0 < v < y_size}
    return xs, ys


def _joint_penalty(prev_layer_edges, x, y, w, d, x_size, y_size):
    """Compte les bords de la piece qui prolongeraient un joint de la couche du dessous.

    Zero = la piece decale ses joints par rapport a l'assise inferieure (bon).
    Chaque coincidence exacte vaut 1 point de penalite.
    """
    xs, ys = _interior_edges(x, y, w, d, x_size, y_size)
    penalty = len(xs & prev_layer_edges["x"]) + len(ys & prev_layer_edges["y"])
    return penalty


def _rampe_libre(voxels, covered, x, y, z, w, d, h, direction):
    """La rampe d'une pente a-t-elle la place de descendre ?

    Une pente n'est pas un pave : elle remplit sa rangee haute, puis deborde d'une
    rangee en descendant. Cette rangee doit etre VIDE dans la grille — sinon la pente
    retirerait de la matiere que le modele demande — et pas deja reclamee par une
    autre piece, sinon deux pentes face a face se percuteraient dans le creux qui les
    separe, ce qu'aucun controle en aval ne verrait (le validateur ne connait que
    l'empreinte haute).

    Elle doit aussi rester DANS la grille : laisser une rampe depasser reviendrait a
    ignorer le bbox declare par le modele.
    """
    dx, dy = direction
    x_size, y_size, _ = voxels.shape
    # la rampe occupe la rangee adjacente, sur toute la largeur de l'empreinte haute
    rx = x + dx * w if dx else x
    ry = y + dy * d if dy else y
    rw, rd = (1, d) if dx else (w, 1)
    if rx < 0 or ry < 0 or rx + rw > x_size or ry + rd > y_size:
        return False
    region = voxels[rx:rx + rw, ry:ry + rd, z:z + h]
    if np.any(region != 0):
        return False
    return not np.any(covered[rx:rx + rw, ry:ry + rd, z:z + h])


def _rien_au_dessus(voxels, x, y, z, w, d, h):
    """Une pente ne supporte rien : son dessus est incline. Elle n'est donc legitime
    que la ou le modele s'arrete, c'est-a-dire sur une marche ou un bord de toit."""
    z_size = voxels.shape[2]
    if z + h >= z_size:
        return True
    return not np.any(voxels[x:x + w, y:y + d, z + h])


def _pente_posable(voxels, covered, x, y, z, part, w, d, direction):
    """Toutes les conditions de pose d'une pente, en un seul endroit."""
    return (_rien_au_dessus(voxels, x, y, z, w, d, part.h)
            and _rampe_libre(voxels, covered, x, y, z, w, d, part.h, direction))


def _best_candidate(voxels, covered, x, y, z, color, remaining, prev_layer_edges, x_size, y_size):
    """Choisit la meilleure piece posable a (x, y, z), ou None si aucune ne tient.

    Ordre de preference : penalite de joint d'ABORD, volume ensuite. Cette priorite
    est le coeur de l'algorithme et le fruit d'un debogage : avec l'ordre inverse
    (volume d'abord, penalite en simple departage), un mur uniforme se faisait paver
    a l'identique a chaque assise — 100 % de joints alignes, soit une fissure
    verticale continue. En faisant primer la penalite, l'algorithme accepte une piece
    un peu plus petite pour decaler le joint, et le decalage type appareillage de
    maconnerie emerge tout seul (mesure a 0 % sur le mur de test).

    Les deux orientations sont essayees pour les pieces non carrees.
    """
    best = None
    for part in _SORTED_PARTS:
        if part.h > remaining:
            continue
        pente = est_une_pente(part)
        # Une pente a besoin des QUATRE orientations (sa rampe a un sens), la ou une
        # piece pleine n'en distingue que deux.
        if pente:
            orientations = [(part.w, part.d, False, False), (part.d, part.w, True, False),
                            (part.w, part.d, False, True), (part.d, part.w, True, True)]
        else:
            orientations = [(part.w, part.d, False, False)]
            if part.w != part.d:
                orientations.append((part.d, part.w, True, False))
        for w, d, rotated, retournee in orientations:
            if not _fits(voxels, covered, x, y, z, w, d, part.h, color):
                continue
            if pente and not _pente_posable(voxels, covered, x, y, z, part, w, d,
                                            DIRECTION_RAMPE[(rotated, retournee)]):
                continue
            penalty = _joint_penalty(prev_layer_edges, x, y, w, d, x_size, y_size)
            # Ordre : penalite de joint, PUIS pente, puis volume. La pente passe avant
            # le volume parce que c'est tout l'interet de l'avoir au catalogue — sinon
            # une brique plus longue la battrait systematiquement et aucun toit ne
            # serait jamais incline. Elle reste apres la penalite de joint : un toit
            # joli ne vaut pas un mur qui se fend.
            key = (penalty, 0 if pente else 1, -(w * d * part.h))
            if best is None or key < best[0]:
                best = (key, part, w, d, rotated, retournee)
    return best


def tile(voxels):
    """Retourne la liste des Placement couvrant tous les voxels pleins de `voxels`.

    Assert (garanti par le fallback 1x1) qu'aucun voxel plein ne reste non couvert.
    """
    x_size, y_size, z_size = voxels.shape
    covered = np.zeros_like(voxels, dtype=bool)
    placements = []
    # edges_by_z[z] = bords interieurs des pieces qui OCCUPENT la couche z (pas
    # seulement celles qui y demarrent) — indispensable pour qu'une brique haute de 3
    # couches continue de "compter" comme bord pour la couche juste au-dessus d'elle.
    edges_by_z = [{"x": set(), "y": set()} for _ in range(z_size)]

    for z in range(z_size):
        prev_layer_edges = edges_by_z[z - 1] if z > 0 else {"x": set(), "y": set()}
        for y in range(y_size):
            for x in range(x_size):
                if voxels[x, y, z] == 0 or covered[x, y, z]:
                    continue
                color = voxels[x, y, z]
                remaining = _remaining_height(voxels, x, y, z, color)
                best = _best_candidate(voxels, covered, x, y, z, color, remaining,
                                        prev_layer_edges, x_size, y_size)
                if best is None:
                    raise RuntimeError(
                        f"Aucune piece ne peut couvrir le voxel ({x},{y},{z}) — "
                        "ne devrait jamais arriver (fallback plate 1x1 garanti)."
                    )
                _, part, w, d, rotated, retournee = best
                covered[x:x + w, y:y + d, z:z + part.h] = True
                if est_une_pente(part):
                    # La rampe est marquee couverte bien qu'elle soit VIDE : rien ne
                    # doit venir s'y loger, et surtout pas la rampe d'une autre pente.
                    dx, dy = DIRECTION_RAMPE[(rotated, retournee)]
                    rx, ry = (x + dx * w if dx else x), (y + dy * d if dy else y)
                    rw, rd = (1, d) if dx else (w, 1)
                    covered[rx:rx + rw, ry:ry + rd, z:z + part.h] = True
                placements.append(Placement(part, int(color), x, y, z, rotated,
                                            retournee))
                xs, ys = _interior_edges(x, y, w, d, x_size, y_size)
                for zz in range(z, min(z + part.h, z_size)):
                    edges_by_z[zz]["x"].update(xs)
                    edges_by_z[zz]["y"].update(ys)

    uncovered = int(np.count_nonzero((voxels != 0) & (~covered)))
    assert uncovered == 0, f"{uncovered} voxels non couverts apres tiling"

    return placements


def joint_alignment_rate(placements):
    """Fraction des bords INTERNES (le contour exterieur ne compte pas) des pieces
    d'une couche qui coincident exactement avec un bord de la couche immediatement
    inferieure. 0 = aucun alignement (bon), 1 = tout aligne (mur qui se fend)."""
    if not placements:
        return 0.0

    x_size = max(p.x + (p.part.d if p.rotated else p.part.w) for p in placements)
    y_size = max(p.y + (p.part.w if p.rotated else p.part.d) for p in placements)

    edges_by_z = {}
    for p in placements:
        w, d = (p.part.d, p.part.w) if p.rotated else (p.part.w, p.part.d)
        xs, ys = _interior_edges(p.x, p.y, w, d, x_size, y_size)
        e = edges_by_z.setdefault(p.z, {"x": set(), "y": set()})
        e["x"].update(xs)
        e["y"].update(ys)

    zs = sorted(edges_by_z)
    total = 0
    aligned = 0
    for i in range(1, len(zs)):
        cur, prev = edges_by_z[zs[i]], edges_by_z[zs[i - 1]]
        for axis in ("x", "y"):
            for e in cur[axis]:
                total += 1
                if e in prev[axis]:
                    aligned += 1
    return aligned / total if total else 0.0
