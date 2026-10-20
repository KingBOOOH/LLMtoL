# Mesures figées

Toutes les mesures du projet, avec leurs chiffres et leur protocole.

Ce document existe pour une raison précise : `AGENTS.md` est chargé **à chaque session**,
qu'on en ait besoin ou non. Les règles et les pièges doivent y rester, parce qu'on peut
les enfreindre sans le savoir. Les tableaux de chiffres, non : on les consulte quand on
en a besoin. Les y laisser coûtait environ 3 000 tokens par session pour une matière
qu'on ouvre une fois par mois.

`AGENTS.md` garde donc la *conclusion* de chaque mesure — ce qu'il faut savoir pour ne
pas refaire une erreur — et renvoie ici pour les chiffres.

---

## Décalage d'indice z (résolu)

Le défaut venait du **planificateur**, pas du designer. Son plan écrivait « z=1..8 » et le
designer obéissait au plan qu'on lui donne.

| tentative | résultat |
|---|---|
| corriger `prompts/designer_system.txt` | 8/26 hors sol avant, **9/26 après** (aucun effet) |
| déplacer la consigne dans `prompts/planner_system.txt` | **9/26 → 0/26** |

La consigne retenue : `Z IS 0-INDEXED AND z=0 IS THE GROUND`, plus un `z=0` figé dans le
gabarit de sortie.

**Le décalage n'était pas cosmétique.** En démarrant à z=1 dans un bbox de hauteur *n*, le
modèle se faisait tronquer d'une plate par le haut contre son propre bbox. Les IoU
observés valaient exactement (n−1)/n :

| forme | IoU observé | fraction |
|---|---|---|
| cube | 0,875 | 7/8 |
| pavé | 0,833 | 5/6 |
| cylindre | 0,750 | 3/4 |

Une couche entière perdue à chaque fois. Après correction, ces trois formes passent de
0,819 à **1,000**.

**Leçon de méthode.** La première « mesure » de cette correction était fausse : elle
contournait le planificateur avec un plan écrit à la main contenant « poser au sol
(z=0) ». Elle mesurait cette phrase, pas la correction.

---

## cylinder / sphere, puis correction du z

Mesure figée complète, **9 formes × 3 tentatives** via le vrai planificateur.

Références : avant `v1prim_20260923_093512` ; après `v1prim_20261010_185403` ; après
correction du z `v1primz_*` + `v1primB_*` + `v1primC_*`.

| | avant | cylinder/sphere | + correction z |
|---|---|---|---|
| IoU global | 0,614 | 0,706 | **0,774** |
| IoU formes courbes | 0,391 | 0,619 | **0,698** |
| IoU formes en box | — | 0,769 | **0,830** |
| durée moyenne | — | 78 s | **52 s** |
| générations hors sol | — | 9/26 | **0/26** |

Par forme (avant → après correction du z) :

| forme | | forme | |
|---|---|---|---|
| cube | 0,875 → **1,000** | pavé | 0,833 → **1,000** |
| cylindre | 0,750 → **1,000** | anneau | 0,838 → **0,947** |
| croix, tour | 1,000 (inchangé) | pyramide | 0,330 → 0,384 |
| escalier | 0,139 → 0,149 | sphère | 0,527 → 0,342 |

Opérations réellement employées : `box`, `cylinder`, `layer`, `carve`, `mirror`.
**`cylinder` est adopté, `sphere` NON.**

**Le recul de `prim_sphere` n'est pas une régression.** Sur 3 tentatives : une échoue
(déjà avant), une reproduit exactement l'ancien résultat (0,527 avec `layer`), une bascule
sur `box`+`carve` et tombe à 0,157. La moyenne repose sur deux générations exploitables,
dont une qui joue à pile ou face sur la méthode. **Chiffre non solide statistiquement.**

**Conséquence sur la métrique :** un IoU de 1,0 sur `prim_cylindre` mesure désormais que
le modèle a choisi la bonne opération et les bons paramètres, plus qu'il a su tracer un
cercle. Ce n'est pas la même chose, et c'est voulu.

---

## prim_escalier : diagnostiqué

Le modèle construit un escalier **géométriquement parfait**. Deux causes distinctes
expliquent le score de 0,149, et une seule est de sa faute.

**1. Erreur du modèle** — 3 plates par marche au lieu d'une. Il raisonne en briques
(1 brique = 3 plates) alors que le prompt dit « la première marche fait 1 plate de haut ».
Volume produit : 180 voxels contre 60 attendus, exactement 3×. C'est **encore le
planificateur** : les plans écrivent z=0..2, z=3..5… et une boîte de 15 plates ; le
designer obéit fidèlement. Même schéma que le décalage z.

**2. Artefact de mesure** — l'escalier est tourné de 270° par rapport à la référence. Le
prompt n'impose aucune orientation. `voxel_iou` recadre sur le contenu mais ne tourne
pas : il punissait une réponse correcte.

Décomposition mesurée (tentative 1) :

| | IoU |
|---|---|
| tel quel | 0,200 |
| rotation autorisée | 0,333 |
| hauteur corrigée | 0,429 |
| **hauteur corrigée + rotation autorisée** | **1,000** |

D'où `src/primitives.voxel_iou_oriente()`, rapportée **en plus** de `voxel_iou` (colonne
`iou_oriente`) et jamais à sa place.

**Deux corrections de prompt essayées et mesurées. Les deux sans effet — ne pas les
retenter :**

| tentative | escalier | orienté | témoin `prim_tour` |
|---|---|---|---|
| référence | 0,149 | — | 1,000 |
| a) consigne explicite sur l'unité | 0,200 | 0,333 | 1,000 |
| b) la plate comme unité par défaut | 0,198 | 0,333 | 1,000 |

Pour (a), vérifié que la consigne partait bien dans la requête : elle y était, le modèle
l'a ignorée 3 fois sur 3. Pour (b), plan produit mot pour mot identique. Le prompt a été
restauré à l'identique.

