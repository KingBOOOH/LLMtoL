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

Trois points d'entrée, **une seule chaîne** derrière :

| | Pour quoi |
|---|---|
| `python lego.py "<prompt>"` | un modèle complet, en une commande |
| `python pipeline.py` | un lot depuis `prompts.json` |
| l'interface web | un prompt, plusieurs variantes, un choix (voir *Pile web*) |

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
# UN modèle complet, en une commande : plan, design, .ldr, 4 rendus, planche,
# nomenclature, notice — dans un dossier horodaté
python lego.py "Un phare blanc avec une lanterne rouge au sommet."

# en bornant la taille, et en proposant plusieurs variantes
python lego.py "Une petite maison" --taille petite --iterations 3

# un lot depuis prompts.json
python pipeline.py

# le design seul, sans rendu ni notice
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
| `LEGO_PIECES_PAR_ETAPE` | `8` | pièces par étape ; **si tu la fixes, le regroupement automatique est désactivé** |
| `LEGO_NOTICE_MAX_ETAPES` | `40` | plafond du nombre d'étapes (voir *La notice*) |

**Choix du moteur, mesuré.** EEVEE est écarté : il rend les pièces en aplat uniforme, les
groupes de nœuds de l'add-on datant de Blender 2.81. Workbench est rapide et détaillé mais
son fond reste le gris du thème, non pilotable. Cycles rend correctement et respecte le
fond configuré, pour 51 s sur un modèle de 1267 pièces.

---

---

## Pile web

Une interface pour saisir une description, comparer plusieurs variantes et garder celle
qu'on préfère. Base, API et interface tournent en conteneurs ; **la génération reste sur
l'hôte**, parce qu'un conteneur Linux ne peut pas exécuter `blender.exe`.

```
Docker                            Hôte Windows
  api   FastAPI + interface         agent.py
          dépose / lit les jobs  ──►  prend un job en attente
  db    postgres  ◄────────────────   lance la chaîne de génération
                                        LM Studio :1234
  outputs/ monté en lecture seule       blender.exe
```

L'interface affiche une **barre de progression alimentée par des chiffres réels** :
`design 2/4`, `rendu des étapes 38/46`. La progression du rendu — la phase la plus
longue — est lue dans la sortie de Blender, qui annonce déjà chaque modèle du lot. Quand
une étape n'a pas de sous-progression mesurable (planification, écriture de la notice),
la barre passe en mode indéterminé plutôt que d'afficher un pourcentage inventé.

L'agent traite **un job à la fois**, le plus ancien d'abord. Un job en file indique
combien de générations passent avant lui, au lieu de laisser croire à un blocage.

Un job interrompu pendant la production des variantes **repart de zéro** : celles déjà
produites sont incomplètes, et surtout leurs indices rejoueraient à l'identique, ce qui
violerait la contrainte d'unicité et ferait échouer le job au lieu de le reprendre. En
revanche, si l'utilisateur avait déjà choisi, son choix est conservé et seule la phase
notice est rejouée.

L'agent émet son signe de vie depuis un **thread dédié**, et non depuis sa boucle
principale : celle-ci est bloquée plusieurs minutes pendant un rendu Blender, et l'API
déclarait alors l'agent mort au moment précis où il travaillait le plus (mesuré :
3 min 05 sans signal pour un délai de 90 s).

La **base sert de file d'attente** — pas de Redis, pas de Celery. Ça règle aussi un
problème réel : le tueur de tâches par pression mémoire a déjà tué des runs (LM Studio
retient ~18 Go). Un job interrompu reste en base et l'agent le reprend au redémarrage,
au lieu d'être perdu.

```powershell
copy .env.example .env     # puis adapter si besoin
docker compose up -d       # base + API + interface
python agent.py            # sur la machine où sont Blender et LM Studio
```

Interface : <http://127.0.0.1:8000/ui/> — Vue 3 chargé par `<script>`, donc **aucune
chaîne de build**, pas de node dans l'image.

