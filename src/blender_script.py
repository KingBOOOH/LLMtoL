"""Rendu Blender des vraies pieces LEGO (Phase 4) : model.ldr -> quatre rendus ortho.

S'execute DANS Blender (`blender --background --python src/blender_script.py`), pas dans
l'interpreteur du projet : le module bpy n'existe qu'a l'interieur de Blender, d'ou
l'absence d'import de src.*. Le dossier de travail arrive par LEGO_WORK_DIR.

Remplace l'ancien rendu en cubes : la geometrie vient desormais de l'add-on ImportLDraw,
donc vraies pieces, vrais tenons, couleurs officielles. Si l'add-on est absent, le script
echoue avec un message explicite — il ne retombe PAS sur des cubes, ce serait revenir en
arriere sans le dire.

Cadrage : les cameras sont orthographiques et leur `ortho_scale` est calculee depuis la
boite englobante du modele importe. C'est ce qui rend deux generations comparables — un
petit et un gros modele occupent la meme proportion de l'image, condition necessaire a
toute evaluation automatique des rendus.

Moteur de rendu : CYCLES par defaut, apres comparaison des trois disponibles.
- EEVEE est ECARTE : il rend les pieces en aplat uniforme, tenons et aretes invisibles.
  Les groupes de noeuds crees par l'add-on datent de Blender 2.81 et l'EEVEE moderne ne
  sait plus les interpreter ; ni le raytracing ni l'exposition n'y changent rien.
- WORKBENCH est rapide et rend le detail correctement (il ignore ces noeuds et utilise
  la couleur d'affichage), mais son fond reste le gris clair du theme : ni le mode
  VIEWPORT ni le mode WORLD ne le pilotent une fois l'add-on passe. A reserver aux
  iterations rapides ou le fond importe peu.
- CYCLES rend correctement ET respecte le fond configure. Cout mesure : 51 s pour les
  quatre vues d'un modele de 1267 pieces (698 000 sommets), 28 s pour un petit modele.

Deux modes d'appel :
- un seul modele  : LEGO_WORK_DIR designe le dossier contenant model.ldr ;
- en lot          : LEGO_BATCH_MANIFEST designe un fichier texte listant un dossier de
  travail par ligne. Un seul processus Blender les traite tous, ce qui evite de repayer
  le demarrage a chaque modele. Gain mesure : environ 1,6 s par modele (demarrage +
  activation de l'add-on), soit ~5 min sur un benchmark de 200 — sensiblement moins que
  les 3 a 8 s supposees au depart, mais l'economie reste reelle sur les gros lots.
  Le manifeste est un FICHIER et non une liste d'arguments : Windows plafonne la ligne
  de commande a ~32 ko, vite atteint avec des chemins longs.

Variables d'environnement optionnelles :
    LEGO_RENDER_ENGINE   "cycles" (defaut), "workbench" (rapide) ou "eevee" (deconseille)
    LEGO_RENDER_SAMPLES  echantillons Cycles (defaut 64)
    LEGO_RENDER_RES      largeur en pixels (defaut 800)
"""

import math
import os
import sys

import bpy

WORK_DIR = os.environ.get("LEGO_WORK_DIR", os.getcwd())
BATCH_MANIFEST = os.environ.get("LEGO_BATCH_MANIFEST")

ENGINE = os.environ.get("LEGO_RENDER_ENGINE", "cycles").lower()
RESOLUTION = int(os.environ.get("LEGO_RENDER_RES", "800"))

# Vues : top/front/right/iso. L'isometrique est la seule sur laquelle un humain (ou un
# VLM) juge la ressemblance ; bottom et left n'apportaient presque rien par symetrie.
# Restreignable via LEGO_RENDER_VIEWS : les etapes de notice et les vignettes de pieces
# n'utilisent que l'iso, rendre les trois autres serait du calcul jete.
VIEWS = [v.strip() for v in os.environ.get("LEGO_RENDER_VIEWS",
                                           "top,front,right,iso").split(",") if v.strip()]