**Ce qui reste à tenter :** agir côté jeu de test (le prompt figé dit « 1 plate », le
modèle comprend « 1 niveau »), ou accepter que ce cas mesure la capacité du modèle à
respecter une unité — ce qu'il ne fait pas ici.

---

## Taille demandée (`--taille` / `bbox_max`)

C'est une **garantie par le schéma**, pas une consigne : `build_schema()` resserre le bbox
**et** les coordonnées. Mesure, 3 générations par taille : **0 dépassement sur 9**.

Mais la borne est un **plafond, pas une cible** :

| taille | bbox obtenus | pièces |
|---|---|---|
| petite | 16×16×20 aux 3 essais (sature sa borne) | 50–143 |
| moyenne | — | 65–241 |
| grande | 16×16×24, 24×24×24, 16×16×12 (ne la touche jamais) | 137–189 |

Choisir `grande` n'a donc **aucun effet observable** aujourd'hui : le planificateur choisit
ce qu'il juge raisonnable. Et le nombre de pièces ne suit pas la taille — il dépend du
creux du modèle. La garantie porte sur la boîte englobante, pas sur le compte.

---

## Élargissement du catalogue aux pentes

L'IoU se calcule sur la **grille de voxels**, que le pavage ne touche pas : élargir le
catalogue ne peut pas le faire bouger, et 27 générations LLM n'auraient rien mesuré. Ce
qui bouge est calculable exactement, et gratuitement, sur les **161 designs archivés**.

| | avant | après |
|---|---|---|
| pièces (moyenne) | 96,87 | **98,89** (+2,1 %) |
| références distinctes | 13,98 | 14,63 |
| joints alignés | 0,2574 | **0,2578** (inchangé) |
| score du validateur | 84,26 | **84,22** (−0,04) |
| erreurs bloquantes | 2 | **2** (les mêmes) |
| designs pavés sans incident | 161/161 | **161/161** |

648 pentes posées sur 79 des 161 designs. Score : 19 en hausse, 18 en baisse, 124 stables.
L'alignement des joints ne bouge pas — la règle de maçonnerie continue de primer, comme
voulu.

Vérification visuelle : pyramide (19 pentes sur 32 pièces) rendue avant et après, gradins
devenus rampes, orientations correctes sur les quatre faces. LeoCAD n'étant pas installé,
le contrôle est passé par un rendu Blender examiné directement.

---

## Génération en conteneur

| | |
|---|---|
| taille de l'image | **1,07 Go** |
| rendu d'un même modèle, sur l'hôte | 65 s |
| rendu du même modèle, en conteneur | **36 s** |
| images produites | 682 762 contre 682 703 octets, soit équivalentes |

Le conteneur est **plus rapide**, à l'inverse de ce qui était attendu.

Vérifié de bout en bout depuis le conteneur : pré-contrôle LM Studio via
`host.docker.internal`, une phase de finition complète (49 rendus en 248 s, nomenclature,
liste d'achat, PDF de 14,5 Mo), et une génération complète (planificateur 31 s, designer,
boucle de réparation, 3 variantes sur 4 abouties).

---

## Choix du modèle vision du juge (US V2-01)

Candidats retenus sur l'empreinte réelle, relevée par l'API Hugging Face sur le
catalogue `lmstudio-community` — quantification Q4_K_M plus le projecteur vision F16,
**qui est un fichier séparé** qu'on oublie dans les estimations :

| modèle | poids | mmproj | total | tient dans 4 Go de VRAM |
|---|---|---|---|---|
| Qwen3-VL-2B-Instruct | 1,03 Go | 0,76 Go | **1,79 Go** | largement |
| Qwen2.5-VL-3B-Instruct | 1,80 Go | **1,25 Go** | 3,05 Go | oui |
| Qwen3-VL-4B-Instruct | 2,33 Go | 0,78 Go | **3,11 Go** | oui, serré |
| gemma-3-4b-it | 2,32 Go | 0,79 Go | 3,11 Go | oui, serré |
| Qwen3-VL-8B-Instruct | 4,68 Go | 1,08 Go | 5,76 Go | **non** |

Gemma 3 4B et Qwen3-VL-4B ont la **même empreinte à 10 Mo près**. L'argument du
« projecteur plus léger », qui vaut contre Qwen **2.5**-VL-3B (1,25 Go), ne sépare pas
ces deux-là. Il ne faut pas s'en servir pour trancher.

### Pré-filtre de discrimination

Avant de dépenser du temps Blender, test sur les **9 rendus complets déjà présents dans
`outputs/`** (château ×2, maison ×2, phare, tours ×4). Chaque rendu noté contre chacun
des 4 sujets, soit 36 notes par modèle. Un juge utilisable note haut sur le vrai sujet
et bas sur les autres.

| | Qwen3-VL-4B | Qwen3-VL-2B |
|---|---|---|
| note moyenne sur le vrai sujet | 5,56/10 | 4,00/10 |
| note moyenne sur un autre sujet | 2,70/10 | 2,00/10 |
| **écart** | **+2,85** | +2,00 |
| notes perdues | **0/36** | **4/36** |
| temps par note | **9,0 s** | 9,3 s |

