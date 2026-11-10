# Flux de données — types exacts et exemples réels

Ce document complète la section « Les flux de données » du guide complet, qui dit où chaque
donnée **atterrit**. Celui-ci dit **de quel type** elle est, **à quoi elle ressemble**, et
**qui la passe à qui**.

Tous les exemples viennent d'une génération réelle, conservée sur disque :
`outputs/job_f6967ade-99fd-4d06-8315-307224f53ab1/variante_02/`, prompt « une tours
basique de chevalier (forme cylindrique avec une base) », taille `grande`, 485 pièces.
Rien n'est inventé ni reconstitué.

---

## 1. Les trois frontières de processus

La chaîne est presque entièrement un enchaînement d'appels de fonctions Python dans un
seul processus. Il n'y a que **trois** endroits où une donnée quitte ce processus, et ce
sont les seuls qui imposent un format sérialisé :

| Frontière | Transport | Donnée qui passe |
|---|---|---|
| `src/llm_client.py` → LM Studio | HTTP POST JSON, `localhost:1234` | messages + schéma JSON → texte |
| chaîne → Blender | **fichier sur disque** + variables d'environnement | `model.ldr` → `render_*.png` |
| API (conteneur) ↔ agent (hôte) | **base Postgres** + dossier `outputs/` monté | lignes `jobs` / `variantes` |

Partout ailleurs, les objets circulent en mémoire. Les fichiers écrits au passage sont
des **traces** et des **livrables** : plus rien ne les relit, à une exception près —
`model.ldr`, que Blender relit depuis le disque dans un processus séparé. C'est
exactement ce qui rend le rendu rejouable sans rien régénérer.

---

## 2. Repères et unités — à lire avant tout le reste

Deux systèmes de coordonnées coexistent, et les confondre est la première source
d'erreur du projet.

### Grille interne (DSL, compilateur, pavage, validateur)

| Axe | Unité | Sens |
|---|---|---|
| X | tenon (stud) | horizontal |
| Y | tenon | horizontal |
| Z | **plate** | vertical, vers le haut, `z=0` = sol |

1 brique = 3 plates. Entiers, 0-indexés.

### Repère LDraw (fichier `.ldr`, Blender)

| Axe | Unité | Sens |
|---|---|---|
| X | LDU | horizontal |
| Y | LDU | vertical, **vers le bas** |
| Z | LDU | horizontal |

`LDU_PER_STUD = 20`, `LDU_PER_PLATE = 8` (`src/ldraw_writer.py`, relevés
empiriquement).

**La grille n'est donc pas isotrope** : un cube de 8×8×8 dans la grille mesure
160×64×160 LDU, soit un objet écrasé 2,5× à l'écran. Constantes nommées dans
`src/primitives.py` (`LDU_PAR_TENON`, `LDU_PAR_PLATE`) — ne jamais recopier le 2,5
ailleurs.

---

## 3. Étape A — prompt → plan

| | |
|---|---|
| Producteur | `src/planner.py` → `src/llm_client.py` → LM Studio |
| Consommateur | `src/designer.py` |
| Type en mémoire | `str` |
| Trace disque | `plan.md`, `planner_request.json`, `planner_raw.txt`, `planner_meta.json` |

### Ce qui part sur le réseau

`planner_request.json`, charge utile exacte du `requests.post` :

```json
{
  "model": "qwen/qwen3-coder-30b",
  "messages": [
    { "role": "system", "content": "You are a LEGO model planner. …" },
    { "role": "user",   "content": "une tours basique de chevalier …\n\nContrainte de taille impérative : …" }
  ],
  "temperature": 0.7,
  "max_tokens": 16000
}
```

Le `system` vient de `prompts/planner_system.txt`. La contrainte de taille est
**concaténée au message utilisateur**, pas ajoutée au system : elle dépend de la demande,
pas du rôle. **Pas de `response_format`** à cette étape — c'est précisément ce qui laisse
le planificateur exposé au Structured Output hérité de LM Studio.

### Ce qui revient

`plan_text: str`, texte libre structuré, jamais du JSON :