| Aspect | Choix |
|---|---|
| Ports | base 5442, API 8000, **uniquement sur `127.0.0.1`** |
| Authentification | aucune, d'où l'écoute sur la boucle locale seulement |
| `outputs/` | monté en **lecture seule** dans l'API : seul l'agent y écrit |
| Chemins en base | **relatifs** à `outputs/` — l'agent est sous Windows, l'API sous Linux |

### Ce qui est stocké

Deux tables. `jobs` (une soumission : prompt, taille, état, étape en cours) et
`variantes` (une par variante produite).

La colonne qui compte est **`design_json`** : environ 1 à 2 ko, et toute la chaîne en
aval étant déterministe, elle suffit à régénérer le `.ldr`, les rendus, la nomenclature
et la notice **sans un seul appel LLM**. C'est la seule chose qu'on ne peut pas
reconstruire, donc la seule qu'il faut absolument garder.

Sont aussi conservées les métriques déjà calculées par le validateur (score, composantes,
`z_min`, pièces flottantes) — l'historique devient triable par qualité pour zéro calcul
supplémentaire — et de quoi expliquer un vieux résultat (`seed`, modèle LLM, commit git).

À la sélection, les variantes non retenues sont **réduites, pas supprimées** : on garde
`design_json`, les métriques et la seed (~2 ko chacune), on efface le PDF et les rendus.
C'est la seule matière pour comprendre plus tard *ce que* le modèle rate.

La nomenclature n'est **pas** stockée : c'est une fonction pure de `design_json`.

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

### La notice

Le modèle est découpé en étapes d'un **nombre fixe de pièces**, dans l'ordre de montage
réel (z croissant : on ne pose pas une pièce sous une pièce déjà posée). Découper par
couche donnait des étapes très inégales — une couche peut contenir une pièce comme
cinquante — et on perdait le fil.

Sur un gros modèle, le nombre de pièces par étape **augmente automatiquement** pour que
le total d'étapes reste sous `LEGO_NOTICE_MAX_ETAPES`. Auparavant, au-delà de 320 pièces
les rendus d'étapes étaient purement sautés : un modèle de 1267 pièces donnait 159 étapes
et une notice **sans la moindre image**, donc inutilisable. Mieux vaut des étapes plus
chargées qu'une notice vide.

En dessous du seuil, rien ne change. Et si tu fixes `LEGO_PIECES_PAR_ETAPE`, ton choix
prime : l'adaptation est désactivée.

**Mesuré** sur un pavé creux de 476 pièces, chaîne déterministe seule (aucun appel LLM) :

| | |
|---|---|
| étapes | **40** — exactement le plafond, contre 60 sans images auparavant |
| pièces par étape | 12, relevé de 8 automatiquement |
| images d'étapes produites | **40 / 40, aucune manquante** |
| vignettes de pièces | 9 (une par référence et couleur, pas une par étape) |
| découpage | 0,1 s |
| rendu des 49 images | 357 s, en **un seul** processus Blender |

Les 357 s sont le vrai coût d'une notice de gros modèle. Ils se règlent par
`LEGO_NOTICE_MAX_ETAPES` : baisser le plafond réduit proportionnellement le nombre de
rendus.

### Savoir si le modèle tient debout

Le validateur calcule déjà tout — composantes connexes, pièces sans support, contact au
sol, taux de remplissage — mais il l'écrivait en vocabulaire interne, dans un
`report.json` que personne n'ouvre. L'utilisateur recevait quatre images et devait juger
seul. C'est d'ailleurs comme ça qu'un défaut de pièces flottantes a été repéré : à l'œil,
dans LeoCAD, pas par le projet.

La notice s'ouvre donc sur un encadré « Avant de commencer », et chaque étape contenant
une pièce sans support le signale sur place. **Aucun calcul nouveau** : uniquement la
mise en mots de ce qui était déjà mesuré.