**Le 2B n'est pas plus rapide** malgré deux fois moins de poids : l'inférence est
dominée par l'encodage de l'image, pas par le décodeur. Son seul avantage supposé
disparaît. Il perd en plus 11 % de ses notes en partant en répétition dégénérée (« le
toit est en forme de carré », cinq fois d'affilée) jusqu'à dépasser `max_tokens`.

Corrigé à la cause : `raison` est borné à 200 caractères **dans le schéma**, plutôt que
d'augmenter le budget de tokens. Vérifié que LM Studio compile bien `maxLength` en
grammaire — les deux appels qui échouaient passent, à exactement 200 caractères.

Retenu : **Qwen3-VL-4B-Instruct en Q4_K_M**.

Il *tient* en mémoire en même temps que le générateur de 18,6 Go — mais il ne faut pas le
faire : les deux chargés, il reste 1,1 Go libre sur 31,7, et le tueur de tâches de Windows
a coupé deux calibrations sur trois. Décharger le générateur (`lms unload --all`) laisse
16 Go libres et la troisième est passée d'un bloc. Le juge tourne en passe séparée après
les rendus, précisément pour n'avoir jamais besoin des deux à la fois.

### Ce que ce chiffre ne mesure pas

L'étiquette de chaque rendu vient du champ `name` de son `design.json` : c'est ce que le
générateur **voulait** produire, pas ce qu'il a produit. Un modèle étiqueté « château »
est en réalité une maison à toit plat, et le juge a répondu maison=10 / château=5 —
**il a raison, l'étiquette a tort**. L'écart de +2,85 est donc un plancher, pas une
estimation : une partie des « erreurs » comptées contre le juge sont des erreurs
d'étiquette.

## Calibration du juge visuel contre l'IoU (US V2-01)

91 rendus du jeu primitives, notés par Qwen3-VL-4B, confrontés à l'IoU calculé
analytiquement. Corrélation de **rang** (Spearman) : on demande au juge de classer les
rendus dans le bon ordre, pas de prédire l'IoU.

| corrélation de rang | vs `iou` | vs `iou_oriente` |
|---|---|---|
| tous les rendus (91) | **+0,382** | **+0,412** |
| sans `prim_sphere` (84) | +0,310 | +0,333 |
| sans `prim_escalier` (76) | +0,502 | +0,502 |

**Barrière annoncée avant la mesure : 0,55. Résultat 0,41, donc le juge est refusé** —
pas de colonne `note_vlm` dans le CSV d'évaluation.

| forme | n | IoU | IoU orienté | note |
|---|---|---|---|---|
| `prim_anneau` | 9 | 0,828 | 0,828 | 4,2 |
| `prim_croix` | 9 | 1,000 | 1,000 | 10,0 |
| `prim_cube` | 9 | 0,958 | 0,958 | 10,0 |
| `prim_cylindre` | 9 | 0,674 | 0,674 | 2,2 |
| `prim_escalier` | 15 | 0,152 | 0,261 | 7,1 |
| `prim_pave` | 9 | 0,889 | 0,889 | 10,0 |
| `prim_pyramide` | 9 | 0,360 | 0,360 | 8,1 |
| `prim_sphere` | 7 | 0,365 | 0,365 | 0,4 |
| `prim_tour` | 15 | 0,996 | 0,996 | 6,0 |

8 valeurs de note distinctes sur 11 : l'échelle ne s'est pas effondrée, c'est un désaccord
réel. Mais la lecture « exact sur les rectilignes, effondré sur les courbes » ne résiste
pas aux rendus.

### L'aplatissement touche tout le jeu, pas seulement la sphère

X et Y se comptent en tenons (20 LDU), Z en plates (8 LDU). Toute forme dont la hauteur
est donnée en plates est donc écrasée à l'écran. Relevé sur les références du jeu :

| forme | largeur | hauteur | rapport | IoU | note |
|---|---|---|---|---|---|
| `prim_cube` | 160 LDU | 64 LDU | 2,5:1 | 0,96 | **10,0** |
| `prim_pave` | 240 LDU | 48 LDU | 5,0:1 | 0,89 | 10,0 |
| `prim_tour` | 80 LDU | 120 LDU | 0,7:1 | 1,00 | 6,0 |
| `prim_cylindre` | 180 LDU | 32 LDU | 5,6:1 | 0,67 | **2,2** |
| `prim_sphere` | 140 LDU | 56 LDU | 2,5:1 | 0,37 | 0,4 |

`prim_cube` et `prim_cylindre` sont la même galette, et les deux rendus respectent leur
prompt à la lettre. Le juge valide l'un à 10/10 en **vérifiant les dimensions** du prompt
(« 8 tenons de côté et 8 plates de haut, comme demandé, toutes les proportions sont
justes ») et rejette l'autre à 3/10 en **jugeant le nom** (« un bloc rectangulaire avec des
dents, pas un cylindre »), sans tenir compte des 4 plates qu'il venait de lire.

Donc : pas un juge sévère sur les courbes, un juge qui **change de critère**. Et un juge
incohérent est pire qu'un juge sévère — la sévérité se recalibre, l'incohérence non.

C'est aussi un résultat sur le **jeu de test** : l'IoU ne peut pas voir l'aplatissement,
sa référence étant calculée dans les mêmes unités. Un IoU de 1,0 ici veut dire « conforme
à la spécification », jamais « ressemble à l'objet nommé ».

Coût : **10,6 s par note**, 966 s pour les 91 — générateur déchargé. Recalculer les IoU
sur des notes déjà produites coûte 0,3 s par note (`--resume`).

### Trois barèmes, et ce que chacun a appris

| | 3 ancres | plages + comptage | plages seules |
|---|---|---|---|
| ρ vs `iou` | 0,383 | 0,432 | **0,382** |
| ρ sans `prim_escalier` | 0,644 | 0,551 | 0,502 |
| valeurs de note distinctes | **3** | 9 | 8 |

Le premier barème ne montrait que trois ancres (0, 5, 10) et le modèle n'a utilisé que ces
trois valeurs : {0: 17, 5: 2, 10: 72}. Une échelle à trois crans plafonne mécaniquement
Spearman — c'était un défaut du barème, pas du juge.

Le deuxième changeait **deux choses à la fois** (plages de notes *et* consigne de compter
les parties visibles) : la chute de 0,644 à 0,551 n'était donc attribuable à aucune des
deux. Le troisième retire la seule consigne de comptage, et ne remonte pas. Les trois
barèmes tiennent dans la même fourchette 0,38-0,43 : **le barème n'est pas le levier**.

