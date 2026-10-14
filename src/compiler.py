"""Compilateur DSL -> grille de voxels (Phase 1, tache 1.4). Deterministe, aucun appel LLM.

Ne devine jamais si une operation depasse le bbox : elle est clampee dans la grille et un
avertissement est journalise (jamais d'exception a ce stade — la validation stricte est
la responsabilite de src/validator.py, Phase 2).
"""

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.dsl_schema import PALETTE_NAMES, validate_design
from src.primitives import cylinder as _cylinder_mask
from src.primitives import sphere as _sphere_mask

COLOR_INDEX = {name: i + 1 for i, name in enumerate(PALETTE_NAMES)}  # 0 = vide
INDEX_COLOR = {v: k for k, v in COLOR_INDEX.items()}


def _clamp(point, maxima):
    """Ramene un point dans [0, maxima] axe par axe.

    Retourne (point_clampe, a_ete_modifie) — le booleen sert a n'emettre un
    avertissement que si un clamp a reellement eu lieu.
    """
    clamped = [max(0, min(v, m)) for v, m in zip(point, maxima)]
    return clamped, clamped != list(point)


def _apply_box(grid, op, warnings, op_idx):
    """Remplit un pave plein de la couleur demandee (coins inclus)."""
    shape = grid.shape
    (x0, y0, z0), c0 = _clamp(op["from"], [s - 1 for s in shape])
    (x1, y1, z1), c1 = _clamp(op["to"], [s - 1 for s in shape])
    if c0 or c1:
        warnings.append(f"op #{op_idx} (box) : coordonnees hors grille, clampees")
    # `from` et `to` sont triees : le modele peut les donner dans n'importe quel ordre.
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    za, zb = sorted((z0, z1))
    grid[xa:xb + 1, ya:yb + 1, za:zb + 1] = COLOR_INDEX[op["color"]]


def _apply_carve(grid, op, warnings, op_idx):
    """Vide une region (met les voxels a 0). Meme geometrie que box, sans couleur."""
    shape = grid.shape
    (x0, y0, z0), c0 = _clamp(op["from"], [s - 1 for s in shape])
    (x1, y1, z1), c1 = _clamp(op["to"], [s - 1 for s in shape])
    if c0 or c1:
        warnings.append(f"op #{op_idx} (carve) : coordonnees hors grille, clampees")
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    za, zb = sorted((z0, z1))
    grid[xa:xb + 1, ya:yb + 1, za:zb + 1] = 0


def _apply_column(grid, op, warnings, op_idx):
    """Empile un pilier 1x1 de `height` plates a partir de `at`.

    Une colonne trop haute est tronquee au sommet de la grille plutot que refusee.
    """
    shape = grid.shape
    (x, y, z0), clamped_at = _clamp(op["at"], [s - 1 for s in shape])
    z1 = z0 + op["height"] - 1
    clamped_height = z1 > shape[2] - 1
    z1 = min(z1, shape[2] - 1)
    if clamped_at or clamped_height:
        warnings.append(f"op #{op_idx} (column) : hors grille, clampee")
    grid[x, y, z0:z1 + 1] = COLOR_INDEX[op["color"]]


def _apply_layer(grid, op, warnings, op_idx):
    """Peint un motif ASCII a plat sur une seule couche z.

    Convention de lecture de `grid` : une ligne de la liste = un pas en Y, un
    caractere dans la ligne = un pas en X, a partir de `origin`. '#' remplit,
    tout le reste laisse vide. C'est l'operation la plus expressive du DSL (seule
    facon de decrire une forme non rectangulaire), mais elle oblige le modele a
    rasteriser lui-meme ses contours.
    """
    shape = grid.shape
    ox, oy = op["origin"]
    z = op["z"]
    if not (0 <= z <= shape[2] - 1):
        # Un z hors grille rend toute l'operation inapplicable : on l'ignore en bloc.
        warnings.append(f"op #{op_idx} (layer) : z={z} hors grille, operation ignoree")
        return
    color_idx = COLOR_INDEX[op["color"]]
    any_clamped = False
    for row_i, row in enumerate(op["grid"]):
        for col_i, ch in enumerate(row):
            if ch != "#":
                continue
            x, y = ox + col_i, oy + row_i
            if not (0 <= x <= shape[0] - 1 and 0 <= y <= shape[1] - 1):
                # Ici on ignore voxel par voxel : un motif qui deborde garde sa
                # partie utile au lieu d'etre perdu entierement.
                any_clamped = True
                continue
            grid[x, y, z] = color_idx
    if any_clamped:
        warnings.append(f"op #{op_idx} (layer) : certains voxels hors grille, ignores")