```
Avant de commencer
  • 3 pièce(s) n'ont rien juste en dessous : il faudra les maintenir
    en place le temps de poser la suite.
```

Un modèle sans défaut le dit explicitement plutôt que d'afficher un encadré vide, qui
laisserait croire à un oubli.

Deux points d'implémentation :

- `check_floating` construisait des phrases ; il s'appuie maintenant sur
  `pieces_flottantes()`, qui renvoie les **placements** eux-mêmes. Rattacher une pièce à
  son numéro d'étape en ré-extrayant des coordonnées depuis du texte aurait été fragile.
- `server/static/index.html` refait cette traduction en JavaScript, parce que le
  conteneur de l'API ne contient pas `src/`. Les deux formulations sont identiques et
  un commentaire de chaque côté le rappelle.

Un test vérifie qu'aucune phrase ne contient de vocabulaire interne — pas de « voxel »,
pas de « composante connexe ».

### La notice PDF

Un fichier unique à imprimer, produit pour chaque modèle (et, côté web, pour la seule
variante retenue — le produire pour les quatre reviendrait à en jeter trois).

| Page | Contenu |
|---|---|
| 1 | le rendu du modèle **terminé**, en grand, avec le nom, la demande d'origine, la date et le nombre de pièces |
| 2 | une présentation courte de LLMtoL et de son principe |
| 3… | les étapes de montage, précédées de l'état de constructibilité |
| fin | la liste complète des pièces : image, quantité, référence LDraw, dimensions, couleur |

**Choix technique.** `notice.html` et `bom.html` existent déjà avec leur mise en page et
leurs images. Les réécrire dans un moteur PDF donnerait deux mises en page à maintenir,
qui divergeraient. On compose donc un HTML unique et on le fait **imprimer par un
navigateur en mode headless** : Edge est livré avec Windows, Chrome fait aussi bien.
**Zéro dépendance nouvelle.**

Le chemin du navigateur vit dans `config.NAVIGATEURS_PDF`, à côté de `BLENDER_EXE` ; le
premier existant est retenu, sinon le `PATH` est consulté. Pour containeriser la
génération un jour, ajouter `/usr/bin/chromium` suffit — il y est déjà.

Mesuré : **1,7 s** pour un modèle de 37 pièces, PDF de 90 ko en 4 pages.

Deux garde-fous :

- Chaque étape porte `page-break-inside: avoid` : **aucune étape n'est coupée** entre
  deux pages, ce qui la rendrait inutilisable au montage.
- Un PDF qui échoue **n'invalide pas le modèle**, qui lui est bien généré. Le message le
  dit, le `.ldr` et la notice HTML restent là, et l'interface web affiche simplement que
  le PDF manque.

Le HTML intermédiaire est conservé à côté (`notice_pdf.html`) : il explique un PDF raté
sans avoir à tout rejouer.

### La taille demandée

`--taille petite|moyenne|grande` (ou `--bbox-max XxYxZ`) resserre les bornes du schéma
JSON envoyé au modèle. C'est une **garantie par construction**, pas une consigne : le
décodage contraint rend le modèle *incapable* d'émettre une coordonnée hors bornes.

Demander « fais environ 200 pièces » dans le prompt ne marche pas — la chaîne v0 l'a
prouvé (« grille 10×10 » → 49 cubes). Le schéma, lui, ne se négocie pas.

Point d'implémentation : resserrer le `bbox` **sans** resserrer les coordonnées ne
garantirait rien, le modèle déclarerait une boîte de 16 et y poserait des `box` allant
jusqu'à 48. `build_schema()` reconstruit donc tout de façon cohérente — `bbox`,
`from`/`to`, `at`, `height`, `diameter`, `delta` — et un test le verrouille.

**Mesuré** sur « une petite maison aux murs creux et toit plat », 3 générations par
taille, chaîne complète :