On s'arrête là. Au quatrième barème on n'étalonne plus un juge, on optimise un prompt
contre un jeu de test de 91 rendus — et on fabrique le signal qu'on prétend mesurer.

Chaque échec du générateur compte donc comme un échec du juge. **+2,85 est un plancher,
pas une estimation.** C'est exactement pourquoi la calibration qui décide de l'US se
fait sur le jeu primitives, où l'IoU dit ce qui a été produit indépendamment de ce qui
était voulu.

Notes de passage du juge, qui montrent qu'il voit réellement : le « phare » noté 0/10,
« l'image représente un gâteau au chocolat, pas un phare » — et c'est bien ce que
l'image montre. Une « maison » notée 0 sur les quatre sujets : c'est une dalle beige
sans une arête.

---

## Correspondance BrickLink

Vérifiée **une par une** le 2026-10-12 sur bricklink.com. **4 pièces sur 26** portent un
numéro différent des deux côtés :

| LDraw | BrickLink | pourquoi |
|---|---|---|
| `3023b` | `3023` | LDraw a renommé la pièce (mise à jour 2024-07), BrickLink non |
| `3070b` | `3070` | BrickLink le liste en numéro *alterné* |
| `3069b` | `3069` | idem |
| `3040b` | `3040` | n'existe pas du tout chez BrickLink, pas même en alterné |

Les couleurs utilisent **les mêmes nombres pour des couleurs différentes** : LDraw 8 =
*Dark Grey*, BrickLink 8 = *Brown* ; BrickLink 88 = *Reddish Brown* là où LDraw utilise 70.

Deux couleurs de la palette ne sont **plus produites** : *Light Gray* (fin 2007) et *Dark
Gray* (fin 2006). Équivalents actuels : *Light / Dark Bluish Gray* (BrickLink 86 et 85).

---

## Chaîne v0 (retirée)

Une chaîne antérieure demandait directement au LLM une liste de cubes : pas de vraies
pièces, aucune garantie géométrique, et **15 générations de créatures sur 15** produisaient
des membres détachés. Ses mesures de référence restent figées dans `eval/results/` mais ne
sont plus regénérables.

## L'op `sphere` et le prompt qui la bloquait (Priorité 2)

Trois formulations, chacune à **une clause** de la précédente — jamais deux changements
dans la même mesure. Référence d'IoU identique dans les trois cas.

| version | prompt | op `sphere` | IoU moyen |
|---|---|---|---|
| `prim_sphere` (figé) | « …7 tenons de diametre : chaque couche horizontale est un disque, le plus large au milieu, retrecissant… » | 0 / 7 | 0,365 |
| `prim_sphere_v2` | **+** « et 7 plates de haut » | 0 / 3 | 0,324 |
| `prim_sphere_v3` | **−** la clause de méthode | **3 / 3** | **1,000** |

Entrées des deux essais : `eval/primitives_sphere_v2.json`, `eval/primitives_sphere_v3.json`.
Sorties : `eval/results/_work/sphere_v2/`, `eval/results/_work/sphere_v3/`.
Comparaison : `python -m tools.comparer_adoption sphere`.

Les trois designs de v3 sont identiques et tiennent en une ligne, sans aucune réparation :

```json
{"op": "sphere", "at": [0, 0, 0], "diameter": 7, "color": "white"}
```

### Points de repère, pour situer les scores

