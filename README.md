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
| **Add-on ImportLDraw** | importer un `.ldr` dans Blender | rendu (obligatoire, pas de repli) |

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

**Vérifiez que ces deux champs sont vides** dans les paramètres du modèle chargé
(*My Models* > le modèle > *System Prompt* et *Structured Output*). Symptôme typique :
le modèle renvoie toujours la même structure JSON quel que soit le prompt (y compris sur
un prompt sans rapport).

Détails constatés sur cette machine, utiles pour diagnostiquer vite :

- Les deux réglages vivent dans
  `.lmstudio/.internal/user-concrete-model-default-config/<éditeur>/<modèle>.json`
  (clés `llm.prediction.systemPrompt` et `llm.prediction.structured`), et **reviennent à
  chaque rechargement** du modèle. `lms load` n'a aucune option pour les ignorer.
- **Ni** un system prompt envoyé par la requête **ni** un `response_format` valant
  `{"type":"text"}` ne les neutralisent. Seul un `json_schema` explicite prend le dessus.
- Conséquence : `src/designer.py` est immunisé (il envoie son schéma), `src/planner.py`
  ne l'est pas — il répond `[{"action":"create","object":"cube"}]` à n'importe quelle
  demande. Donc `eval/run_eval.py`, qui passe par le planificateur, est inutilisable
  tant que le champ n'est pas vidé.

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

## La pipeline

```
prompt
  ├─ [A] src/planner.py     LLM libre, température 0.7          → plan.md
  ├─ [B] src/designer.py    LLM contraint par le schéma DSL      → design.json
  │       └─ boucle de réparation (max 2) alimentée par [D]
  ├─ [C] src/compiler.py    DSL → grille de voxels               ─┐
  │      src/tiler.py       voxels → vraies pièces du catalogue   │
  │      src/ldraw_writer.py pièces → model.ldr                   ├─ déterministe
  ├─ [D] src/validator.py   contrôles géométriques → report.json  │
  ├─ [E] src/bom.py         nomenclature → bom.csv, bom.html      │
  │      src/instructions.py étapes de montage → notice.html     ─┘
  └─ [F] src/blender_script.py  model.ldr → rendus (ImportLDraw)
         src/img_output.py      4 rendus → final.png
```

Une itération qui échoue est journalisée et **n'interrompt pas** les suivantes ; voir
`errors_summary.txt` en fin de run.

Une chaîne antérieure demandait directement au LLM une liste de cubes. Elle a été
retirée : pas de vraies pièces, aucune garantie géométrique, et 15 générations de
créatures sur 15 produisaient des membres détachés. Ses mesures de référence restent
figées dans `eval/results/` mais ne sont plus regénérables.

```powershell
# bout-en-bout depuis prompts.json : modèles, rendus, nomenclature, notices
python pipeline.py

# une seule génération, sans rendu
python -m src.designer "Une petite maison avec un toit plat." outputs/mon_essai

# depuis un design.json déjà écrit (aucun appel LLM)
python -m tools.build_from_design prompts/fewshot/maison.json
python -m src.bom prompts/fewshot/maison.json
python -m src.instructions prompts/fewshot/maison.json

# rendre N modèles en un seul processus Blender
python -m tools.render_batch --glob "outputs/run_*/prompt_*/iteration_*" --assemble
```

### Rendu

Le rendu importe `model.ldr` via l'add-on **ImportLDraw** : vraies pièces, vrais tenons,
couleurs officielles. Si l'add-on manque, le script échoue explicitement — il ne retombe
pas sur des cubes.

Les caméras sont **orthographiques** et leur `ortho_scale` est calculé depuis la boîte
englobante : deux modèles de tailles très différentes occupent la même proportion du
cadre (vérifié à 87,0 % sur un rapport de volume de 270×), condition nécessaire pour
comparer des générations entre elles.