| taille | borne | bbox réellement choisi | pièces |
|---|---|---|---|
| petite | `[16,16,20]` | `[16,16,20]` aux 3 essais | 50 – 143 |
| moyenne | `[28,28,36]` | `[24,24,30]` aux 3 essais | 65 – 241 |
| grande | `[48,48,60]` | `[16,16,24]`, `[24,24,24]`, `[16,16,12]` | 137 – 189 |

**0 dépassement sur 9.** La borne est respectée à chaque fois.

Deux limites à connaître, mesurées et non contournées :

- **La borne est un plafond, pas une cible.** Pour `petite` elle mord — le modèle veut
  plus grand et se fait plafonner. Pour `grande` elle ne mord jamais : le planificateur
  choisit ce qu'il estime raisonnable pour l'objet demandé, et son prompt lui souffle
  déjà de préférer des tailles modestes. Choisir `grande` n'a donc aujourd'hui **aucun
  effet observable**. Pour qu'il en ait un, il faudrait un plancher en plus du plafond.
- **Le nombre de pièces ne suit pas la taille.** Les fourchettes se chevauchent
  largement et `grande` produit en moyenne moins de pièces que `moyenne`. Le nombre de
  pièces dépend surtout du creux du modèle, pas de son encombrement. La garantie porte
  sur la boîte englobante, pas sur le compte.

La borne appliquée est consignée dans `report.json` (`bbox_max_applique`), pour qu'un
résultat reste explicable après coup.

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

136 tests, couvrant le schéma DSL, le compilateur, le pavage, le validateur, les
primitives, la nomenclature, le découpage en étapes, la notice PDF et la
détection du piège LM Studio.
Deux d'entre eux valent d'être signalés : l'un verrouille le fait que le DSL exprime
*exactement* un cylindre ou une sphère (IoU aller-retour = 1,0), l'autre que resserrer
le `bbox` resserre **aussi** les coordonnées — sans quoi la borne de taille ne
garantirait rien.

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