| ce qu'on pose | IoU vs la sphère de référence |
|---|---|
| op `sphere` | **1,000** (même rastériseur que la référence : c'est une identité, pas une performance) |
| cylindre plein 7×7×7 | 0,691 |
| boîte pleine 7×7×7 | 0,522 |
| ce que le modèle produit en dessinant ses `layer` à la main | 0,087 à 0,527 |

**Six tentatives sur dix font moins bien qu'une boîte carrée.** Dessiner les couches une à
une pour obtenir un arrondi coûte plus que ça ne rapporte.

### Deux défauts distincts, à ne pas confondre

1. **L'unité en Z.** Aucun des 9 plans du cas figé n'écrit « 7 plates (H) » : six écrivent
   « 7 tenons (H) », trois « 14 plates (H) », et quatre posent des sections jusqu'à z=14
   dans un objet de 7 plates. Or `prompts/planner_system.txt` donne la consigne **deux
   fois**, dont une dans son gabarit de sortie. Le modèle recopie l'unité du prompt
   utilisateur par-dessus son propre gabarit. Ajouter la clause corrige ce défaut (3/3
   corrects), mais ne change **rien** à l'adoption.
2. **La dictée de méthode.** C'est elle, et elle seule, qui bloquait l'op.

Conséquence : allonger `planner_system.txt` ne servirait à rien ici — la consigne y est
déjà, et elle est écrasée par l'entrée.

### Décision

`prim_sphere` reste **intact** : son historique vaut plus que son score, et il mesure
quelque chose de réel — l'obéissance à une méthode imposée. Un 10e cas,
`prim_sphere_libre`, porte le prompt mesuré. Le jeu a donc deux sphères qui répondent à
deux questions différentes.

Non mesuré : `prim_cylindre` n'est adopté que 6 fois sur 9 et son prompt décrit lui aussi
la forme (« la section horizontale est un disque »). La même suppression le mènerait
peut-être à 9/9.

### Confirmé sur le cylindre : décrire la forme suffit à bloquer l'op

Le prompt figé du cylindre ne dicte aucune méthode — il **décrit** seulement la forme.
Ça suffit à faire perdre un tiers des adoptions.

| version | prompt | op `cylinder` | IoU moyen |
|---|---|---|---|
| `prim_cylindre` (figé) | « …9 tenons de diametre et 4 plates de haut **: la section horizontale est un disque** » | 6 / 9 | 0,674 |
| `prim_cylindre_v2` | **−** cette clause | **3 / 3** | **1,000** |

Entrée : `eval/primitives_cylindre_v2.json`. Sortie : `eval/results/_work/cylindre_v2/`.
Comparaison : `python -m tools.comparer_adoption cylindre`.

Détail qui ne se voyait pas sur la sphère : trois des tentatives figées **adoptent
pourtant l'op** et plafonnent quand même à 0,750, parce que leur plan pose `z=0..4` — cinq
plates là où il annonce quatre. En appelant l'op avec `height: 4`, le modèle n'a plus de
bornes à calculer et l'erreur disparaît. Repère : une boîte pleine 9×9×4 vaut déjà 0,852,
soit mieux que la moyenne du cas figé.

### La règle qui en sort

Pour tout prompt de jeu de test visant une forme que le DSL sait rastériser : **donner le
nom et les dimensions, rien de plus.** Décrire la géométrie — même sans prescrire de
méthode, même en une demi-phrase — pousse le modèle à la construire à la main, et il la
construit moins bien que l'opération, parfois moins bien qu'une boîte.

| | clause en trop | adoption | IoU |
|---|---|---|---|
| sphère | une **recette** (« chaque couche… rétrécissant… ») | 0/7 → 3/3 | 0,365 → 1,000 |
| cylindre | une **description** (« la section horizontale est un disque ») | 6/9 → 3/3 | 0,674 → 1,000 |

## La sphère était une galette (correction de la géométrie)

`src/primitives.py` documentait ce choix comme délibéré et différé : *« l'anisotropie
réelle du LEGO n'est volontairement PAS prise en compte ici […] une sphère physiquement
ronde viendra plus tard. »* Ce qui a décidé que « plus tard » était maintenant : le juge
visuel a noté **0/10, trois fois**, des sphères à **IoU 1,000**, en écrivant « un bloc
rectangulaire, pas une sphère ». Il avait raison, et les deux métriques du projet étaient
aveugles — l'IoU parce que sa référence utilise les mêmes unités que l'objet mesuré.

### Ce qui a changé

| | avant | après |
|---|---|---|
| grille d'une sphère de 7 tenons | 7 × 7 × **7** | 7 × 7 × **18** |
| emprise réelle | 140 × **56** LDU | 140 × **144** LDU |
| rapport largeur / hauteur | **0,40** | **0,97** |

L'appartenance se teste désormais **en LDU** et non en cellules. `cylinder`, `cube` et
`box` ne changent pas : leurs dimensions sont données axe par axe, donc une galette
demandée est une galette livrée. La sphère était le seul cas où un paramètre unique
devait valoir deux longueurs différentes.

### Le correctif n'a pas suffi : il a fallu prévenir le planificateur

Première mesure après correction, 3 générations : l'op `sphere` est bien adoptée 3/3,
mais **les 3 plans écrivent une boîte englobante de 7 plates de haut**, donc la sphère est
rognée et retombe à 0,338. Le prompt du designer avait été mis à jour, pas celui du
planificateur — or c'est **lui** qui écrit la boîte. Encore le piège documenté : le défaut
vient du planificateur.

Après ajout de l'emprise verticale dans `prompts/planner_system.txt` :

| | bbox du plan | op | IoU | débordements |
|---|---|---|---|---|
| avant | `7 × 7 × 7` ×3 | `sphere` 3/3 | 0,338 | 3 |
| après | `7 × 7 × 18` ×3 | `sphere` 3/3 | **1,000** | **0** |

### Attention à la résolution avant de crier au bug

À 7 tenons de diamètre, une sphère en briques ressemble à un cube aux coins coupés — le
juge visuel la note d'ailleurs toujours 0/10 en disant « un cube », et ce n'est pas
absurde. À **16 tenons** (40 plates, 1120 pièces) elle est franchement ronde. Vérifié sur
rendu dans les deux cas.

Ce qui en dit long sur le juge : il a **détecté** un vrai défaut que deux métriques
exactes laissaient passer, mais il est incapable d'en **valider** la correction. Bon
détecteur d'anomalie, mauvais arbitre.

### Coût

Les designs écrits sous l'ancienne convention (bbox de 7 plates pour une sphère de 7
tenons) débordent désormais : ils tombent de 1,000 à 0,338, avec un avertissement
`depasse la grille` — rogné bruyamment, jamais en silence. Toute comparaison d'IoU sur
`prim_sphere` d'avant et d'après cette date est invalide.

### Le 11e cas : `prim_cylindre_libre`

Mesure refaite avec la configuration du jour — le prompt du planificateur ayant changé
entre-temps pour la sphère, l'ancienne mesure ne pouvait plus justifier un ajout au jeu
figé. Confirmé : **3/3 d'adoption, IoU 1,000, zéro débordement**, bbox `[9, 9, 4]`.

Le jeu contient donc quatre cas pour deux formes. Les deux cas figés mesurent
l'obéissance à une consigne de forme, les deux cas « libre » mesurent la construction.
Une moyenne qui mélange les quatre ne veut rien dire.

| cas | ce qu'il mesure |
|---|---|
| `prim_sphere` | obéissance à une **recette** imposée |
| `prim_sphere_libre` | construction d'une sphère |
| `prim_cylindre` | obéissance à une **description** imposée |
| `prim_cylindre_libre` | construction d'un cylindre |

## Poids du PDF de notice (US V2-07, issue #22)

Modèle de 134 pièces, 17 étapes, 37 références. Pixels identiques dans les deux
colonnes pour les images d'étape : seul l'encodage change.

| | avant | après |
|---|---|---|
| PDF | **15,17 Mo** | **1,12 Mo** |
| images embarquées | 28,17 Mo | 1,24 Mo |
| temps de génération | 5,7 s | 1,1 s |

13,6× plus léger, 5× plus rapide. Critère de l'issue (moins de 3 Mo sur ~150 pièces) :
atteint avec une marge de 2,7×.

### Où pesait le PDF, mesuré avant de corriger

| | images | poids |
|---|---|---|
| vignettes de pièces | 32 | **20,11 Mo** |
| images d'étape | 17 | 7,35 Mo |
| couverture | 1 | 0,71 Mo |