# Marge autour du modele. 1.15 = le modele remplit 87 % du cadre. Configurable car les
# images d'etapes de notice ont besoin d'air : l'encart des pieces occupe le coin
# superieur droit et recouvrirait l'assemblage dans les dernieres etapes.
MARGIN = float(os.environ.get("LEGO_RENDER_MARGIN", "1.15"))


class ModelError(Exception):
    """Probleme propre a UN modele (fichier absent, geometrie vide).

    Distinct de fail() qui arrete tout : en mode lot, un modele defectueux doit etre
    signale puis saute, pas emporter les suivants. fail() leve SystemExit, que le
    `except Exception` de la boucle n'attraperait pas.
    """


def fail(message):
    """Arrete le script : probleme global (add-on absent, manifeste introuvable)."""
    print(f"ERREUR: {message}")
    sys.exit(1)


def enable_importldraw():
    """Active l'add-on ImportLDraw, ou echoue en expliquant comment l'installer."""
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_importldraw")
    except Exception as e:
        fail(
            f"add-on ImportLDraw introuvable ou non activable ({e}).\n"
            "  Installer importldraw<version>.zip via Blender > Edit > Preferences >\n"
            "  Add-ons > Install, puis cocher la case pour l'activer."
        )
    if not hasattr(bpy.ops.import_scene, "importldraw"):
        fail("add-on active mais operateur import_scene.importldraw absent.")


def clear_scene():
    """Vide la scene ET purge les donnees devenues orphelines.

    La purge est indispensable en mode lot : supprimer les objets ne libere pas leurs
    maillages, materiaux et groupes de noeuds, que l'add-on recree a chaque import.
    Sans elle, la memoire grimpe modele apres modele et les materiaux s'accumulent en
    doublons (materiau.001, .002...).
    """
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    # Suppression manuelle plutot que l'operateur orphans_purge, qui exige un contexte
    # d'outliner absent en mode --background. Plusieurs passes : retirer un maillage
    # peut rendre orphelin le materiau qu'il referencait.
    for _ in range(4):
        removed = 0
        for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.node_groups,
                           bpy.data.images, bpy.data.lights, bpy.data.cameras):
            for item in list(collection):
                if item.users == 0:
                    collection.remove(item)
                    removed += 1
        if removed == 0:
            break


def import_model(path):
    """Importe le .ldr en desactivant ce que l'add-on ajoute de son propre chef.

    addEnvironment/positionCamera/importCameras a False : le script gere lui-meme
    l'eclairage et le cadrage, sinon les rendus ne seraient pas comparables entre
    generations. positionOnGround reste actif : il normalise l'assise du modele.
    """
    if not os.path.isfile(path):
        raise ModelError(f"model.ldr introuvable : {path}")

    bpy.ops.import_scene.importldraw(
        filepath=path,
        addEnvironment=False,
        positionCamera=False,
        importCameras=False,
        positionOnGround=True,
        useLogoStuds=False,
        bevelEdges=True,
        smoothParts=True,
    )

    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise ModelError("import termine mais aucune geometrie creee (.ldr vide ?)")
    print(f"Importe : {len(meshes)} pieces, "
          f"{sum(len(o.data.vertices) for o in meshes)} sommets")
    return meshes


def world_bounds(meshes):
    """Boite englobante du modele en coordonnees monde : (centre, dimensions)."""
    corners = [obj.matrix_world @ mathutils_vector(c)
               for obj in meshes for c in obj.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
    dims = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    return center, dims


def mathutils_vector(coords):
    """Convertit un triplet de bound_box en Vector (import local : bpy seulement ici)."""
    from mathutils import Vector
    return Vector(coords)


def setup_world():
    """Fond neutre uniforme, identique pour toutes les generations."""
    world = bpy.data.worlds.new("Fond") if not bpy.data.worlds else bpy.data.worlds[0]
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.05, 0.05, 0.06, 1.0)
        bg.inputs["Strength"].default_value = 1.0