def _stamp(grid, mask, at, color_idx, warnings, op_idx, kind):
    """Pose un masque booleen dans la grille, son coin bas place en `at`.

    Decoupe le masque au lieu de refuser l'operation : un volume qui depasse garde sa
    partie utile, comme `layer`. Gere aussi un `at` negatif (possible via les deltas de
    translate_group) en rognant le masque du cote bas.
    """
    lo = [max(0, at[i]) for i in range(3)]
    crop = [lo[i] - at[i] for i in range(3)]  # part du masque sortie par le bas
    hi = [min(at[i] + mask.shape[i], grid.shape[i]) for i in range(3)]
    if any(hi[i] <= lo[i] for i in range(3)):
        warnings.append(f"op #{op_idx} ({kind}) : entierement hors grille, ignoree")
        return
    sub = mask[crop[0]:crop[0] + hi[0] - lo[0],
               crop[1]:crop[1] + hi[1] - lo[1],
               crop[2]:crop[2] + hi[2] - lo[2]]
    # Vue sur la grille : l'affectation par masque booleen ecrit bien dans l'original.
    grid[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]][sub] = color_idx
    if sub.shape != mask.shape:
        warnings.append(f"op #{op_idx} ({kind}) : depasse la grille, partie hors grille ignoree")


def _apply_cylinder(grid, op, warnings, op_idx):
    """Cylindre plein a section circulaire, extrude sur Z, rasterise par src.primitives.

    `at` est le coin bas de la boite englobante (comme `from` de box), pas le centre :
    pour un diametre pair le centre tomberait sur un demi-entier.
    """
    mask = _cylinder_mask(op["diameter"], op["height"]) != 0
    _stamp(grid, mask, op["at"], COLOR_INDEX[op["color"]], warnings, op_idx, "cylinder")


def _apply_sphere(grid, op, warnings, op_idx):
    """Sphere pleine, rasterisee par src.primitives (meme coin bas que cylinder).

    `diameter` est un diametre REEL, en tenons : la sphere occupe donc environ 2,5x
    plus de plates en Z que de tenons en X/Y (un tenon vaut 20 LDU, une plate 8).
    src.primitives.hauteur_sphere() donne cette emprise, et build_schema borne le
    diametre en consequence pour que la sphere ne puisse pas deborder du bbox.

    Il n'y a rien a synchroniser a la main : la reference d'IoU et ce rasteriseur sont
    la MEME fonction. Elles ont longtemps partage une sphere cubique dans la grille,
    donc aplatie a l'ecran ; ce qui a tranche est dans docs/mesures.md.
    """
    mask = _sphere_mask(op["diameter"]) != 0
    _stamp(grid, mask, op["at"], COLOR_INDEX[op["color"]], warnings, op_idx, "sphere")


def _apply_mirror(grid, op, warnings, op_idx):
    """Duplique par symetrie tout ce qui est deja construit, autour du milieu d'un axe.

    Agit sur l'etat courant de la grille, donc sa position dans la liste d'operations
    compte : ce qui vient apres ne sera pas mis en miroir.
    """
    axis_idx = {"x": 0, "y": 1, "z": 2}[op["axis"]]
    # Copie indispensable : sans elle on lirait des voxels qu'on vient d'ecrire,
    # et le miroir se propagerait sur lui-meme.
    source = grid.copy()
    xs, ys, zs = np.nonzero(source)
    limit = grid.shape[axis_idx] - 1
    coords = [xs, ys, zs]
    coords[axis_idx] = limit - coords[axis_idx]  # i -> taille-1-i sur l'axe choisi
    grid[tuple(coords)] = source[xs, ys, zs]


def _shift_point(p, delta):
    """Translate un point de `delta` (utilitaire de translate_group)."""
    return [p[i] + delta[i] for i in range(3)]


