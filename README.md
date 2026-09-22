# Pipeline LEGO — génération de modèles 3D depuis un prompt

Génère des modèles LEGO à partir d'une description en langage naturel, via un LLM local
(LM Studio), en **vraies pièces du catalogue LDraw**, rendus dans Blender.

Le principe directeur : **le LLM ne produit jamais la géométrie finale.** Il produit un
plan en texte, puis un programme d'opérations volumétriques (DSL). Du code Python
déterministe compile ce programme en briques réelles. Ça retire au modèle deux
responsabilités qu'il assume mal — garantir la légalité géométrique et poser les briques
une à une — et ne lui laisse que celle où il est bon : imaginer la forme.

---

## Prérequis

| Composant | Rôle | Obligatoire pour |
|---|---|---|
| **Python 3.11+** | — | tout |
| **LM Studio** | serveur LLM local sur `http://localhost:1234` | toute génération |
| **Blender** | rendu des images | rendu |
| **Bibliothèque LDraw** | définitions des pièces réelles (`LDConfig.ldr`, `parts/`) | export `.ldr`, catalogue |
| **LeoCAD** (ou LDView / LPub3D) | visualiser un `.ldr` | vérification visuelle |
| **Add-on ImportLDraw** | importer un `.ldr` dans Blender | rendu des vraies pièces |

Un script vérifie tout ça d'un coup, en lecture seule :

```powershell
python tools/check_env.py
```

### ⚠️ Piège LM Studio à connaître

LM Studio applique **en fallback silencieux** le *system prompt par défaut* et le
*Structured Output* configurés sur le modèle dans son interface, dès qu'une requête API
n'envoie pas son propre `response_format`. Un schéma oublié dans ces réglages écrase
alors vos prompts sans aucun message d'erreur — le modèle répond hors sujet et on
soupçonne le code à tort.

**Vérifiez que ces deux champs sont vides** dans les paramètres du modèle chargé.
Symptôme typique : le modèle renvoie toujours la même structure JSON quel que soit le
prompt (y compris sur un prompt sans rapport).

### Réglages du modèle

Longueur de contexte **≥ 16384** recommandée. En dessous, les modèles un peu gros se
font tronquer silencieusement. `tools/check_env.py` signale la valeur chargée.

### Chemins

Les chemins locaux sont centralisés dans `config.py` (exécutable Blender, URL et nom du
modèle LM Studio). La bibliothèque LDraw est cherchée automatiquement dans les
emplacements usuels — y compris un sous-dossier `ldraw/` imbriqué et un `Documents/`
redirigé par OneDrive (voir `src/catalog.py`).

---

## Installation

```powershell
pip install -r requirements.txt
```

---

## Les deux pipelines

Le dépôt contient deux chaînes de génération. La v0 reste en place comme référence de
comparaison mesurée ; la v1 est celle qui est développée.

### v0 — historique (cubes bruts)

Le LLM sort directement une liste de cubes. Pas de vraies pièces, pas de garantie
géométrique.

```
prompts.json → pipeline.py → src/prompt.py (LM Studio) → src/blender_script.py → src/img_output.py → final.png
```

```powershell
python pipeline.py
```

Une itération qui échoue est journalisée et **n'interrompt pas** les suivantes ; voir
`errors_summary.txt` en fin de run.

### v1 — DSL contraint (vraies pièces LEGO)

```
prompt
  ├─ [A] src/planner.py     LLM libre, température 0.7          → plan.md
  ├─ [B] src/designer.py    LLM contraint par le schéma DSL      → design.json
  │       └─ boucle de réparation (max 2) alimentée par [D]
  ├─ [C] src/compiler.py    DSL → grille de voxels               ─┐
  │      src/tiler.py       voxels → vraies pièces du catalogue   ├─ déterministe
  │      src/ldraw_writer.py pièces → model.ldr                  ─┘
  └─ [D] src/validator.py   contrôles géométriques               → report.json
```

```powershell
# chaîne complète depuis un prompt
python -m src.designer "Une petite maison avec un toit plat." outputs/mon_essai

# depuis un design.json déjà écrit (aucun appel LLM)
python -m tools.build_from_design prompts/fewshot/maison.json
```

---

## Le DSL

Un design est un **programme d'opérations volumétriques**, pas une liste de briques —
20 lignes décrivent 400 pièces. Coordonnées entières : X/Y en tenons, Z en plates
(1 brique = 3 plates).