Le suivi détaillé vit dans les [issues](https://github.com/KingBOOOH/LLMtoL/issues) et
le tableau du projet. Résumé :

| | Sujet | État |
|---|---|---|
| #1 | détection du piège LM Studio | fait |
| #3 | `lego.py`, un modèle en une commande | fait |
| #4 | tests de `instructions.py` et `bom.py` | fait |
| #7 | conteneurs base + API + interface | fait |
| #8 | persistance jobs et variantes | fait |
| #10 | API backend | fait |
| #11 | interface web Vue 3 | fait |
| #13 | taille du modèle bornée par le schéma | fait |
| #2 | mesure figée du jeu primitives | en cours — 3 formes sur 9 rejouées depuis la correction du `z` |
| #9 | agent de génération | fait — validé par une génération réelle de bout en bout |
| #5 | notice utilisable quelle que soit la taille | fait |
| #6 | constructibilité annoncée en français | fait |
| #12 | notice PDF | fait |

**Fait** — assainissement (appel LLM centralisé, journalisation complète, coordonnées
entières, sorties horodatées) ; compilateur DSL → voxels ; pavage en vraies pièces avec
décalage des joints type appareillage de maçonnerie ; export `.ldr` vérifié
visuellement dans LeoCAD ; catalogue et palette vérifiés contre la vraie bibliothèque
LDraw ; validateur géométrique ; harnais d'évaluation ; planificateur, designer
contraint et boucle de réparation ; rendu Blender via ImportLDraw ; nomenclature
(`bom.csv` / `bom.html`, avec vignette par référence) ; notice de montage
(`notice.html`, étapes à nombre de pièces fixe, encart des pièces de l'étape) ;
taille bornée par le schéma ; pile web (conteneurs, base, API, interface, agent).

**Résultat mesuré (v0 → v1, sur la connexité)** — les catégories qui marchaient déjà se
maintiennent (véhicules 15/15 → 14/14, bâtiments 15/15 → 13/13) ; les créatures passent
de **0/15 à 12/14** structures d'un seul tenant. 32 générations sur 33 n'ont eu besoin
d'aucune réparation.

**Le jeu de formes primitives a servi à isoler ce qui casse.** Sur un prompt
sémantique, un mauvais résultat ne dit pas *où* ça rate ; sur « un cube 8×8×8 », l'écart
devient un chiffre. Il a montré que le goulot était le modèle et non le DSL — un test
verrouille le fait que le DSL exprime un cylindre ou une sphère exactement (IoU
aller-retour = 1,0), alors que le modèle, lui, ne sait pas rastériser un cercle en ASCII.

**Effet des corrections, mesuré** — opérations `cylinder`/`sphere`, puis correction du
décalage `z`. Mesure figée : 3 tentatives par forme, via le vrai planificateur.

| | avant | après |
|---|---|---|
| IoU global | 0,614 | **0,706** |
| IoU formes courbes | 0,391 | **0,619** |
| durée moyenne, formes courbes | 339 s | **131 s** |
| IoU formes en `box` | 0,777 | 0,769 |

Les gains viennent des formes courbes : `prim_cylindre` 0,272 → **0,750** (l'opération
`cylinder` est adoptée), `prim_anneau` 0,698 → **0,838** (`cylinder` + `carve`),
`prim_sphere` 0,145 → **0,527** avec un temps divisé par trois.

**Le décalage `z` venait du planificateur, pas du designer.** Son plan écrivait
`z=1..8` et le designer obéissait au plan reçu. Corriger le prompt du designer n'a rien
changé (8/26 → 9/26) ; la consigne appartient à `prompts/planner_system.txt`. Mesuré sur
les 3 formes concernées : **9/9 hors sol → 0/9**, et IoU **0,819 → 1,000**.

Ce défaut n'était pas cosmétique. En démarrant à `z=1` dans une boîte de hauteur *n*, le
modèle se faisait tronquer d'une plate contre son propre bbox : les IoU observés valaient
exactement (n−1)/n — 7/8 pour le cube, 5/6 pour le pavé, 3/4 pour le cylindre. Une
couche entière perdue à chaque génération.

**Ce qui reste à mesurer** — les 6 autres formes n'ont pas été rejouées depuis la
correction du planificateur. La figure globale « après » est donc incomplète :

```powershell
python -u -m eval.run_eval --version v1prim --n 3 --testset eval/primitives_testset.json
```

Par petits lots de 3 ou 4 formes plutôt qu'en un seul run : un run opaque de 27
générations dure plus de trente minutes sans permettre de distinguer « bloqué » de
« lent ». Et `python -u`, sinon le journal reste vide.

**Un cas résiste** — le modèle n'adopte pas `sphere`, il reste sur `layer`. La cause est
dans le prompt figé lui-même, qui dicte la méthode : « chaque couche horizontale est un
disque, le plus large au milieu, rétrécissant vers le haut et vers le bas ». Celui du
cylindre ne prescrit rien, d'où son adoption immédiate. Ajouter un exemple few-shot avec
`sphere` a été essayé et mesuré : aucun effet sur l'adoption, IoU dégradé (0,526 →
0,258), exemple retiré.

Noter aussi qu'un IoU de 1,0 sur `prim_cylindre` signifie désormais « le modèle a choisi
la bonne opération et les bons paramètres », plus « le modèle a su tracer un cercle ».
C'est délibérément plus facile : c'est tout l'intérêt de l'opération.

**Prochain chantier** — similarité sémantique (CLIP/VLM) : sur un objet non primitif,
aucune métrique ne dit aujourd'hui si le résultat *ressemble* au prompt. LM Studio sert
plusieurs modèles simultanément, donc un VLM qui note `render_iso.png` est faisable ; à
calibrer d'abord sur le jeu primitives, où l'IoU fournit la vérité terrain.