def setup_lighting(center, dims):
    """Eclairage fixe par lumieres SOLAIRES, identique pour toutes les generations.

    Des lampes de type AREA obligeraient a calibrer la puissance en fonction du carre
    de la distance, donc de la taille du modele — l'add-on importe a l'echelle reelle
    (1 tenon = 8 mm), et l'ecart de taille entre un petit objet et un gros suffit a
    passer de la sous-exposition a la surexposition. Une lumiere SUN a une irradiance
    independante de la distance : meme exposition quelle que soit la taille, ce qui est
    exactement la propriete recherchee pour comparer des generations entre elles.
    """
    key = bpy.data.lights.new(name="Key", type="SUN")
    key.energy = 3.0
    key.angle = math.radians(10)  # ombres legerement adoucies
    key_obj = bpy.data.objects.new("Key", key)
    key_obj.location = (center[0], center[1], center[2] + max(dims) + 1)
    key_obj.rotation_euler = (math.radians(50), 0, math.radians(35))
    bpy.context.collection.objects.link(key_obj)

    fill = bpy.data.lights.new(name="Fill", type="SUN")
    fill.energy = 1.0
    fill_obj = bpy.data.objects.new("Fill", fill)
    fill_obj.location = (center[0], center[1], center[2] + max(dims) + 1)
    fill_obj.rotation_euler = (math.radians(65), 0, math.radians(-120))
    bpy.context.collection.objects.link(fill_obj)


def setup_render():
    """Moteur, resolution et echantillonnage explicites — jamais les defauts de Blender.

    Workbench pour iterer vite (pas d'eclairage calcule), EEVEE pour le rendu final.
    """
    scene = bpy.context.scene
    if ENGINE.startswith("cycles"):
        scene.render.engine = "CYCLES"
        scene.cycles.samples = int(os.environ.get("LEGO_RENDER_SAMPLES", "64"))
        scene.cycles.use_denoising = True
    elif ENGINE.startswith("eevee"):
        # L'identifiant EEVEE a change d'une version de Blender a l'autre
        # (BLENDER_EEVEE_NEXT en 4.2-4.4, revenu a BLENDER_EEVEE en 5.x).
        for candidate in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            try:
                scene.render.engine = candidate
                break
            except TypeError:
                continue
        if hasattr(scene, "eevee"):
            scene.eevee.taa_render_samples = 32
            # Indispensable : sans raytracing l'EEVEE moderne rend les pieces en
            # aplat uniforme, tenons et aretes invisibles.
            if hasattr(scene.eevee, "use_raytracing"):
                scene.eevee.use_raytracing = True
    else:
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.display.shading.light = "STUDIO"
        scene.display.shading.color_type = "MATERIAL"
        scene.display.render_aa = "8"
        # Fond via le monde : le mode VIEWPORT est ignore au rendu final une fois
        # l'add-on passe (constate empiriquement : reglage bien pris en compte au
        # moment du rendu, mais fond quand meme gris clair). WORLD reprend le monde
        # sombre configure par setup_world, donc un fond identique a celui de Cycles.
        scene.display.shading.background_type = "WORLD"

    scene.render.resolution_x = RESOLUTION
    scene.render.resolution_y = RESOLUTION
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    print(f"Moteur : {scene.render.engine}, resolution : {RESOLUTION}x{RESOLUTION}")
    if scene.render.engine == "BLENDER_WORKBENCH":
        sh = scene.display.shading
        print(f"  fond workbench : type={sh.background_type} "
              f"couleur={tuple(round(c, 3) for c in sh.background_color)}")


