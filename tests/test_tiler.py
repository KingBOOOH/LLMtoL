import random

import numpy as np
import pytest

from src.tiler import joint_alignment_rate, tile


def test_solid_8x8x3_block_uses_large_parts_not_64_ones():
    voxels = np.ones((8, 8, 3), dtype=np.int16)  # une seule couleur (index 1)
    placements = tile(voxels)

    assert len(placements) < 20, f"trop de pieces ({len(placements)}) pour un pave plein"
    ones = [p for p in placements if p.part.w * p.part.d == 1]
    assert len(ones) == 0, "ne devrait utiliser aucune piece 1x1 sur un pave plein"
    max_footprint = max(p.part.w * p.part.d for p in placements)
    assert max_footprint >= 16, "devrait utiliser au moins une piece de type 2x8 (16 tenons)"


@pytest.mark.parametrize("seed", range(10))
def test_random_shapes_fully_covered(seed):
    rng = random.Random(seed)
    x, y, z = rng.randint(2, 10), rng.randint(2, 10), rng.randint(1, 6)
    voxels = np.zeros((x, y, z), dtype=np.int16)
    n_colors = 3
    fill_prob = 0.5
    for xi in range(x):
        for yi in range(y):
            for zi in range(z):
                if rng.random() < fill_prob:
                    voxels[xi, yi, zi] = rng.randint(1, n_colors)

    if not voxels.any():
        voxels[0, 0, 0] = 1  # evite le cas degenere "grille totalement vide"

    placements = tile(voxels)  # leve si un voxel reste non couvert (assert interne)

    covered = np.zeros_like(voxels, dtype=bool)
    for p in placements:
        w, d = (p.part.d, p.part.w) if p.rotated else (p.part.w, p.part.d)
        covered[p.x:p.x + w, p.y:p.y + d, p.z:p.z + p.part.h] = True
    assert np.array_equal(covered & (voxels != 0), voxels != 0)


def test_joint_alignment_rate_low_on_wall():
    voxels = np.ones((10, 2, 9), dtype=np.int16)
    placements = tile(voxels)
    rate = joint_alignment_rate(placements)
    assert rate < 0.25, f"taux de joints alignes trop eleve : {rate:.2%}"


def test_tile_empty_grid_returns_no_placements():
    voxels = np.zeros((3, 3, 3), dtype=np.int16)
    assert tile(voxels) == []


def test_single_voxel_falls_back_to_1x1_plate():
    voxels = np.zeros((3, 3, 3), dtype=np.int16)
    voxels[1, 1, 1] = 2
    placements = tile(voxels)
    assert len(placements) == 1
    assert placements[0].part.w == 1 and placements[0].part.d == 1