Le coupable n'était pas celui que l'issue désignait. Les vignettes étaient rendues en
800×800 pour un affichage en **42 px** (`img.vign` dans `src/pdf.py`), soit 360 fois
trop de pixels. Réduites à 160 px — dimensionnés pour l'impression, 42 px CSS valant
environ 131 px à 300 DPI.

### PNG contre JPEG sur un rendu Blender, à définition égale

| 800×800 | PNG | JPEG q92 | JPEG q85 |
|---|---|---|---|
| poids | 398 ko | **22 ko** | 16 ko |

Un rendu Blender est photographique : dégradés et anticrénelage, le pire cas pour la
compression sans perte. Les images d'étape **gardent donc leur définition** — elles
s'affichent à 95 mm où 800 px se justifient — et ne changent que de format. Réduire leur
taille aurait dégradé l'impression pour un gain marginal une fois le bon format choisi.

### Ce qui a failli fausser la mesure

Première tentative : 6,00 Mo « avant » au lieu de 15,17. `src/pdf.py` indexe les
vignettes par `(ldraw_id, color_index)` et le script de mesure passait des index
entiers — **aucune vignette n'était embarquée**, donc le principal poste de poids était
absent des deux colonnes. Le ratio paraissait bon, la mesure ne valait rien. Repère pour
la prochaine fois : le « avant » reconstruit doit coller au PDF réel sur disque (14,52 Mo
ici), sinon la reconstruction est incomplète.

## Taille demandée : un plancher, pas seulement un plafond (US V2-03, issue #18)

3 générations par taille, même prompt (« une petite maison aux murs creux et toit plat »)
et **mêmes seeds** avant et après : seule la contrainte de taille change.

| taille | pièces avant | pièces après | emprise réelle après |
|---|---|---|---|
| `petite` | 84 – 127 | 12 – 170 | `16x16x11` |
| `moyenne` | **7** – 254 | **239 – 372** | `20x20x13` → `24x24x13` |
| `grande` | 86 – 169 | **452 – 707** | `40x40x13` → `48x48x13` |

Les fourchettes **ne se chevauchent plus**. Avant, `grande` rendait `16x16x24`,
`16x16x21`, `16x16x21` — moins que le plafond de `petite` — et une génération en
`moyenne` est sortie à `8x6x4`, soit 7 pièces.

**Le volume réellement construit suit, ce qui n'était pas acquis.** Un plancher force une
*déclaration* ; rien n'oblige le modèle à remplir sa boîte. Mesure faite sur le volume
occupé et pas sur le bbox déclaré, précisément pour pouvoir répondre : `grande` passe
d'une emprise de `16x16` à `40x40`–`48x48`.

### Pourquoi le plafond seul ne suffisait pas

Le planificateur ne recevait qu'un plafond (« ne doit pas dépasser »), pendant que son
prompt système lui souffle de *préférer des tailles modestes*. Les deux combinés donnent
le résultat mesuré : la borne haute ne mord jamais. Le plancher lui est donc annoncé
aussi, avec la mention explicite que la consigne de modestie ne s'applique pas — sinon il
propose un plan que le schéma refusera, et le designer brûle ses réparations à le
rattraper.

### Formes élancées

Un plafond nommé est cubique. `--bbox-max 10x10x48` sur « un phare blanc étroit avec une
lanterne rouge au sommet » : **`4x4x48` réellement construit, rapport 12:1**, 73 pièces,
score 83,2, zéro réparation, deux `cylinder`. Vérifié au rendu.

### Un échec sur neuf dans la campagne « avant », et il est de moi

`moyenne` seed 103 a échoué sur `Erreur API : 400 — Context`. J'avais lancé un essai du
nouveau `--bbox-max` **pendant** la campagne : le modèle n'a que 4096 jetons de contexte
pour 4 emplacements parallèles, et deux requêtes simultanées le font déborder. Ce n'est
pas une donnée, c'est une interférence. Ne pas générer pendant une campagne de mesure.

---

## Le score pénalisait la taille, et ce n'est pas une note de qualité

Point de départ : un château de 456 pièces à quatre tours notait **69,2**, là où d'autres
modèles bien plus pauvres passaient 90. Soupçon de départ — un score anti-corrélé à
l'ambition — **partiellement démenti par les chiffres**, et c'est le résultat utile.

### Décomposition exacte, sur deux modèles archivés

```
château 456 pièces : 100 − 20 (11 flottantes, compte plafonné) − 10,8 (joints 0,542) = 69,2
modèle  407 pièces : 100 −  0 (aucune flottante)               −  5,0 (joints 0,250) = 95,0
```

Les deux sont gros. Ce n'est donc **pas** « petit bat grand » : c'est un seul terme qui
comptait en valeur absolue quand tous les autres comptaient en taux.

```python
score -= 5  * min(len(floating_errors), 4)    # compte -> plafond atteint à 4 pièces
score -= 20 * joints_rate                     # taux <- le bon patron était déjà là
```

Le plafond de −20 est atteint dès 4 pièces flottantes, ce qui arrive mécaniquement quand
un modèle grossit : 11 flottantes sur 456, soit **2,4 %**, coûtaient le plafond entier.
Un modèle de 7 pièces ne peut structurellement pas l'atteindre.

Les 2 avertissements « op #7 » du château coûtent **zéro** : les dépassements de bbox ne
sont pas au score du tout.

### Après : taux, au même poids maximal que les joints

`score -= 20 * floating_rate`. Recalculé sur les **16 rapports archivés**, sans aucune
génération :

| pièces | flottantes | taux | avant | après |
|---|---|---|---|---|
| 485 | 0 | 0 % | 84,6 | 84,6 |
| 456 | 11 | 2,4 % | 69,2 | **88,7** |
| 412 | 8 | 1,9 % | 71,2 | **90,8** |
| 244 | 23 | 9,4 % | 70,5 | **88,6** |
| 139 | 4 | 2,9 % | 73,3 | **92,7** |
| 53 | 6 | 11,3 % | 77,0 | **94,7** |
| 18 | 3 | 16,7 % | 75,8 | **87,5** |