def _shift_op(op, delta):
    """Retourne une COPIE de l'operation dont les coordonnees sont translatees.

    Chaque type d'operation porte sa position dans un champ different, d'ou le
    branchement. La copie evite de modifier le design d'origine, qui est reutilise
    a chaque repetition de translate_group.
    """
    shifted = dict(op)
    if op["op"] in ("box", "carve"):
        shifted["from"] = _shift_point(op["from"], delta)
        shifted["to"] = _shift_point(op["to"], delta)
    elif op["op"] in ("column", "cylinder", "sphere"):
        shifted["at"] = _shift_point(op["at"], delta)
    elif op["op"] == "layer":
        # origin est en 2D (X, Y) ; le decalage vertical s'applique au champ z.
        shifted["origin"] = [op["origin"][0] + delta[0], op["origin"][1] + delta[1]]
        shifted["z"] = op["z"] + delta[2]
    return shifted


def _apply_translate_group(grid, op, warnings, op_idx):
    """Repete un groupe d'operations `count` fois, chaque copie decalee de `delta`.

    La copie d'indice 0 est posee sans decalage : `count` compte donc l'original.
    Sert a decrire une serie reguliere (fenetres, barreaux, marches) en une seule
    operation au lieu de N.
    """
    delta = op["delta"]
    for i in range(op["count"]):
        cur_delta = [delta[k] * i for k in range(3)]
        for nested in op["ops"]:
            # op_idx enrichi ("3.t2") pour que les avertissements designent la
            # bonne repetition et pas juste le groupe.
            _dispatch(grid, _shift_op(nested, cur_delta), warnings, f"{op_idx}.t{i}")


def _dispatch(grid, op, warnings, op_idx):
    """Aiguille une operation vers son implementation.

    Leve ValueError sur une operation inconnue : le schema JSON est cense l'avoir
    deja refusee, donc arriver ici signale un bug, pas une entree utilisateur.
    """
    kind = op["op"]
    if kind == "box":
        _apply_box(grid, op, warnings, op_idx)
    elif kind == "carve":
        _apply_carve(grid, op, warnings, op_idx)
    elif kind == "column":
        _apply_column(grid, op, warnings, op_idx)
    elif kind == "layer":
        _apply_layer(grid, op, warnings, op_idx)
    elif kind == "cylinder":
        _apply_cylinder(grid, op, warnings, op_idx)
    elif kind == "sphere":
        _apply_sphere(grid, op, warnings, op_idx)
    elif kind == "mirror":
        _apply_mirror(grid, op, warnings, op_idx)
    elif kind == "translate_group":
        _apply_translate_group(grid, op, warnings, op_idx)
    else:
        raise ValueError(f"op #{op_idx} : operation inconnue '{kind}'")


def compile_design(design):
    """Compile un design DSL en grille de voxels.

    Retourne un tableau (X, Y, Z) int16 : 0 = vide, n = index couleur (COLOR_INDEX).
    Les operations sont appliquees DANS L'ORDRE, chacune ecrasant les voxels qu'elle
    touche — c'est ce qui permet a un carve de creuser une box posee juste avant.

    Le design est revalide contre le schema (defense en profondeur : un design
    malforme leve, c'est un bug d'appelant). En revanche tout ce qui deborde du bbox
    est clampe avec un avertissement, jamais une exception : juger si le resultat est
    acceptable revient a src.validator, pas au compilateur.
    """
    validate_design(design)
    grid = np.zeros(tuple(design["bbox"]), dtype=np.int16)
    warnings = []
    for i, op in enumerate(design["ops"]):
        _dispatch(grid, op, warnings, i)
    for w in warnings:
        print(f"WARNING: {w}")
    return grid


def print_ascii_layers(grid):
    """Affiche une coupe ASCII par couche z non vide (inspection visuelle rapide)."""
    x_size, y_size, z_size = grid.shape
    for z in range(z_size):
        layer = grid[:, :, z]
        if not layer.any():
            continue
        print(f"--- z={z} ---")
        for y in range(y_size):
            print("".join("#" if layer[x, y] else "." for x in range(x_size)))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.compiler design.json")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        design_data = json.load(f)

    voxels = compile_design(design_data)
    out_path = os.path.splitext(sys.argv[1])[0] + "_voxels.npy"
    np.save(out_path, voxels)
    print(f"Voxels sauvegardes : {out_path} (shape={voxels.shape})")
    print_ascii_layers(voxels)