| Variable | Défaut | Rôle |
|---|---|---|
| `LEGO_RENDER_ENGINE` | `cycles` | `cycles`, `workbench` (rapide) ou `eevee` (déconseillé) |
| `LEGO_RENDER_SAMPLES` | `64` | échantillons Cycles |
| `LEGO_RENDER_RES` | `800` | résolution en pixels |
| `LEGO_RENDER_MARGIN` | `1.15` | marge autour du modèle (1.15 = 87 % du cadre) |
| `LEGO_RENDER_VIEWS` | les 4 | vues à rendre, ex. `iso` seule |
| `LEGO_PIECES_PAR_ETAPE` | `8` | pièces par étape de notice |
| `LEGO_NOTICE_MAX_ETAPES` | `40` | au-delà, les étapes ne sont pas rendues |

**Choix du moteur, mesuré.** EEVEE est écarté : il rend les pièces en aplat uniforme, les
groupes de nœuds de l'add-on datant de Blender 2.81. Workbench est rapide et détaillé mais
son fond reste le gris du thème, non pilotable. Cycles rend correctement et respecte le
fond configuré, pour 51 s sur un modèle de 1267 pièces.

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

Opérations : `box`, `carve`, `column`, `layer` (grille ASCII), `cylinder`, `sphere`,
`mirror`, `translate_group`. Schéma complet et palette (12 couleurs) dans
`src/dsl_schema.py`.

Le schéma JSON est le principal outil d'alignement : bien borné, il rend le modèle
*incapable* d'émettre une coordonnée aberrante ou une couleur hors palette.

### Les formes courbes

