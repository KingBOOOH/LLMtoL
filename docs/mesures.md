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