```
Objet : Tour de chevalier
Boite englobante : 24 tenons (L) x 24 tenons (l) x 45 plates (H)
Symetrie : axe Z (symétrie autour de l'axe vertical)
Sections :
  1. Base ronde   z=0..3   pleine, forme cylindrique, diamètre 20 tenons, centrée
  2. Corps        z=4..25  pleine, forme cylindrique, diamètre 16 tenons, centrée
  …
Palette : Base ronde=reddish_brown, Corps=brown, …
```

`planner_meta.json` conserve de quoi expliquer un résultat plus tard :

```json
{ "tag": "planner", "model": "qwen/qwen3-coder-30b", "temperature": 0.7,
  "max_tokens": 16000, "seed": null, "attempts": 1, "duration_s": 25.14,
  "finish_reason": "stop",
  "usage": { "prompt_tokens": 580, "completion_tokens": 252, "total_tokens": 832 } }
```

---

## 4. Étape B — prompt + plan → design DSL

| | |
|---|---|
| Producteur | `src/designer.py` → `src/llm_client.py` → LM Studio |
| Consommateur | `src/compiler.py` |
| Type en mémoire | `dict` (JSON désérialisé) |
| Trace disque | `design.json`, `report.json`, `designer_*.json/.txt` (+ `_repair1`, `_repair2`) |

### Ce qui part

Même forme qu'en A, **plus deux différences décisives** :

- `response_format` porte le schéma JSON construit par `dsl_schema.build_schema(bornes,
  plancher)` — le décodage contraint rend le modèle *incapable* d'émettre une coordonnée
  hors bornes ou une couleur hors palette ;
- `messages` contient les **quatre exemples few-shot** en vrais tours `user` /
  `assistant` (`prompts/fewshot/manifest.json`), et non recopiés dans le system.

`temperature: 0.3`, `top_p: 0.9`.

### Ce qui revient — `design.json`

C'est **la seule donnée irremplaçable de tout le projet** : ~1 à 2 ko, et tout le reste
s'en reconstruit sans aucun appel LLM.

```json
{
  "name": "tour_de_chevalier",
  "bbox": [24, 24, 45],
  "ops": [
    {"op": "cylinder", "at": [2, 2, 0],  "diameter": 20, "height": 4,  "color": "reddish_brown"},
    {"op": "cylinder", "at": [4, 4, 4],  "diameter": 16, "height": 22, "color": "brown"},
    {"op": "cylinder", "at": [6, 6, 26], "diameter": 12, "height": 10, "color": "dark_gray"},
    {"op": "cylinder", "at": [8, 8, 36], "diameter": 8,  "height": 5,  "color": "white"},
    {"op": "box", "from": [10, 10, 41], "to": [13, 13, 45], "color": "red"}
  ]
}
```

Types : `name: str`, `bbox: list[int]` de longueur 3, `ops: list[dict]` de 1 à 60
entrées. Les huit opérations possibles et leurs champs sont dans `prompts/designer_system.txt`
et bornés par `src/dsl_schema.py`. Couleurs : chaînes d'une palette **fermée** de 12 noms.

**Piège visible dans cet exemple réel** : la dernière op va jusqu'à `z=45` alors que le
bbox vaut 45, donc les indices valides s'arrêtent à 44. Le compilateur **clampe** et
avertit, il ne lève pas.

### Boucle de réparation

