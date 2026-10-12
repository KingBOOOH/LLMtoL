"""Formes primitives de reference + metrique de ressemblance (IoU).

Pourquoi : sur un prompt semantique ("un chien"), un mauvais resultat ne dit pas OU ca
casse (plan ? DSL ? pavage ? limite des briques rectangulaires ?). Sur "un cube plein de
8x8x8", la reponse correcte est calculable analytiquement, donc l'ecart devient un
chiffre (IoU) au lieu d'un jugement subjectif.

Les dimensions sont donnees directement dans les unites de la grille interne :
X et Y en tenons, Z en plates (voir src.compiler). L'anisotropie reelle du LEGO
(1 tenon = 20 LDU, 1 plate = 8 LDU) n'est volontairement PAS prise en compte ici :
premier barreau de l'echelle, on veut des references sans ambiguite. Une sphere
"physiquement ronde" (donc ~2.5x plus haute en plates qu'en tenons) viendra plus tard.
"""

import numpy as np


def cube(side):
    """Cube plein : side x side x side."""
    return np.ones((side, side, side), dtype=np.int16)


def box(nx, ny, nz):
    """Pave plein."""
    return np.ones((nx, ny, nz), dtype=np.int16)


def cylinder(diameter, height):
    """Cylindre a section circulaire, extrude sur l'axe Z."""
    grid = np.zeros((diameter, diameter, height), dtype=np.int16)
    center = (diameter - 1) / 2.0
    radius = diameter / 2.0
    for x in range(diameter):
        for y in range(diameter):
            if (x - center) ** 2 + (y - center) ** 2 <= radius ** 2:
                grid[x, y, :] = 1
    return grid


def sphere(diameter):
    """Sphere pleine (voxelisee dans une grille cubique de cote `diameter`)."""
    grid = np.zeros((diameter, diameter, diameter), dtype=np.int16)
    center = (diameter - 1) / 2.0
    radius = diameter / 2.0
    for x in range(diameter):
        for y in range(diameter):
            for z in range(diameter):
                if (x - center) ** 2 + (y - center) ** 2 + (z - center) ** 2 <= radius ** 2:
                    grid[x, y, z] = 1
    return grid


def pyramid(base, height):
    """Pyramide a base carree : chaque couche retrecit regulierement vers le sommet."""
    grid = np.zeros((base, base, height), dtype=np.int16)
    for z in range(height):
        shrink = round(z * (base / 2.0) / height)
        lo, hi = shrink, base - shrink
        if hi <= lo:
            break
        grid[lo:hi, lo:hi, z] = 1
    return grid


def stairs(width, steps, step_height=1, step_depth=1):
    """Escalier : chaque marche monte de step_height et avance de step_depth en Y."""
    depth = steps * step_depth
    height = steps * step_height
    grid = np.zeros((width, depth, height), dtype=np.int16)
    for s in range(steps):
        y0 = s * step_depth
        z_top = (s + 1) * step_height
        grid[:, y0:y0 + step_depth, 0:z_top] = 1
    return grid


def cross(arm, thickness, height):
    """Croix (plus) extrudee sur Z : deux barres perpendiculaires centrees."""
    size = 2 * arm + thickness
    grid = np.zeros((size, size, height), dtype=np.int16)
    grid[arm:arm + thickness, :, :] = 1
    grid[:, arm:arm + thickness, :] = 1
    return grid


def ring(outer_diameter, inner_diameter, height):
    """Anneau : cylindre plein moins un cylindre interieur."""
    grid = cylinder(outer_diameter, height)
    center = (outer_diameter - 1) / 2.0
    inner_radius = inner_diameter / 2.0
    for x in range(outer_diameter):
        for y in range(outer_diameter):
            if (x - center) ** 2 + (y - center) ** 2 <= inner_radius ** 2:
                grid[x, y, :] = 0
    return grid


GENERATORS = {
    "cube": cube,
    "box": box,
    "cylinder": cylinder,
    "sphere": sphere,
    "pyramid": pyramid,
    "stairs": stairs,
    "cross": cross,
    "ring": ring,
}


def build_reference(spec):
    """spec = {"shape": "cube", "params": {"side": 8}} -> grille de reference."""
    return GENERATORS[spec["shape"]](**spec["params"])


def _crop_to_content(grid):
    """Recadre la grille sur sa boite englobante remplie (retire le vide autour).

    C'est ce qui rend l'IoU insensible a la position : un cube correct place dans un
    coin d'un grand bbox ne doit pas etre penalise pour autant.
    """
    filled = np.argwhere(grid != 0)
    if len(filled) == 0:
        return np.zeros((1, 1, 1), dtype=np.int16)
    lo = filled.min(axis=0)
    hi = filled.max(axis=0) + 1
    return grid[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]


def voxel_iou(grid_a, grid_b):
    """Intersection sur union entre deux grilles de voxels (rempli / vide).

    Les deux grilles sont d'abord recadrees sur leur contenu (l'IoU ne doit pas punir
    un modele juste parce qu'il est place a un offset different dans son bbox), puis
    completees par des zeros a une taille commune. La TAILLE, elle, compte : un cube
    6x6x6 la ou on demandait 8x8x8 doit bien etre penalise.
    """
    a = _crop_to_content(grid_a) != 0
    b = _crop_to_content(grid_b) != 0

    shape = tuple(max(a.shape[i], b.shape[i]) for i in range(3))
    pa = np.zeros(shape, dtype=bool)
    pb = np.zeros(shape, dtype=bool)
    pa[:a.shape[0], :a.shape[1], :a.shape[2]] = a
    pb[:b.shape[0], :b.shape[1], :b.shape[2]] = b

    union = np.count_nonzero(pa | pb)
    if union == 0:
        return 1.0
    return float(np.count_nonzero(pa & pb) / union)


def ascii_layers(grid, max_layers=None):
    """Coupe ASCII par couche z (debug rapide, meme format que src.compiler)."""
    x_size, y_size, z_size = grid.shape
    out = []
    for z in range(z_size if max_layers is None else min(z_size, max_layers)):
        layer = grid[:, :, z]
        out.append(f"--- z={z} ---")
        for y in range(y_size):
            out.append("".join("#" if layer[x, y] else "." for x in range(x_size)))
    return "\n".join(out)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m src.primitives <shape> [param=valeur ...]")
        print(f"Formes : {', '.join(sorted(GENERATORS))}")
        sys.exit(1)

    shape = sys.argv[1]
    params = {}
    for arg in sys.argv[2:]:
        key, value = arg.split("=")
        params[key] = int(value)

    reference = GENERATORS[shape](**params)
    print(f"{shape}{params} -> shape={reference.shape}, "
          f"{int(np.count_nonzero(reference))} voxels pleins")
    print(ascii_layers(reference, max_layers=6))