def make_camera():
    """Cree la camera orthographique unique, repositionnee pour chaque vue."""
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.type = "ORTHO"
    cam_data.clip_start = 0.01
    cam_data.clip_end = 10000
    cam_obj = bpy.data.objects.new("Camera", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj
    return cam_obj


def place_camera(cam_obj, view, center, dims):
    """Place la camera pour une vue et adapte ortho_scale a la taille du modele.

    L'etendue visible depend de la vue : de dessus on voit X et Y, de face X et Z,
    etc. En isometrique les trois axes se projettent, d'ou la diagonale. C'est ce
    calcul qui garantit qu'un petit et un gros modele occupent la meme proportion
    de l'image.
    """
    cx, cy, cz = center
    dx, dy, dz = dims
    distance = max(dims) * 4 + 10  # sans effet sur la taille en ortho, evite le clipping

    if view == "top":
        cam_obj.location = (cx, cy, cz + distance)
        cam_obj.rotation_euler = (0, 0, 0)
        extent = max(dx, dy)
    elif view == "front":
        cam_obj.location = (cx, cy - distance, cz)
        cam_obj.rotation_euler = (math.radians(90), 0, 0)
        extent = max(dx, dz)
    elif view == "right":
        cam_obj.location = (cx + distance, cy, cz)
        cam_obj.rotation_euler = (math.radians(90), 0, math.radians(90))
        extent = max(dy, dz)
    elif view == "iso":
        d = distance / math.sqrt(3)
        cam_obj.location = (cx + d, cy - d, cz + d)
        cam_obj.rotation_euler = (math.radians(54.736), 0, math.radians(45))
        extent = math.sqrt(dx ** 2 + dy ** 2 + dz ** 2)
    else:
        fail(f"vue inconnue : {view}")

    cam_obj.data.ortho_scale = max(extent, 0.001) * MARGIN


def render_model(work_dir):
    """Rend les quatre vues d'un modele. Retourne True en cas de succes.

    Reconstruit toute la scene a chaque appel : en mode lot, laisser trainer les
    objets, lampes ou camera du modele precedent fausserait le cadrage du suivant.
    """
    model_path = os.path.join(work_dir, "model.ldr")
    if not os.path.isfile(model_path):
        print(f"  IGNORE : model.ldr absent dans {work_dir}")
        return False

    clear_scene()
    meshes = import_model(model_path)
    center, dims = world_bounds(meshes)
    print(f"  boite englobante : {dims[0]:.2f} x {dims[1]:.2f} x {dims[2]:.2f}")

    setup_world()
    setup_lighting(center, dims)
    setup_render()
    cam_obj = make_camera()

    for view in VIEWS:
        place_camera(cam_obj, view, center, dims)
        bpy.context.scene.render.filepath = os.path.join(work_dir, f"render_{view}.png")
        bpy.ops.render.render(write_still=True)

    print(f"  4 rendus ecrits dans {work_dir}")
    return True


def read_manifest(path):
    """Lit un manifeste : un dossier de travail par ligne, vides et # ignores."""
    if not os.path.isfile(path):
        fail(f"manifeste introuvable : {path}")
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f
                if line.strip() and not line.startswith("#")]


def main():
    """Rend un modele (LEGO_WORK_DIR) ou tout un lot (LEGO_BATCH_MANIFEST).

    Code de retour non nul seulement si AUCUN modele n'a pu etre rendu : un lot
    partiellement reussi reste un succes, les echecs individuels sont listes.
    """
    enable_importldraw()

    if BATCH_MANIFEST:
        work_dirs = read_manifest(BATCH_MANIFEST)
        print(f"Mode lot : {len(work_dirs)} modeles")
    else:
        work_dirs = [WORK_DIR]

    succes = 0
    for i, work_dir in enumerate(work_dirs, 1):
        print(f"[{i}/{len(work_dirs)}] {os.path.basename(work_dir.rstrip(os.sep))}")
        # Un modele qui echoue ne doit pas interrompre le lot : meme principe que
        # l'isolation des iterations dans pipeline.py.
        try:
            if render_model(work_dir):
                succes += 1
        except Exception as e:
            print(f"  ECHEC : {e}")

    print(f"Rendus termines : {succes}/{len(work_dirs)}")
    if succes == 0:
        sys.exit(1)


main()