**+19 points** sur les 5 modèles concernés, **aucune régression** sur les 11 autres (leur
score est identique au dixième : ils n'avaient aucune flottante).

### L'inversion épinglée par le test

Même forme — boîte creuse coiffée d'un toit plat — à deux échelles :

| | pièces | flottantes | taux | avant | après |
|---|---|---|---|---|---|
| 14×14 | 37 | 3 | 8,1 % | 77,0 | 90,4 |
| 20×20 | 76 | **8** | 10,5 % | **73,7** | **91,6** |

Le gros modèle avait plus de flottantes en nombre absolu pour un taux comparable, et se
retrouvait **classé derrière** le petit. `test_flottantes_penalisees_au_taux_et_non_au_nombre`
fixe l'invariant : à taux comparable, agrandir ne doit pas baisser le score. Le seuil
`> 80` y est le discriminant — l'ancien plafond de −20 rendait 80 inatteignable.

### Mais la barrière annoncée échoue, et c'est la vraie conclusion

Barrière posée avant la mesure : *le château doit passer devant la dalle beige de
7 pièces*. **Elle échoue.** La dalle reste première à 95,0, avant comme après.

En regardant pourquoi : la dalle a zéro avertissement et zéro joint aligné. Elle est
géométriquement parfaite. Elle **mérite** 95 sur la validité.

Donc le score n'était pas cassé — il mesure exactement ce qu'il annonce, la validité
géométrique, et aucun repondérage n'en fera une note de ressemblance. C'est la
priorité 1 de `AGENTS.md`, toujours ouverte. **Ne pas réessayer de « corriger le score »
pour qu'il classe l'ambition : il n'est pas l'instrument.** Pour comparer deux
configurations de génération, le banc à utiliser est `eval/primitives_testset.json`, dont
l'IoU a une référence calculable.

Au passage : 10 modèles archivés sur 16 sont au-dessus de 83. La validité est déjà au
plafond, elle ne discrimine presque plus.

---

## Relevé de référence sur les 11 primitives (16 octobre 2026)

`v1prim_base30b_20261016_135655`. Configuration du jour : `qwen3-coder-30b` aux **deux**
étapes, contexte chargé à **4096** sur 262144 disponibles, Q4_K_M, 4 emplacements
parallèles. 11 formes, `--n 1`. Posé pour servir de point de comparaison aux changements
de configuration de génération (modèle du planificateur, contexte, few-shot).

**Non comparable au relevé du 10 octobre** (IoU 0,706) : celui-ci précède la correction
de la sphère, qui a changé la fonction de référence elle-même dans `src/primitives.py`.
Les IoU d'avant et d'après ne mesurent pas contre la même cible. Le jeu a aussi gagné
deux cas depuis.

### Chiffres

| | |
|---|---|
| générations valides | **9/11** |
| IoU moyen sur les survivants | **0,916** ← le chiffre du résumé |
| IoU moyen, échecs comptés 0 | **0,750** ← l'honnête |
| formes à IoU 1,000 exact | **7 sur 9** |
| score moyen (nouveau barème au taux) | 88,2 |
| durée moyenne | 104,5 s |

### Par forme, du pire au meilleur

| forme | IoU | durée | pièces |
|---|---|---|---|
| `prim_escalier` | **ÉCHEC** | 212 s | 0 |
| `prim_sphere` | **ÉCHEC** | 610 s | 0 |
| `prim_pyramide` | 0,463 | 74 s | 44 |
| `prim_anneau` | 0,784 | 40 s | 25 |
| `prim_cube` · `prim_pave` · `prim_croix` · `prim_tour` | 1,000 | 25–42 s | 9–16 |
| `prim_cylindre` · `prim_cylindre_libre` | 1,000 | 24–29 s | 29 |
| `prim_sphere_libre` | 1,000 | 31 s | 120 |

### Les deux échecs sont sémantiques, pas de la troncature

- `prim_escalier` : **4 composantes disjointes** (tailles [72, 12, 12, 12]), erreur
  bloquante, échec après 2 réparations. C'est le cas déjà diagnostiqué.
- `prim_sphere` : **modèle vide** après 2 réparations, 610 s. C'est le cas qui mesure
  l'obéissance à une méthode imposée, et il ne produit plus rien du tout.

À noter : les 2 échecs ont consommé **822 s des 1150 s du relevé**, soit 71 % du temps
pour 18 % des cas. La boucle de réparation coûte sur ce qui échoue, pas sur ce qui passe.

### CE QUE CE RELEVÉ RÉVÈLE SUR LE BANC LUI-MÊME

**7 formes sur 9 sont à 1,000 exact.** Le banc est donc, lui aussi, proche de son
plafond : seules **4 formes** peuvent encore bouger — `escalier`, `sphere`, `pyramide`,
`anneau`. Et deux d'entre elles sont documentées comme pathologiques par construction.

Conséquence pour toute comparaison de configurations de génération : elle se jouera
entièrement sur ces 4 formes. Les 7 autres ne feront que reconfirmer 1,000 et coûter du
temps. Un `--n 3` sur les 11 formes, c'est 33 générations dont 21 pour reconfirmer un
plafond. En tenir compte avant de lancer une campagne.

À noter aussi : `--n 1` ne permet **pas** de distinguer une régression d'un aléa de
graine. `prim_escalier` était valide en octobre (IoU 0,139–0,200) et échoue ici ; avec un
seul tirage, on ne peut pas trancher.

### DÉFAUT DE L'INSTRUMENT, à connaître avant de lire un CSV

Sur une ligne en échec, `eval/run_eval.py` laisse `repairs_used` à sa valeur par défaut
**0**, alors que `error.txt` montre 2 réparations réellement consommées. Le résumé
annonce donc « Réparations moyennes : 0.00 » quand le vrai chiffre est **4 réparations
sur 11 générations, soit 0,36**. L'instrument sous-estime le coût de réparation
précisément sur les générations qui coûtent le plus cher. Non corrigé à dessein : le
harnais est l'instrument partagé des relevés archivés, et le modifier casserait la
comparabilité avec eux. Lire `error.txt` pour le vrai compte.

## Les ops de primitives rattrapent un plan faux (arbre, 16 octobre 2026)

Génération d'un arbre, `--bbox-max 16x16x56 --sans-notice`, 30B aux deux étapes.
Prompt appliquant la règle « nommer et dimensionner, ne pas décrire » :

    un arbre : un tronc cylindrique de 4 tenons de diametre et 20 plates de haut en
    reddish_brown, et un feuillage spherique de 16 tenons de diametre en green,
    centre au sommet du tronc et le chevauchant sur 4 plates

Design obtenu, **2 lignes pour 1153 pièces**, 18,0 s, **0 réparation** :

    {"op": "cylinder", "at": [6,6,0], "diameter": 4,  "height": 20, "color": "reddish_brown"}
    {"op": "sphere",   "at": [0,0,16], "diameter": 16,               "color": "green"}

Quatrième confirmation de la règle de la priorité 2, et la **première sur `sphere` dans
un objet composite** (elle n'avait été vérifiée qu'isolée, sur `prim_sphere_libre`).
Rendu vérifié sur `render_iso.png` : c'est un arbre, sans ambiguïté.

### CE QUI EST NOUVEAU : l'op absorbe une erreur du planificateur

Le plan était **faux sur deux points**, et le modèle a quand même produit la bonne forme :

    2. feuillage   X=0..16 Y=0..16 z=16..35   sphérique plein, en green

- `z=16..35` = **20 plates pour une sphère de 16 tenons qui en occupe 40**. Le
  planificateur a pris le diamètre pour une hauteur, alors que `planner_system.txt`
  énonce la règle des 2,5× **deux fois**, dont une avec l'exemple chiffré « 7 tenons →
  18 plates ». Confirme qu'allonger encore ce prompt ne sert à rien (déjà conclu en
  priorité 2) : ne pas retenter.