```json
{
  "name": "bateau",
  "bbox": [16, 6, 8],
  "ops": [
    {"op": "box",    "from": [0, 0, 0], "to": [15, 5, 1], "color": "reddish_brown"},
    {"op": "box",    "from": [4, 1, 2], "to": [11, 4, 3], "color": "tan"},
    {"op": "column", "at": [7, 2, 4], "height": 4, "color": "white"}
  ]
}
```

Opérations : `box`, `carve`, `column`, `layer` (grille ASCII), `mirror`,
`translate_group`. Schéma complet et palette (12 couleurs) dans `src/dsl_schema.py`.

Le schéma JSON est le principal outil d'alignement : bien borné, il rend le modèle
*incapable* d'émettre une coordonnée aberrante ou une couleur hors palette.

---

## Évaluation

Deux jeux de test figés, et deux métriques complémentaires.

```powershell
python -m eval.run_eval --version v1 --n 3                        # objets sémantiques
python -m eval.run_eval --version v1 --n 3 --testset eval/primitives_testset.json
python -m eval.run_eval --compare eval/results/v0_<date>.csv eval/results/v1_<date>.csv
```

| Jeu de test | Contenu | Mesure |
|---|---|---|
| `eval/testset.json` | 20 prompts, 4 catégories × 3 niveaux | validité géométrique |
| `eval/primitives_testset.json` | 9 formes (cube, cylindre, sphère…) | **IoU** vs référence exacte |

La métrique **IoU** (`src/primitives.py`) compare la forme produite à une référence
calculée analytiquement : sur un prompt sémantique, un mauvais résultat ne dit pas *où*
ça casse ; sur « un cube 8×8×8 », l'écart devient un chiffre. Elle est invariante à la
translation mais pas à la taille, et s'applique à v0 comme à v1.

Si un run est interrompu, les designs déjà produits survivent sur disque et les métriques
se recalculent sans aucun appel LLM :

```powershell
python -m tools.recover_eval eval/results/_work/v1 v1_recovered
```

---

## Tests

```powershell
python -m pytest tests/ -q
```

Couvrent le schéma DSL, le compilateur, le pavage, le validateur et les primitives —
y compris un test qui verrouille le fait que le DSL peut exprimer *exactement* un
cylindre ou une sphère (IoU aller-retour = 1.0).

---

## Organisation des sorties

```
outputs/
  run_<YYYYMMDD_HHMMSS>/          # un dossier horodaté par exécution de pipeline.py
    prompt_001/iteration_001/
      prompt.txt                  # prompt utilisé
      plan.md                     # plan généré (v1)
      design.json                 # programme DSL validé (v1)
      model.json                  # briques validées (v0)
      report.json                 # rapport du validateur
      *_request.json              # payload exact envoyé à LM Studio
      *_raw.txt                   # réponse brute
      *_meta.json                 # tokens, durée, température, seed, tentatives
      render_{top,bottom,left,right}.png
      final.png                   # planche 2×2
      log.txt                     # journal de l'itération
      error.txt                   # uniquement en cas d'échec
```

Deux exécutions successives n'écrasent jamais leurs résultats.

---

## État d'avancement

**Fait** — assainissement (appel LLM centralisé, journalisation complète, coordonnées
entières, sorties horodatées) ; compilateur DSL → voxels ; pavage en vraies pièces avec
décalage des joints type appareillage de maçonnerie ; export `.ldr` vérifié
visuellement dans LeoCAD ; catalogue et palette vérifiés contre la vraie bibliothèque
LDraw ; validateur géométrique ; harnais d'évaluation et ligne de base v0 mesurée ;
planificateur, designer contraint et boucle de réparation.

**Résultat mesuré (v0 → v1, sur la connexité)** — les catégories qui marchaient déjà se
maintiennent (véhicules 15/15 → 14/14, bâtiments 15/15 → 13/13) ; les créatures passent
de **0/15 à 4/6** structures d'un seul tenant. 32 générations sur 33 n'ont eu besoin
d'aucune réparation.

**En cours** — évaluation sur les formes primitives, pour isoler ce qui casse. Hypothèse
à tester : les formes exprimables en simples `box` devraient bien passer, les formes
courbes s'effondrer — non parce que le DSL ne peut pas les exprimer (c'est prouvé qu'il
peut), mais parce qu'elles obligent le modèle à rastériser des cercles en ASCII, de
l'arithmétique voxel mentale. Si ça se confirme, la réponse est d'ajouter des opérations
`cylinder`/`sphere` au DSL, pas de mieux formuler les prompts.

**Pas commencé** — rendu Blender via ImportLDraw (les rendus actuels sont encore des
cubes), nomenclature des pièces (BOM), notice de montage, similarité sémantique
(CLIP/VLM).