Interne à `designer.design`, 2 tours au plus. À chaque rejet, le message renvoyé ne
contient **que les erreurs**, jamais une répétition du prompt ou du plan (ils sont déjà
dans l'historique) :

```
Ton design precedent a ete rejete par le validateur geometrique.

Erreurs :
- structure disjointe : 4 composantes (tailles [72, 12, 12, 12])

Corrige uniquement ces points. Renvoie le design complet corrige.
```

À distinguer des retries **réseau**, qui vivent dans `src/llm_client.py` et ne
concernent pas la sémantique.

---

## 5. design → voxels

| | |
|---|---|
| Producteur | `src/compiler.py`, `compile_design(design)` |
| Consommateur | `src/tiler.py`, `src/validator.py`, `src/primitives.py` (IoU) |
| Type | `numpy.ndarray`, forme `(X, Y, Z)` = `tuple(design["bbox"])`, `dtype=int16` |

Valeurs : `0` = vide, `n > 0` = **index de couleur** (`compiler.COLOR_INDEX`), pas un
code LDraw. Exemple : `reddish_brown` → `6` dans la grille, mais `70` dans le `.ldr`.

```python
>>> voxels = compile_design(design)
>>> voxels.shape, voxels.dtype
((24, 24, 45), dtype('int16'))
>>> voxels[12, 12, 0]        # au cœur de la base
6                            # = reddish_brown
>>> int((voxels > 0).sum())  # voxels pleins
7284
>>> round(7284 / voxels.size, 3)
0.281                        # = le fill_rate de report.json
```

(Valeurs recalculées depuis le `design.json` archivé, pas recopiées : la chaîne étant
déterministe, elles retombent exactement sur le `fill_rate` du rapport d'époque.)

Les opérations s'appliquent **dans l'ordre**, chacune écrasant les voxels qu'elle
touche : c'est ce qui permet à un `carve` de creuser une `box` posée juste avant.

---

## 6. voxels → placements (pavage)

| | |
|---|---|
| Producteur | `src/tiler.py`, `tile(voxels)` |
| Consommateur | `src/ldraw_writer.py`, `src/bom.py`, `src/validator.py` |
| Type | `list[Placement]` |

`Placement` est un `namedtuple` (`src/tiler.py`) :

```python
Placement(part, color_index, x, y, z, rotated, retournee=False)
```

| Champ | Type | Sens |
|---|---|---|
| `part` | `catalog.Part` | la référence du catalogue (26 pièces) |
| `color_index` | `int` | index de couleur, même échelle que la grille |
| `x`, `y`, `z` | `int` | coin **minimal** dans la grille interne |
| `rotated` | `bool` | quart de tour autour de la verticale (échange `w` et `d`) |
| `retournee` | `bool` | demi-tour ; avec `rotated`, donne les 4 orientations des pentes |

`Part` est un dataclass gelé :

```python
Part(ldraw_id='3007', kind='brick', w=8, d=2, h=3)
```

`w` = emprise le long de **X LDraw**, `d` le long de **Z LDraw**, `h` en **plates**
(3 pour une brique, 1 pour une plate). Convention relevée empiriquement, pas supposée.

Le pavage garantit par `assert` qu'aucun voxel plein ne reste découvert — un repli en
1×1 l'assure toujours. C'est pourquoi une surface courbe produit beaucoup de `3005` :
485 pièces ici, et la part de 1×1 explique à elle seule le terme « joints alignés » du
score.

---

## 7. placements → `model.ldr`

| | |
|---|---|
| Producteur | `src/ldraw_writer.py`, `write_ldr(placements, path, name)` |
| Consommateur | **Blender**, dans un autre processus ; LeoCAD ; BrickLink (import `.ldr`) |
| Type | fichier texte, une ligne par pièce |

```
0 FILE tour_de_chevalier
0 Name: tour_de_chevalier
0 Author: pipeline LEGO (genere automatiquement)
0 !LDRAW_ORG Unofficial_Model

1 70 200 -24 120 0 0 1 0 1 0 -1 0 0 3007.dat
```

Format d'une ligne de type 1 : `1 <code couleur> <x> <y> <z> <matrice 3×3> <fichier.dat>`.

**Décodage complet de la ligne ci-dessus**, vérifié par le calcul :

| | |
|---|---|
| `70` | code LDraw de `reddish_brown` (index voxel 6) |
| matrice `0 0 1 0 1 0 -1 0 0` | `rotated=True`, donc `w, d = 2, 8` |
| `x = 200` | `round((9 + 2/2) × 20)` → position grille `x = 9` |
| `y = -24` | `-((0 + 3) × 8)` → position grille `z = 0`, pièce de hauteur 3 |
| `z = 120` | `round((2 + 8/2) × 20)` → position grille `y = 2` |

Soit `Placement(Part('3007', 'brick', 8, 2, 3), 6, x=9, y=2, z=0, rotated=True)`.

Deux conversions à ne pas défaire :

- l'origine d'une pièce LDraw est **centrée** en X/Z, d'où le passage du coin minimal au
  centre (`+ w/2`) ;
- l'origine verticale est à la **base des tenons**, soit au sommet du corps de la pièce,
  d'où `y = -((z + h) × 8)` et non `-(z × 8)`. Ce détail est invisible sur un modèle dont
  toutes les pièces ont la même hauteur ; il est apparu comme des « pièces flottantes »
  sur un premier modèle mêlant briques et plates.

---

## 8. `model.ldr` → rendus (Blender)

| | |
|---|---|
| Producteur | `src/blender_script.py`, exécuté **dans** Blender (`blender --python`) |
| Consommateur | `src/img_output.py`, `src/instructions.py`, l'interface web |
| Type | fichiers PNG |

`src/blender_script.py` **n'importe aucun `src.*`** : il tourne dans l'interpréteur de
Blender, qui n'a pas les dépendances du projet. Tout passe donc par l'environnement :

| Variable | Rôle |
|---|---|
| `LEGO_WORK_DIR` | dossier contenant `model.ldr` (mode un seul modèle) |
| `LEGO_BATCH_MANIFEST` | fichier texte listant un dossier par ligne (mode lot) |
| `LEGO_LDRAW_DIR` | chemin de la bibliothèque LDraw, passé explicitement en `ldrawPath` |
| `LEGO_RENDER_ENGINE` | `cycles` (défaut), `workbench` (rapide), `eevee` (déconseillé) |
| `LEGO_RENDER_RES` | largeur en pixels (défaut 800) |
| `LEGO_RENDER_SAMPLES` | échantillons Cycles (défaut 64) |
| `LEGO_RENDER_VIEWS` | restreint les vues produites |

Sorties : `render_top.png`, `render_front.png`, `render_right.png`, `render_iso.png`,
projections orthographiques.

**`LEGO_LDRAW_DIR` n'est pas un confort** : l'add-on ImportLDraw cherche sinon la
bibliothèque dans ses *préférences enregistrées*, ce qui marche sur un poste configuré à
la main et échoue partout ailleurs (« Could not find LDraw Part Library »).

Le mode lot existe parce que démarrer Blender coûte ~1,6 s par modèle : un seul processus
pour N modèles.

`src/img_output.py` relit les quatre PNG et écrit `final.png`, planche 2×2. Il **échoue
bruyamment** si un rendu manque — mieux qu'une planche silencieusement incomplète.

---

## 9. Sorties dérivées

### `report.json` — `src/validator.py`, `validate(voxels, placements, design)`

```json
{
  "score": 84.6,
  "errors": [],
  "warnings": ["op #4 (to) : coordonnee 45 (axe 2) hors du bbox declare [24, 24, 45] — sera clampee au compile"],
  "metrics": {
    "n_pieces": 485, "n_components": 1, "fill_rate": 0.281,
    "joint_alignment_rate": 0.771, "z_min": 0, "flottantes": []
  },
  "bbox_max_applique": [48, 48, 60],
  "taille_demandee": "grande"
}
```

`errors` non vide = **bloquant**, le design repart en réparation. `warnings` = pénalisé
au score ou simplement signalé. `bbox_max_applique` et `taille_demandee` sont là pour la
rejouabilité : sans eux, impossible de savoir à quelle échelle le modèle a été contraint.

Rappel utile en lisant un score : **ce n'est pas une note de qualité**, c'est une mesure
de validité géométrique. Ici 84,6 pour une tour reconnaissable, quand une dalle beige de
7 pièces note 95.

### `bom.csv` / `bom.html` — `src/bom.py`, `build_bom(placements)`

`list[dict]`, une entrée par (référence, couleur) :

```csv
ldraw_id,type,dimensions,hauteur_plates,couleur,couleur_ldraw,code_ldraw,quantite
3622,brique,3x1,3,brown,Brown,6,31
3010,brique,4x1,3,brown,Brown,6,29
3005,brique,1x1,3,brown,Brown,6,29
```

### `wanted_list.xml` — `src/wanted_list.py`

```xml
<INVENTORY>
<ITEM>
<ITEMTYPE>P</ITEMTYPE>
<ITEMID>3622</ITEMID>
<COLOR>8</COLOR>
<MINQTY>31</MINQTY>
<REMARKS>tour_de_chevalier / LDraw 3622</REMARKS>
</ITEM>
…
```

Trois contraintes vérifiées sur la documentation BrickLink, pas supposées :
**pas de déclaration `<?xml ?>`** ; seuls `ITEMTYPE` et `ITEMID` sont obligatoires ; et
le XML ne passe que par la **zone de collage** de la page *Want > Upload*, dont le
sélecteur de fichier n'accepte que `.bsx .ldr .lxf .io`.

`COLOR` est un numéro **BrickLink**, qui n'a rien à voir avec le code LDraw de la même
couleur : ici `brown` vaut `6` en LDraw et `8` en BrickLink. Et le numéro de pièce
BrickLink ne se déduit pas du numéro LDraw — **4 pièces sur 26 diffèrent**, et aucun
fichier de correspondance officiel n'existe.

---

## 10. Pile web — API, base, agent

```
Navigateur ──HTTP/JSON──▶ API (conteneur)  ──SQL──▶  Postgres  ◀──SQL── agent (hôte)
                                │                                          │
                                └── outputs/ monté en LECTURE SEULE        └── écrit outputs/
```

**`server/` n'importe jamais `src/`** : l'image de l'API ne contient que `server/`, ni
`src/`, ni numpy, ni la bibliothèque LDraw. Un import ferait planter le conteneur **au
démarrage**. Quand une constante du générateur est nécessaire à une validation d'API,
elle est recopiée dans `server/regles.py` **et** un test compare les deux valeurs : c'est
le test qui rend la duplication sûre.

### Dépôt d'une demande

`POST /api/jobs` :

```json
{ "prompt": "une tours basique de chevalier", "taille": "grande", "n_variantes": 4 }
```

→ une ligne `jobs`. `bbox_max` (`integer[]`) l'emporte sur `taille` quand il est fourni,
pour les formes élancées que les tailles nommées, à plafond cubique, ne savent pas
exprimer.

### La base comme file d'attente

Pas de Celery, pas de Redis : une seule génération tourne à la fois de toute façon, LM
Studio étant mono-modèle sur CPU.

`jobs.status` : `en_attente` → `en_cours` → `a_choisir` → `en_finition` → `terminee`,
ou `echec`.

`jobs.etape` est du **texte libre** écrit par l'agent (« design 2/4 », « rendu »). Sa
traduction en identifiants stables se fait **côté serveur**
(`server/regles.PHASES_PAR_ETAPE`) : l'interface n'interprète rien. Un test relit les
chaînes écrites dans `agent.py` et `server/app.py` et exige qu'elles soient toutes
connues.

### Colonnes de `variantes` qui portent la donnée

| Colonne | Type SQL | Rôle |
|---|---|---|
| `design_json` | `jsonb` | **l'archive réelle**, ~2 ko ; tout le reste s'en régénère |
| `score`, `n_components`, `fill_rate`, `joint_alignment_rate`, `z_min`, `n_floating` | numériques | métriques du validateur, déjà calculées |
| `seed`, `llm_model`, `repairs_used`, `code_version` | | rejouabilité |
| `render_dir` | `text` | **relatif** à `outputs/` : l'agent est sur Windows, l'API dans un conteneur Linux |
| `pdf` | `bytea` | uniquement la variante retenue |
| `correspond`, `commentaire`, `avis_at` | `text`, `text`, `timestamptz` | avis de relecture |

`render_dir` relatif n'est pas un détail de style : un chemin absolu n'aurait de sens
que d'un seul côté de la frontière conteneur/hôte.

### Au choix d'une variante

`POST /api/jobs/{id}/choose` → `is_chosen` sur une variante, `is_rejected` sur les
autres. Les rejetées **gardent** `design_json`, leurs métriques et leur seed (~2 ko) ;
elles perdent leur PDF et, côté agent, leurs rendus sur disque. Un `design_json` suffit à
tout reconstruire avec `tools/render_batch.py`, sans aucun appel LLM.

### Schéma de la base

`db/init.sql` n'est joué qu'à la **création** du volume Postgres. Un ajout de colonne sur
une base en service passe par `server.db.assurer_schema()`, idempotent, appelé au
démarrage de l'API **et** de l'agent. Pas d'Alembic. Les deux déclarations doivent rester
d'accord, et c'est un test qui le garantit.

---

## 11. Planche de relecture

| | |
|---|---|
| Producteur | l'utilisateur, via `/ui/relecture` |
| Consommateur | `tools/export_preferences.py` (à venir, issue #24), puis la calibration d'une mesure sémantique (#25) |

`GET /api/relecture?limit=20&offset=0&sans_avis=true` :

Réponse réelle, une variante conservée sur les trois :

```json
[
  {
    "job_id": "e4be67b2-7d36-432d-86ea-abe81fb76e29",
    "prompt": "un tours basique de chevalier",
    "taille": "grande",
    "bbox_max": null,
    "status": "a_choisir",
    "created_at": "2026-10-11T15:03:37.260532+00:00",
    "variantes": [
      {
        "id": "79f12650-9fce-4c39-872d-e274e1b30a06",
        "indice": 2,
        "name": "tour",
        "is_chosen": false,
        "a_un_rendu": true,
        "correspond": "non",
        "commentaire": "ba il y a un rectangle gris qui vole en haut de la tour",
        "avis_at": "2026-10-20T14:55:08.178329+00:00"
      }
    ]
  }
]
```

**Cette réponse n'expose ni `score` ni `n_pieces`, et c'est délibéré** : ce sont les deux
références triviales qu'une mesure sémantique devra battre. Affichées pendant la
relecture, elles influenceraient l'avis, et on ne mesurerait plus que la capacité du
score à se prédire lui-même.

`POST /api/variantes/{id}/avis` :

```json
{ "correspond": "partiel", "commentaire": "la silhouette est bonne, les roues manquent" }
```

`correspond` vaut `"oui"`, `"partiel"`, `"non"` (ordonnés du meilleur au pire,
`server/regles.VERDICTS`) ou `null` tant que la variante n'est pas relue — quatre états.
Un booléen est **refusé** : « oui » et « partiel » seraient tous deux vrais pour un
`bool()` naïf. `"partiel"` exige un commentaire, les deux autres non. Un nouvel avis
écrase le précédent, et `avis_at` bouge à chaque écriture.

---

## 12. Les entrées qui ne viennent pas du prompt

| Entrée | Lue par | Type |
|---|---|---|
| bibliothèque LDraw (`*.dat`) | `src/catalog.py`, Blender | fichiers, cherchés via `$LEGO_LDRAW_DIR` puis par heuristique |
| `LDConfig.ldr` | `src/catalog.py` | la palette RVB est **lue** de la bibliothèque, pas recopiée |
| `prompts/*.txt` | planner, designer, juge | texte |
| `prompts/fewshot/*.json` | designer | 4 designs compilés et vérifiés visuellement |
| `eval/*testset.json` | `eval/run_eval.py` | prompts figés, et références calculables pour les primitives |
| `config.py` | partout | tous les chemins, **tous surchargeables par l'environnement** |

`config.py` ne doit jamais prendre un chemin de conteneur pour défaut : ça marcherait en
conteneur et casserait le poste de l'utilisateur.

---

## 13. Les passes qui ne touchent pas à la chaîne

Elles lisent des fichiers déjà écrits et n'entrent jamais dans la génération — ce qui
garantit que le générateur et un modèle de jugement ne sont jamais chargés en même temps
(LM Studio retient ~18 Go).

| Passe | Entrée | Sortie |
|---|---|---|
| `src/juge_vlm.py` | `render_iso.png` + prompt | note entière 0–10 + raison, contrainte par schéma |
| `src/primitives.py` | `voxels` + une référence analytique | IoU, `float` |
| `eval/run_eval.py` | `eval/*testset.json` | un CSV par campagne |
| `tools/render_batch.py` | des `model.ldr` déjà écrits | les PNG manquants |
| `tools/recover_eval.py` | les `design.json` survivants | un CSV reconstruit |

Le juge ne voit qu'une **image**, jamais un `design.json` : faire relire la géométrie à
un modèle remplacerait une réponse sûre et gratuite (`src/validator.py`) par une opinion.