- `X=0..16` = 17 tenons dans un bbox de 16.

Les deux erreurs sont **absorbées sans dégât** parce que le designer a appelé l'op :
`sphere` calcule elle-même son emprise à partir du seul `diameter`. Argument nouveau en
faveur des primitives — appeler l'op ne fait pas que mieux dessiner, **ça rattrape un
plan faux**. À la main, ces deux erreurs donnaient un feuillage écrasé de moitié et un
dépassement de bbox.

### Score 79,9 — et le modèle est le plus reconnaissable de la journée

| Terme | Valeur | Points |
|---|---|---|
| Joints alignés | 0,903 | **−18,1** |
| Pièces flottantes | 115 / 1153 = 9,97 % | −2,0 |
| **Total** | | **79,9** |

Les 115 flottantes sont le dessous en surplomb du feuillage : un arbre n'a pas d'autre
façon d'exister. L'essentiel de la perte vient des **joints alignés**, mécanique sur une
sphère — la nomenclature le montre : **524 briques 1x1 sur 1153 pièces (45 %)**, parce
qu'une surface courbe se pave en escaliers de 1x1, et une colonne de 1x1 a ses joints
alignés par construction.

Cinquième cas du jour où le score va **à l'inverse** de la qualité visuelle (table basse
93,1 ; château raté 94,4 ; arbre réussi 79,9). Ne pas y voir une régression : confirme la
note « LE SCORE N'EST PAS UNE NOTE DE QUALITÉ » et renforce la priorité 1.

### Notice reconstruite sans aucun appel LLM

Dossier généré avec `--sans-notice`, notice produite après coup en rejouant la chaîne
déterministe depuis `design.json` (`compile_design` → `tile` → `pipeline.preparer_notice`
→ `render_all` → `write_notice_html` / `write_pdf`), soit le chemin de régénération de
l'agent web. **40 étapes** (plafond du regroupement adaptatif), 20 vignettes, 534 s de
rendu Blender pour 60 sous-modèles, PDF de 1,7 Mo. 1153 pièces pour 20 références.

## Le planificateur ne sait pas répartir 5 directions (étoile, 16 octobre 2026)

Tentative d'étoile à cinq branches en relief, `--bbox-max 28x28x10`. **Génération jamais
aboutie** : le designer tournait depuis plus de 10 min sans rendre sa réponse (contre
18 s pour l'arbre) quand le processus a été tué par la pression mémoire. Aucun
`design.json`, donc **rien n'est mesuré sur l'adoption de `layer`** — hypothèse non
vérifiée : le coût de la grammaire ASCII.

Le plan, lui, a été produit (37,7 s) et il est **faux avant tout design** :

    1. Base centrale  X=4..24  Y=4..24  z=0..2   circulaire, 20 tenons
    2. Branche 1      X=12..16 Y=0..4   z=0..9
    ...
    5. Branche 4      X=4..8   Y=20..24 z=0..9   <- entièrement DANS la base

- Les cinq branches sont réparties sur les **quatre bords d'un carré**, pas à 72°. La
  branche 4 tombe à l'intérieur de la base et ne dépassera jamais : il ne resterait que
  quatre pointes.
- Proportions : cœur de 20 tenons contre 4 tenons de dépassement par pointe. Ce n'est
  pas une étoile, c'est une roue dentée.

Troisième cas documenté de « LE DÉFAUT VIENT DU PLANIFICATEUR », après le décalage z et
la confusion plate/brique de `prim_escalier`. Une symétrie d'ordre 5 exclut `mirror`, qui
est axial. Piste non essayée : imposer la répartition et le rapport pointe/cœur dans le
prompt (« tous les 72 degres », « pointes sur un cercle de 28, coeur de 10 »).
