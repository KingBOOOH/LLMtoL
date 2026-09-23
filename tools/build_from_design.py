"""Chaine design.json -> voxels -> placements -> model.ldr en une commande, avec
timings par etape (Phase 1, tache 1.7). Deterministe, aucun appel LLM.

Usage: python -m tools.build_from_design design.json [output.ldr]
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.compiler import compile_design
from src.ldraw_writer import write_ldr
from src.tiler import joint_alignment_rate, tile


def build(design_path, output_path=None):
    """Compile un design.json en .ldr et affiche le temps passe a chaque etape.

    Tout est deterministe : aucun appel LLM, donc rejouable a volonte sur un design
    deja genere (utile pour tester une modification du pavage ou de l'export sans
    repayer une generation).
    """
    with open(design_path, "r", encoding="utf-8") as f:
        design = json.load(f)

    if output_path is None:
        output_path = os.path.splitext(design_path)[0] + ".ldr"

    t0 = time.time()
    voxels = compile_design(design)
    t1 = time.time()
    placements = tile(voxels)
    t2 = time.time()
    write_ldr(placements, output_path, design.get("name", "model"))
    t3 = time.time()

    print(f"{design.get('name', design_path)} :")
    print(f"  compile_design : {t1 - t0:.3f}s  (bbox={tuple(design['bbox'])}, "
          f"{int((voxels != 0).sum())} voxels pleins)")
    print(f"  tile           : {t2 - t1:.3f}s  ({len(placements)} pieces, "
          f"joints alignes={joint_alignment_rate(placements):.1%})")
    print(f"  write_ldr      : {t3 - t2:.3f}s  -> {output_path}")
    print(f"  total          : {t3 - t0:.3f}s")

    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m tools.build_from_design design.json [output.ldr]")
        sys.exit(1)

    out = sys.argv[2] if len(sys.argv) > 2 else None
    build(sys.argv[1], out)