`cylinder` et `sphere` existent parce que la mesure a désigné le modèle, pas le langage :
décrire un cercle avec des grilles `layer` l'oblige à rastériser de tête, et il échoue
(0,39 d'IoU contre 0,78 sur les formes en `box`). Le modèle **déclare** donc le volume,
Python le rastérise :

```json
{"op": "cylinder", "at": [1, 1, 0], "diameter": 8, "height": 12, "color": "white"}
{"op": "sphere",   "at": [1, 1, 4], "diameter": 10, "color": "green"}
```

`at` est le coin bas de la boîte englobante, pas le centre (pour un diamètre pair le
centre tomberait sur un demi-entier). Le rastérisage réutilise **les fonctions de
`src/primitives.py` qui servent déjà de référence à l'IoU** : un seul rastériseur, donc
aucune dérive possible entre ce qu'on produit et ce qu'on mesure (test verrouillé).

Deux limites à connaître :

- Une sphère est ronde **dans la grille**, donc aplatie ~2,5× à l'écran : un `diameter`
  compte autant de tenons en X/Y que de plates en Z, or un tenon fait 20 LDU et une
  plate 8. Les références d'IoU utilisent la même convention ; changer l'une sans
  l'autre fausserait la métrique.
- `translate_group` n'accepte pas `cylinder`/`sphere` en imbriqué. Chaque variante
  grossit la grammaire que LM Studio compile, et l'inférence est déjà limitée par le
  CPU — à ouvrir seulement si une mesure montre que ça manque.

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
translation mais pas à la taille.

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
      plan.md                     # plan généré (étape A)
      design.json                 # programme DSL validé (étape B)
      report.json                 # rapport du validateur
      model.ldr                   # modèle en vraies pièces du catalogue
      planner_*.json / .txt       # payload envoyé, réponse brute, métadonnées
      designer_*.json / .txt      # idem, + _repair1 / _repair2 s'il y a eu réparation
      render_{top,front,right,iso}.png
      final.png                   # planche 2×2
      bom.csv, bom.html           # nomenclature (quantités, références, couleurs)
      notice.html                 # notice de montage, une page par étape
      etapes/
        etape_001/                # un sous-modèle cumulatif par étape
          model.ldr
          render_iso.png          # rendu dézoomé (marge 1.7)
          etape.png               # rendu + encart des pièces de l'étape
        pieces/<ref>_c<couleur>/  # vignette d'une pièce, rendue une seule fois
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
LDraw ; validateur géométrique ; harnais d'évaluation ; planificateur, designer
contraint et boucle de réparation ; rendu Blender via ImportLDraw ; nomenclature
(`bom.csv` / `bom.html`, avec vignette par référence) ; notice de montage
(`notice.html`, étapes à nombre de pièces fixe, encart des pièces de l'étape).

**Résultat mesuré (v0 → v1, sur la connexité)** — les catégories qui marchaient déjà se
maintiennent (véhicules 15/15 → 14/14, bâtiments 15/15 → 13/13) ; les créatures passent
de **0/15 à 12/14** structures d'un seul tenant. 32 générations sur 33 n'ont eu besoin
d'aucune réparation.

**Résultat mesuré (formes primitives)** — l'hypothèse est confirmée et chiffrée : les
formes exprimables en simples `box` atteignent **0,78 d'IoU**, les formes courbes
**0,39**, pour un temps de génération **9× supérieur**. Le DSL n'est pas en cause — un
test verrouille le fait qu'il peut exprimer un cylindre ou une sphère exactement
(IoU aller-retour = 1,0). Le goulot est le modèle, à qui on demandait de rastériser des
cercles en ASCII, c'est-à-dire de faire de l'arithmétique voxel de tête.

**Effet des deux corrections** — le décalage `z` (prompt explicite + détection
`check_ground_contact`) et les opérations `cylinder`/`sphere` (avec un quatrième exemple
few-shot, `prompts/fewshot/phare.json`) :

| | avant | après |
|---|---|---|
| IoU formes en `box` | 0,777 | **0,886** |
| IoU formes courbes | 0,391 | **0,835** |
| durée moyenne, formes courbes | 339 s | **29 s** |
| ne touchent pas le sol | 8/26 | **0/9** |

Les deux cas qui portent le gain : `prim_cylindre` 0,272 → **1,000** (opération
`cylinder` adoptée), `prim_anneau` 0,698 → **0,947** (`cylinder` + `carve`).

**Réserve méthodologique importante.** Ces chiffres viennent du designer appelé
directement, avec un plan écrit à la main et **une** tentative par forme ; la référence
fait trois tentatives via le vrai planificateur. C'est une indication solide, pas une
mesure comparable. La mesure figée reste à refaire :

```powershell
python -m eval.run_eval --version v1prim --n 3 --testset eval/primitives_testset.json
```

Elle exige d'avoir vidé le *Structured Output* dans LM Studio (voir *Piège LM Studio*) :
le planificateur n'envoie pas de `response_format` et se fait donc détourner.

**Un cas résiste** — le modèle n'adopte pas `sphere`, il reste sur `layer` (IoU 0,526).
La cause est dans le prompt figé lui-même, qui dicte la méthode : « chaque couche
horizontale est un disque, le plus large au milieu, rétrécissant vers le haut et vers le
bas ». Le modèle obéit au prompt. Ajouter un exemple few-shot avec `sphere` a été essayé
et mesuré : aucun effet sur l'adoption et IoU dégradé (0,526 → 0,258), exemple retiré.

Noter aussi qu'un IoU de 1,0 sur `prim_cylindre` signifie désormais « le modèle a choisi
la bonne opération et les bons paramètres », plus « le modèle a su tracer un cercle ».
C'est délibérément plus facile : c'est tout l'intérêt de l'opération.

**Prochain chantier** — similarité sémantique (CLIP/VLM) : sur un objet non primitif,
aucune métrique ne dit aujourd'hui si le résultat *ressemble* au prompt. LM Studio sert
plusieurs modèles simultanément, donc un VLM qui note `render_iso.png` est faisable ; à
calibrer d'abord sur le jeu primitives, où l'IoU fournit la vérité terrain.
