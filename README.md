# Pipeline LEGO — génération de modèles 3D depuis un prompt

Génère des modèles LEGO à partir d'une description en langage naturel, via un LLM local
(LM Studio), en **vraies pièces du catalogue LDraw**, rendus dans Blender.

Le principe directeur : **le LLM ne produit jamais la géométrie finale.** Il produit un
plan en texte, puis un programme d'opérations volumétriques (DSL). Du code Python
déterministe compile ce programme en briques réelles. Ça retire au modèle deux
responsabilités qu'il assume mal — garantir la légalité géométrique et poser les briques
une à une — et ne lui laisse que celle où il est bon : imaginer la forme.

---

Toutes les mesures du projet — IoU par forme, effet de chaque correction, temps de
rendu, correspondances vérifiées — sont rassemblées dans **[docs/mesures.md](docs/mesures.md)**,
avec leur protocole.

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

## Les flux de données

La section précédente dit dans quel **ordre** les étapes s'enchaînent. Celle-ci dit où
chaque donnée **atterrit**, qui a le droit de l'écrire, et ce qu'on peut perdre sans
dommage. Trois questions pratiques : qu'est-ce qu'il faut sauvegarder, qu'est-ce qui se
reconstruit tout seul, et pourquoi tel composant ne peut pas tourner ailleurs.

### Ce qui traverse la chaîne

Deux choses très différentes circulent, et les confondre est la principale source de
malentendu sur ce projet : ce que les fonctions **se passent en mémoire** (à gauche du
schéma), et ce qui est **écrit sur disque** (la colonne de droite). La plupart des fichiers
écrits sont des traces et des livrables que plus rien ne relit.

**Une seule exception, et elle est capitale : `model.ldr`.** Blender le relit depuis le
disque, dans un processus séparé. C'est précisément ce qui rend le rendu rejouable sans
rien régénérer — `tools/render_batch.py` ne fait rien d'autre que balayer des `model.ldr`
déjà écrits.

```
  prompt
    │
    ▼
  [A] src/planner.py            appel LLM libre          ──▶  plan.md
    │       ▲
    │       └── prompts/planner_system.txt
    │ plan_text
    ▼
  [B] src/designer.py           appel LLM contraint      ──▶  design.json
    │       ▲                                                 report.json
    │       └── prompts/designer_system.txt + exemples few-shot
    │
    │   boucle de réparation, 2 tours au plus :
    │     compile_design  ──▶ voxels
    │     tile            ──▶ placements    (lit la bibliothèque LDraw)
    │     validate        ──▶ report  ──▶  si erreurs, message renvoyé au modèle
    │
    │ placements
    │
    │   ═════ plus aucun appel LLM en aval d'ici ═════
    │
    ├──▶ src/ldraw_writer.py                             ──▶  model.ldr
    │        │
    │        └──▶ [F] Blender (ImportLDraw)              ──▶  render_{top,front,
    │                 │   RELIT model.ldr + la bibliothèque      right,iso}.png
    │                 └──▶ src/img_output.py             ──▶  final.png
    │
    └──▶ [E] src/bom.py  ──▶ rows                        ──▶  bom.csv, bom.html
             │
             ├──▶ src/wanted_list.py                     ──▶  wanted_list.xml
             └──▶ src/instructions.py                    ──▶  notice.html
```

Trois choses que ce schéma dit et que l'ordre des étapes ne disait pas :

- **La chaîne déterministe n'est pas en aval du designer, elle est dedans.**
  `src/designer.py` appelle lui-même `compile_design`, `tile` et `validate` dans sa boucle,
  et c'est le `report` du validateur qui repart vers le modèle sous forme de message de
  réparation ciblé. C'est le **seul retour en arrière** du projet ; tout le reste va dans
  un seul sens.
- **Ce qui sort du designer, c'est `placements`** — la liste des vraies pièces posées, déjà
  validée. `model.ldr` n'est qu'une sérialisation de cet objet, et `wanted_list.xml` ne
  descend pas de `model.ldr` mais de la nomenclature (`rows`), une branche parallèle.
- **La bibliothèque LDraw est lue deux fois, à deux fins.** Par `src/catalog.py` pendant le
  pavage, pour les dimensions des pièces ; puis par Blender, pour leur géométrie réelle.

L'étape [E] est **volontairement coupée** du reste : la nomenclature et la notice coûtent
un rendu Blender par étape de montage. Quand plusieurs variantes sont proposées, seule
celle que l'utilisateur retient la mérite — d'où la séparation entre
`pipeline.generer_modele` et `pipeline.preparer_notice`.

### L'invariant : le design DSL est la seule donnée irremplaçable

`design.json` fait 20 lignes et décrit 400 pièces. **Tout ce qui est en aval s'en
redéduit, à l'identique, sans un seul appel LLM** — c'est le sens de la ligne en double
trait du schéma. Deux portes d'entrée l'exploitent :

| | Depuis | Reconstruit |
|---|---|---|
| `python -m tools.build_from_design <design.json>` | un fichier sur disque | `model.ldr` |
| `POST /api/jobs/<id>/regenerer` | la copie en base | rendus, notice, PDF |

Conséquence concrète : si tu perds les rendus, tu les refais pour le prix d'un rendu
Blender. Si tu perds les `design.json`, tu as perdu des appels LLM, c'est-à-dire du temps
de génération. C'est le seul fichier dont la perte coûte vraiment quelque chose.

C'est aussi pour ça qu'il est stocké **deux fois** : `design.json` sur disque, et
`variantes.design_json` en JSONB dans Postgres. La copie en base est l'archive — elle
survit à un `outputs/` effacé, et `tools/recover_eval.py` fait le pendant côté disque en
reconstruisant un CSV depuis les `design.json` survivants.

### Qui écrit où, et pourquoi c'est séparé

| | Postgres | `outputs/` | LM Studio | Blender |
|---|---|---|---|---|
| `server/app.py` (API) | **écrit** | lit seulement | — | — |
| `agent.py` | écrit | **écrit** | appelle | appelle |
| `lego.py` / `pipeline.py` | — | écrit | appelle | appelle |

**L'API ne génère rien et n'écrit jamais dans `outputs/`.** Son montage du dossier est en
lecture seule, et l'image ne contient même pas `src/`. Elle dépose une demande en base et
sert des fichiers ; c'est tout. La base joue le rôle de **file d'attente** : l'agent
réclame un job avec `FOR UPDATE SKIP LOCKED`, ce qui rend l'ajout d'un second agent sûr —
mais un seul à la fois, les deux se disputeraient LM Studio.

Le découpage n'est pas esthétique, il est contraint : un conteneur Linux ne peut pas
exécuter `blender.exe`, et la bibliothèque LDraw comme LM Studio vivent sur l'hôte.
L'agent en conteneur joint LM Studio par `host.docker.internal`.

### Ce qui va en base, et ce qui reste sur disque

La base ne contient **aucune image**. Elle contient la demande, l'état, les mesures, et un
*chemin* vers le disque :

| En base (`jobs`, `variantes`) | Sur disque (`outputs/job_<uuid>/variante_NN/`) |
|---|---|
| `prompt`, `taille`, `n_variantes` | `prompt.txt`, `plan.md` |
| `status`, `etape`, `etape_fait/total` | `log.txt`, `error.txt` |
| `design_json` (JSONB) | `design.json` |
| `score`, `n_pieces`, `fill_rate`, `z_min`… | `report.json` |
| `render_dir` — **un chemin, pas un contenu** | `render_*.png`, `final.png`, `model.ldr` |
| `pdf` (`LargeBinary`) | `bom.csv`, `bom.html`, `notice.html`, `wanted_list.xml` |
| `llm_model`, `seed`, `repairs_used`, `code_version` | `*_request.json`, `*_raw.txt`, `*_meta.json` |

L'asymétrie du bas du tableau est voulue : **le PDF est le seul binaire lourd qui entre en
base**, parce qu'il est produit par un navigateur headless et qu'on ne veut pas le refaire
pour le servir. Les images, elles, sont servies depuis le disque par chemin — les mettre
en base gonflerait les sauvegardes pour rien.

Les `*_request.json` / `*_raw.txt` / `*_meta.json` sont les **traces de chaque appel LLM**,
écrites systématiquement par `src/llm_client.py`. Elles ne sont lues par aucun code : elles
existent pour qu'un résultat surprenant six mois plus tard puisse être expliqué. Elles sont
jetables.

### Les entrées qui ne viennent pas du prompt

| Entrée | Où | Lecture seule |
|---|---|---|
| bibliothèque LDraw (`.dat`) | hors dépôt, cherchée par `src/catalog.py`, `$LEGO_LDRAW_DIR` | oui |
| system prompts, exemples few-shot | `prompts/` | oui |
| jeux de test figés | `eval/testset.json`, `eval/primitives_testset.json` | oui |
| lot de prompts | `prompts.json` | oui |
| chemins, modèle, moteur de rendu | l'environnement, via `config.py` | oui |

Le chemin LDraw résolu est transmis à Blender par `LEGO_LDRAW_DIR` **et** passé
explicitement en `ldrawPath` : l'add-on cherchait sinon dans ses préférences enregistrées,
ce qui marche sur un poste configuré à la main et nulle part ailleurs.

### Les passes qui ne touchent pas à la chaîne

Deux outils lisent les sorties sans rien y réinjecter — ils n'ont donc aucun moyen de
fausser une génération :

- `tools/render_batch.py` rend N modèles dans **un seul** processus Blender : il lit des
  `model.ldr` et écrit des `.png`.
- `src/juge_vlm.py` lit un `render_iso.png` et écrit un `juge_vlm.json` à côté. Il ne voit
  jamais `design.json` — un modèle qui relirait du JSON remplacerait la réponse exacte de
  `src/validator.py` par une opinion. Il écrit dans son propre fichier, jamais dans
  `report.json`, qui ne contient que des mesures exactes. Voir *Le juge visuel*.

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

### Tout démarrer en une commande

```powershell
docker compose --profile conteneur up -d     # base, API, interface ET génération
```

L'image de l'agent embarque **Blender 5.1.1**, l'add-on **ImportLDraw 1.2.3** et la
**bibliothèque LDraw**, à des versions figées. Il n'y a plus rien à installer à la main.

**LM Studio reste sur l'hôte** : il tient ~18 Go en mémoire et veut le CPU de la machine,
l'embarquer n'aurait aucun sens. Le conteneur le joint par `host.docker.internal`.

Le mode hôte reste entier. Sans le profil, `docker compose up -d` se comporte exactement
comme avant et `python agent.py` continue de fonctionner — c'est voulu, les deux chemins
ont leur usage : l'hôte pour développer, le conteneur pour installer ou montrer. Le
profil évite surtout que deux agents démarrent ensemble et se disputent LM Studio.

#### Ce que ça coûte, mesuré

| | |
|---|---|
| taille de l'image | **1,07 Go** |
| rendu d'un même modèle, sur l'hôte | 65 s |
| rendu du même modèle, en conteneur | **36 s** |

Le conteneur est donc **plus rapide**, à l'inverse de ce qu'on attendait. Et les deux
images produites sont équivalentes à l'octet près ou presque (682 762 contre
682 703 octets) : la reproductibilité est réelle, pas supposée.

#### La licence, vérifiée avant tout le reste

Redistribuer la bibliothèque LDraw dans une image n'allait pas de soi. La question a été
tranchée **avant** d'écrire la moindre ligne, et depuis la source livrée avec la
bibliothèque elle-même — `CAlicense.txt`, `CAlicense4.txt`, `CAreadme.txt`, et la ligne
`0 !LICENSE` que porte chacun de ses fichiers de pièce. Elle est sous **Creative Commons
Attribution 2.0 et 4.0**, qui autorisent explicitement la redistribution. L'attribution
qu'elles exigent est écrite dans `/opt/ldraw/ATTRIBUTION` de l'image.

#### Deux pièges rencontrés, et leur cause

**Blender 5.1 ne charge pas les add-ons hérités depuis son dossier d'installation.**
Posé sous `/opt/blender/5.1/scripts/addons`, l'add-on apparaît pourtant dans les chemins
que `bpy.utils.script_paths(subdir='addons')` annonce — et `addon_enable` répond malgré
tout « No module named 'io_scene_importldraw' ». Il faut le poser dans
`bpy.utils.user_resource('SCRIPTS')`, c'est-à-dire là où se trouve celui de l'hôte.

**L'add-on cherchait la bibliothèque dans ses préférences enregistrées.** Ça marche sur
un poste configuré à la main, et échoue partout ailleurs sur « Could not find LDraw Part
Library » — conteneur, profil Blender neuf, autre machine. Le projet sait pourtant où
elle est (`src.catalog`) : le chemin résolu est désormais transmis à Blender dans
l'environnement et passé explicitement à l'opérateur d'import. Ce n'est pas qu'un
correctif de conteneur : il retire une dépendance invisible à un réglage d'interface.

### Les pentes, et pourquoi une pente n'est pas un pavé

Le catalogue est resté longtemps à 24 pièces sans la moindre pente : tous les toits
étaient plats. `AGENTS.md` l'interdisait explicitement — « élargir le catalogue avant que
la géométrie ne soit fiable ». La condition est maintenant remplie (0 génération hors
sol sur 26, IoU global 0,774, aucune collision ni structure disjointe), donc la règle est
levée, en le disant plutôt qu'en l'ignorant.

Deux pièces ajoutées : `3040b` (Slope 45 2 x 1) et `3039` (Slope 45 2 x 2).

**Une pente ne remplit pas sa boîte englobante.** C'est tout le problème : le pavage
raisonnait en pavés pleins, et chaque voxel plein devait être couvert exactement une
fois. La solution retenue garde cet invariant intact :

> `w` et `d` décrivent la seule **empreinte haute** — les cases que la pièce remplit
> vraiment. La rampe déborde d'une rangée supplémentaire, qui doit être **vide**.

Trois conditions de pose, chacune avec son test :

1. **rien au-dessus** — le dessus d'une pente est incliné, il ne porte rien ;
2. **la rangée où descend la rampe est vide** dans la grille, et dans la grille : laisser
   la rampe déborder reviendrait à ignorer le bbox déclaré ;
3. **cette rangée n'est pas déjà réservée** — sinon deux pentes face à face se
   percuteraient dans le creux qui les sépare, et rien ne le verrait : le validateur ne
   connaît que l'empreinte haute.

#### La géométrie, vérifiée et non supposée

Les sommets de `3040b.dat`, sous-fichiers résolus, donnent : X −10..10 (1 tenon),
Z −30..10 (2 tenons), Y −4..24. L'extension en Z **n'est pas centrée sur l'origine** :
la pièce est centrée sur son tenon *haut*, et la rampe descend vers −Z.

C'est exactement le genre de détail qui se paie cher. Prendre `w`/`d` pour l'emprise
totale 1×2 aurait décalé chaque pente d'un demi-tenon, silencieusement. Avec l'empreinte
haute — qui, elle, est centrée sur l'origine — la formule de centrage existante
s'applique **sans aucun changement**.

Une pente a besoin des quatre orientations, là où une pièce pleine n'en distingue que
deux (un demi-tour ne change rien à un pavé). D'où le champ `retournee` à côté de
`rotated`. La correspondance orientation → sens de descente a été **relevée sur un
rendu** des quatre matrices, pas calculée de tête :

| matrice | descente (coordonnées internes) |
|---|---|
| identité | −y |
| 90° | −x |
| 180° | +y |
| 270° | +x |

#### Mesure

L'IoU se calcule sur la **grille de voxels**, que le pavage ne touche pas : élargir le
catalogue ne peut pas le faire bouger, et 27 générations LLM n'auraient rien mesuré. Ce
qui bouge, c'est le nombre de pièces, le décalage des joints et le score du validateur —
tous calculables exactement, et gratuitement, sur les **161 designs déjà en archive**.

| | avant | après |
|---|---|---|
| pièces (moyenne) | 96,87 | **98,89** (+2,1 %) |
| références distinctes | 13,98 | 14,63 |
| joints alignés | 0,2574 | **0,2578** (inchangé) |
| score du validateur | 84,26 | **84,22** (−0,04) |
| erreurs bloquantes | 2 | **2** (les mêmes) |
| designs pavés sans incident | 161/161 | **161/161** |

648 pentes posées, sur 79 designs des 161. Le score est stable (19 en hausse, 18 en
baisse, 124 inchangés) et l'alignement des joints ne bouge pas : la règle de maçonnerie
continue de primer, comme prévu — une pente passe avant le volume, mais après la pénalité
de joint. Un toit joli ne vaut pas un mur qui se fend.

La vérification visuelle a été faite sur une pyramide (19 pentes sur 32 pièces) rendue
avant et après : la ziggourat en gradins devient une pyramide, rampes correctement
orientées sur les quatre faces. `LeoCAD` n'étant pas installé, le contrôle est passé par
un rendu Blender examiné directement — ce qui vaut mieux qu'un contrôle non fait.

### La liste d'achat

La nomenclature donne des références **LDraw**. Pour acheter les pièces il faut les
numéros des boutiques, et `src/bom.py` s'en était délibérément abstenu : pour les pièces
classiques les deux numéros coïncident *le plus souvent*, et « le plus souvent » ne
suffit pas.

Cette prudence était justifiée. La correspondance a été vérifiée une par une sur
bricklink.com — un catalogue de quelques dizaines de pièces rend l'exercice fini — et
**4 pièces sur 26 portent un numéro différent des deux côtés** :

| LDraw | BrickLink | pourquoi |
|---|---|---|
| `3023b` | `3023` | LDraw a renommé la pièce (mise à jour 2024-07), BrickLink non |
| `3070b` | `3070` | BrickLink liste `3070b` comme numéro *alterné*, pas comme numéro principal |
| `3069b` | `3069` | idem |
| `3040b` | `3040` | ce numéro n'existe pas du tout chez BrickLink, pas même en alterné |

La quatrième a été trouvée **par le test** qui verrouille les trois premières, en ajoutant
les pentes : il a signalé `3040b` comme ayant « changé de numéro ». La correspondance a
alors été établie par le nom et les dimensions (BrickLink `3040` « Slope 45 2 x 1 »,
*Stud Dim.* 2 x 1 x 1), puis enregistrée.

Les couleurs sont pires : les deux systèmes utilisent **les mêmes nombres pour des
couleurs différentes**. Le code LDraw 8 est *Dark Grey*, le code BrickLink 8 est *Brown*.
Et BrickLink numérote *Reddish Brown* 88 là où LDraw utilise 70. D'où deux tables
séparées dans `src/catalog.py`, jamais un champ de plus dans la même.

Il n'existe d'ailleurs **aucun fichier de correspondance officiel** entre les deux
numérotations — les discussions LDraw renvoient toutes à des bases tierces.

Le fichier produit est une *wanted list* XML. Son format vient de la page d'aide de
BrickLink, pas d'une supposition — et il réserve un piège : **le fichier ne doit pas
porter de déclaration `<?xml ?>`**.

**Ce n'est pas un panier, et l'interface le dit.** Livré sans mode d'emploi, le fichier
n'était pas utilisable : l'import de panier de BrickLink attend un autre format, et la
confusion est immédiate.

Pire, la page `Want` > `Upload` offre **deux** options qu'il est facile de confondre — et
le fichier n'est pas le même :

| option BrickLink | ce qu'elle accepte | ce qu'on lui donne |
|---|---|---|
| *Upload a file from your computer* | `.bsx` `.ldr` `.lxf` `.io` — **pas** de XML | `model.ldr`, déjà produit à chaque génération |
| *Upload BrickLink XML format* | une zone de **collage**, pas un fichier | le contenu de `wanted_list.xml` |

Les deux marchent, avec un arbitrage réel : par le `.ldr`, c'est **BrickLink** qui fait la
correspondance entre numéros LDraw et numéros de boutique ; par le XML, c'est notre table
vérifiée — avec ses quatre écarts connus et l'avertissement sur les couleurs qui ne sont
plus produites.

L'interface propose donc les deux, en disant clairement lequel va où.

Pourquoi aucune URL ne peut « remplir le panier » d'un coup : un panier appartient à
**un seul magasin**, et un modèle de plusieurs centaines de pièces se répartit sur des
dizaines de vendeurs. C'est le *Buy All* de BrickLink qui cherche la combinaison de
magasins, à partir de la wanted list.

```
wanted_list.xml          produit à côté de bom.csv, par les trois chemins de génération
```

Deux garde-fous :

- une pièce **sans correspondance vérifiée n'est jamais devinée** : elle sort du XML et
  part dans la liste des manquants, affichée à l'utilisateur ;
- `verify_bricklink()` échoue si une pièce du catalogue n'a pas de correspondance
  **explicite**. Élargir le catalogue sans se poser la question du numéro de boutique
  casse donc le test, au lieu de produire une liste d'achat trouée en silence.

Dernier point, découvert en vérifiant les couleurs : les deux gris de la palette
(`light_gray`, `dark_gray`) **ne sont plus produits** depuis 2007 et 2006. Ils restent
au catalogue BrickLink, donc le fichier est valide, mais l'offre est d'occasion. La
liste le signale et nomme l'équivalent actuel (*Light / Dark Bluish Gray*). Changer la
palette serait une décision produit, pas une correction : elle modifierait la couleur de
tous les modèles déjà générés.

### Suivre ce qui se passe

Une génération dure de trente secondes à dix minutes. L'interface n'affichait qu'un
libellé d'étape (« design », « rendu des etapes ») : on ne savait ni où l'on en était
dans la suite des phases, ni s'il restait une minute ou dix, ni même — en lançant une
demande — si quelque chose était parti.

Elle affiche maintenant la suite complète des étapes, avec celle en cours mise en
évidence et son compteur :

```
[x] Envoi au modèle
[x] Proposition 1 sur 4
[>] Proposition 2 sur 4
[ ] Proposition 3 sur 4
[ ] Proposition 4 sur 4
[ ] Images des propositions
[ ] Ton choix
[ ] Nomenclature et étapes de montage
[ ] Images de la notice          50 / 111
[ ] Notice PDF
```

Les propositions sont détaillées une par une parce que c'est la partie longue, et que
« proposition 2 sur 4 » répond à la seule question qu'on se pose en attendant.

L'agent écrit son avancement en texte libre ; la traduction en identifiants stables se
fait **côté serveur**, en un seul endroit (`server/regles.py`). L'interface n'interprète
rien. Un test relit les chaînes écrites dans `agent.py` et `server/app.py` et exige
qu'elles soient toutes connues : ajouter une étape sans la déclarer casse le test au
lieu de faire silencieusement retomber la barre dans l'inconnu.

### Un seul panneau, celui qu'on regarde

Deux sections se faisaient concurrence — « job en cours » et « détail » — et la première
ne désignait qu'une demande, en donnant la priorité à celle qui attendait un choix.
Défaut vicieux : une demande laissée en attente masquait en permanence celle que l'agent
était en train de traiter, et plus rien ne semblait avancer.

Il n'y a plus qu'un panneau. Il suit **ce sur quoi on a cliqué**, sinon ce qui travaille
vraiment. Les propositions, le choix, les quatre vues, la notice et les actions y vivent
ensemble, pour la demande qu'on regarde — plus de duplication de gabarit, et l'option de
régénération n'apparaît que sur la demande concernée.

### Quand une proposition n'aboutit pas

Le validateur rejette ce qui ne tient pas debout, et c'est fréquent. L'utilisateur se
retrouvait alors avec moins de choix que demandé, sans autre recours que de tout
relancer — y compris les propositions réussies, qu'il aurait fallu re-rendre pour rien.

Un bouton propose de ne reproduire **que les manquantes**, dans le même job.
`agent.indices_a_produire` rend la production reprenable : les propositions déjà
abouties sont intactes, seules les ratées sont rejouées, en réutilisant leur indice.

Et l'historique ne montre plus de case grise : la vignette retombe sur la première
proposition rendue quand il n'y a pas encore de variante retenue.

### Refaire ou relancer

Deux actions voisines dont les coûts n'ont rien à voir — et la distinction est tout
l'intérêt de garder `design_json` :

| | repart de | coût | pour quoi |
|---|---|---|---|
| **Refaire la notice** | `design_json` | **aucun appel LLM** | reconstruire rendus, notice et PDF du modèle existant — après un changement de moteur de rendu ou de mise en page, ou si un fichier a été effacé |
| **Relancer** | `prompt` | N générations | obtenir de nouvelles propositions quand aucune ne convient |

« Refaire » remet simplement le job en `en_finition` : toute la chaîne en aval du design
étant déterministe, l'agent la rejoue entièrement. Il reconstruit au passage ce qui
manque sur disque (`agent.chemins_manquants`) — le `.ldr` depuis `design_json`, les
quatre vues si elles ont disparu — au lieu de refuser de partir d'un dossier incomplet.

« Relancer » crée un **nouveau** job rattaché à l'essai d'origine par `jobs.origine_id`.
L'ancien n'est jamais écrasé : ses sorties vivent dans son propre `outputs/job_<id>/`.
Le coût est annoncé avant de lancer, parce qu'une proposition se compte en minutes.

Le rattachement n'est pas qu'un confort d'affichage. Il rend enfin mesurable la
**variance du modèle sur une même demande**, ce que le projet n'a jamais su faire —
c'est exactement ce qui manquait pour juger le recul de `prim_sphere`, où deux
générations exploitables ne disent rien de solide.

`origine_id` pointe **toujours** la racine, jamais l'essai précédent : un seul niveau,
donc regrouper les tentatives est un filtre et non une remontée d'arbre.

Un ajout de colonne sur une base déjà en service ne peut pas passer par `db/init.sql`,
qui n'est joué qu'à la création du volume Postgres : `server.db.assurer_schema()` le
fait en idempotent au démarrage de l'API et de l'agent. Pas d'Alembic — une colonne
ne justifie pas un outil de migration et son état à garder cohérent entre le conteneur
et l'hôte.

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

### Ce que mesure l'IoU, et ce qu'il ne mesure pas

La métrique compare la forme produite à une référence calculée analytiquement. Elle est
invariante à la translation — un cube correct placé dans un coin n'est pas pénalisé —
mais **pas à la taille**, ce qui est voulu : un cube 6×6×6 là où on demandait 8×8×8 doit
l'être.

Elle n'est pas non plus invariante à la **rotation**, et là c'est un problème : les
prompts du jeu de test n'imposent aucune orientation. « Un escalier de 5 marches » ne dit
pas vers où il monte.

C'est ce qui faisait de `prim_escalier` le pire cas du jeu, à 0,149. Le diagnostic :

| | IoU |
|---|---|
| tel quel | 0,200 |
| rotation autorisée | 0,333 |
| hauteur corrigée | 0,429 |
| **hauteur corrigée + rotation autorisée** | **1,000** |

**La forme était exacte.** Le modèle construit un escalier parfait, tourné de 270°, et
dont chaque marche fait 3 plates au lieu d'une — il raisonne en briques là où le prompt
dit « 1 plate ».

D'où `voxel_iou_oriente()` : le meilleur IoU parmi les 8 orientations du plan horizontal.
Le haut et le bas restent fixes, un modèle à l'envers ne tiendrait pas debout. Elle est
rapportée **en plus** de l'IoU principal, dans la colonne `iou_oriente` — jamais à sa
place : remplacer la métrique casserait la comparabilité de tout l'historique de mesures.

La confusion plate/brique, elle, n'est **pas corrigée**. Deux formulations de prompt ont
été essayées et mesurées : sans effet, 0,200 et 0,198 contre 0,149 de référence. Elles
ont été retirées — une ligne de prompt qui ne fait rien coûte des tokens et laisse croire
que le cas est traité.

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

- **La grille n'est pas isotrope** : un tenon fait 20 LDU de large, une plate 8 LDU de
  haut. Une grille cubique est donc un objet écrasé 2,5× à l'écran. `cube`, `box` et
  `cylinder` reçoivent leurs dimensions axe par axe — c'est l'utilisateur qui décide, et
  un cylindre de 9 tenons sur 4 plates est bien une galette parce que c'est ce qui a été
  demandé. **La sphère, elle, est isotrope par définition** : son unique paramètre est un
  diamètre réel, et elle occupe donc ~2,5× plus de plates en Z que de tenons en X/Y — 18
  plates pour 7 tenons. La référence d'IoU et le rastériseur du DSL sont la même
  fonction, donc les deux ne peuvent pas diverger. Voir *Une sphère qui n'en était pas
  une*.
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
| `eval/primitives_testset.json` | 11 formes (cube, cylindre, sphère…) | **IoU** vs référence exacte |

La métrique **IoU** (`src/primitives.py`) compare la forme produite à une référence
calculée analytiquement : sur un prompt sémantique, un mauvais résultat ne dit pas *où*
ça casse ; sur « un cube 8×8×8 », l'écart devient un chiffre. Elle est invariante à la
translation mais pas à la taille.

### Quatre cas pour deux formes, et ils ne mesurent pas la même chose

Le DSL a une opération `sphere`, rastérisée par la **même fonction** que la référence
d'IoU : l'utiliser donne 1,000 par construction. Le modèle ne l'a pourtant **jamais**
choisie — 0 fois sur 7 — et `prim_sphere` plafonnait à 0,365, le pire score du jeu.

La cause, démontrée à variable unique : la phrase du prompt figé qui dicte la **méthode**.

| prompt | op `sphere` | IoU |
|---|---|---|
| `prim_sphere`, tel quel | 0 / 7 | 0,365 |
| + l'unité manquante (« et 7 plates de haut ») | 0 / 3 | 0,324 |
| **− la méthode** (« chaque couche horizontale est un disque… ») | **3 / 3** | **1,000** |

En retirant cette seule phrase, les trois designs produits sont identiques, longs d'**une
ligne**, et aucun n'a demandé de réparation. Pour situer : un cylindre plein posé bêtement
vaut 0,691 et une simple boîte 0,522 — **les couches dessinées à la main faisaient pire
que de ne rien tenter**.

**Le même effet existe sur le cylindre, et il n'y faut même pas de recette.** Son prompt
figé se contente de *décrire* la forme — « la section horizontale est un disque ». Ça
suffit : 6 adoptions sur 9 et 0,674 d'IoU. En retirant cette demi-phrase, 3 sur 3 et
1,000.

| forme | clause en trop | adoption | IoU |
|---|---|---|---|
| sphère | une **recette** (« chaque couche… rétrécissant… ») | 0/7 → 3/3 | 0,365 → 1,000 |
| cylindre | une **description** (« la section horizontale est un disque ») | 6/9 → 3/3 | 0,674 → 1,000 |

**La règle qui en sort** : pour un prompt visant une forme que le DSL sait rastériser,
donner le **nom et les dimensions, rien de plus**. Décrire la géométrie pousse le modèle à
la construire à la main, et il la construit moins bien que l'opération.

D'où quatre cas plutôt que deux. `prim_sphere` et `prim_cylindre` **ne sont pas corrigés**
— leur historique de mesures vaut plus que leur score, et ils mesurent quelque chose de
réel : l'obéissance à une consigne de forme. `prim_sphere_libre` et `prim_cylindre_libre`
mesurent la construction. **Ne pas lire la moyenne du jeu sans savoir que les quatre y
sont.**

Ce qu'il ne faut pas retenter, parce que c'est mesuré et sans effet : un exemple few-shot
avec `sphere`, et l'allongement de `planner_system.txt`. La consigne d'unité y figure déjà
deux fois, dont une dans le gabarit de sortie — le modèle recopie par-dessus l'unité qu'il
lit dans le prompt utilisateur.

Si un run est interrompu, les designs déjà produits survivent sur disque et les métriques
se recalculent sans aucun appel LLM :

```powershell
python -m tools.recover_eval eval/results/_work/v1 v1_recovered
```

### Le juge visuel : mesuré, et refusé

Le trou de mesure du projet est connu : sur « un château avec quatre tours », aucun
chiffre ne dit si le résultat ressemble à un château. L'idée était d'y mettre un **second
modèle, de vision**, qui note le rendu de 0 à 10 — LM Studio sert plusieurs modèles à la
fois, le champ `model` de la requête suffit à les distinguer.

Avant de s'en servir, il faut savoir s'il vaut quelque chose. Le jeu primitives est le
seul endroit du projet où on connaît la bonne réponse : l'IoU se calcule analytiquement
contre une référence. On fait donc noter au juge **91 rendus dont on sait déjà ce qu'ils
valent**, et on mesure l'accord par une corrélation de **rang** (Spearman) — on lui
demande de classer les rendus dans le bon ordre, pas de prédire l'IoU.

```powershell
python -m tools.calibrer_juge --model qwen/qwen3-vl-4b
```

**Le résultat, publié tel quel.** Qwen3-VL-4B, 91 rendus, 0 échec, barème à plages :

| corrélation de rang | vs `iou` | vs `iou_oriente` |
|---|---|---|
| tous les rendus | **+0,382** | **+0,412** |
| sans `prim_sphere` | +0,310 | +0,333 |
| sans `prim_escalier` | +0,502 | +0,502 |

C'est **faible**, et la barrière était annoncée avant la mesure à 0,55. Le juge n'est donc
pas devenu une métrique : il n'y a pas de colonne `note_vlm` dans le CSV d'évaluation.
Un juge à 0,41 ne permet pas de dire si un changement améliore ou dégrade un résultat,
ce qui était exactement sa raison d'être.

Le détail par forme dit *pourquoi*, et c'est plus instructif que le chiffre global :

| forme | IoU | note moyenne | |
|---|---|---|---|
| `prim_croix` | 1,000 | 10,0 | accord exact |
| `prim_cube` | 0,958 | 10,0 | accord exact |
| `prim_pave` | 0,889 | 10,0 | accord exact |
| `prim_escalier` | 0,152 | 7,1 | **le juge a raison, l'IoU a tort** |
| `prim_pyramide` | 0,360 | 8,1 | juge trop indulgent |
| `prim_tour` | 0,996 | 6,0 | juge trop dur |
| `prim_anneau` | 0,828 | 4,2 | juge trop dur |
| `prim_cylindre` | 0,674 | 2,2 | juge très trop dur |
| `prim_sphere` | 0,365 | 0,4 | les deux disent « mauvais » |

De loin, on lit « exact sur les formes rectilignes, s'effondre sur les courbes ». En
regardant les rendus, c'est plus précis et moins flatteur : **le juge ne pèse pas la même
chose selon la forme.**

Les deux cas sont comparables à dessein. Dans ce jeu, X et Y se comptent en tenons (20
LDU) et Z en plates (8 LDU) : une forme dont la hauteur est donnée en plates est donc
**aplatie à l'écran**, et ni `prim_cube` ni `prim_cylindre` ne ressemblent à leur nom.

| | demandé | à l'écran | rapport | IoU | note |
|---|---|---|---|---|---|
| `prim_cube` | 8 tenons de côté, 8 plates de haut | 160 × 64 LDU | 2,5:1 | 0,958 | **10,0** |
| `prim_cylindre` | 9 tenons de diamètre, 4 plates de haut | 180 × 32 LDU | 5,6:1 | 0,674 | **2,2** |

Les deux rendus respectent leur prompt **à la lettre**. Le juge en valide un et rejette
l'autre, et ses justifications disent pourquoi :

> `prim_cube`, 10/10 — « L'objet est un cube parfaitement reconnaissable, avec 8 tenons de
> côté (X et Y) et 8 plates de haut (Z), comme demandé, et toutes les proportions et
> l'agencement sont justes. »

> `prim_cylindre`, 3/10 — « l'assemblage de plaques LEGO forme un bloc rectangulaire avec
> des dents, pas un cylindre. »

Sur la boîte il **vérifie les dimensions** du prompt et valide ; sur la courbe il **juge le
nom** et refuse, en ignorant les 4 plates qu'il venait de lire. Les deux objets sont
pourtant la même galette. Ce n'est donc pas le crénelage des briques qui le gêne — la
consigne de ne pas le pénaliser est respectée sur le cube — c'est qu'il n'applique pas le
même critère d'une forme à l'autre. Un juge incohérent est pire qu'un juge sévère : la
sévérité se recalibre, l'incohérence non.

Il utilise 8 valeurs de note distinctes sur 11 : l'échelle ne s'est pas effondrée, c'est un
vrai désaccord, pas un juge qui met la même note partout.

**Au passage, ceci est un résultat sur le jeu de test, pas sur le juge.** Les prompts sont
littéralement exacts, mais `prim_cube` et `prim_cylindre` nomment des objets que leur
propre spécification rend impossibles à l'écran. L'IoU ne peut pas le voir : sa référence
est calculée dans les mêmes unités, donc elle donne ~1,0 à une galette appelée cube. Les
deux métriques du projet sont d'accord sur ces cas — et toutes les deux à côté.

Deux résultats secondaires valent d'être gardés :

- **Il corrobore le diagnostic de `prim_escalier`.** L'IoU dit 0,152, le juge dit 7,1 et
  écrit « l'escalier est reconnaissable avec 5 marches ». La forme *est* exacte — c'est
  ce que ce README documente plus haut, atteint par un autre chemin.
- **Corréler contre `voxel_iou` seul fait porter au juge un défaut de la vérité terrain.**
  `voxel_iou` punit une rotation ; un modèle qui regarde une image n'a aucun moyen de
  savoir quelle orientation était attendue, et le prompt du jeu de test ne la dit pas.
  D'où les deux colonnes. L'écart n'est que de +0,03 : la rotation n'était qu'une moitié
  du problème de `prim_escalier`, la confusion plate/brique déforme aussi l'axe Z.

Le code reste en place, et **désactivé par défaut** : sans `LEGO_VLM_MODEL`,
`juge_vlm.disponible()` est faux et aucune génération ne l'appelle. Il coûte **10,6 s par
note** sur un VLM 4B local, soit 16 min pour les 91 rendus — à condition que le générateur
30B ne soit pas chargé en même temps : en concurrence CPU avec Blender, la même note passe
à 97 s. Relancer la calibration sur des notes déjà produites est en revanche gratuit
(`--resume`, 0,3 s par note) : seules les notes coûtent, les deux IoU se recalculent.

Ce qui reste à essayer, si le sujet revient : un modèle de vision plus gros, ou noter les
4 vues au lieu de la seule vue isométrique. Ce qui ne doit **pas** être réessayé : un
troisième barème. Deux l'ont déjà été, et au troisième on n'étalonne plus un juge, on
optimise un prompt contre un jeu de test — on fabrique le signal qu'on prétend mesurer.

---

## Tests et qualité

```powershell
pip install -r requirements-dev.txt

python -m ruff check .        # linter + tri des imports
python -m ruff check . --fix  # corrige ce qui est sûr
python -m pytest tests/ -q    # 259 tests
```

Le linter garde le jeu de règles **par défaut** de `ruff` plutôt qu'une sélection
choisie à l'avance : au premier passage il a trouvé un `except:` nu qui attrapait
jusqu'au `Ctrl+C`, deux imports morts et une variable déballée jamais lue. Chaque règle
désactivée porte sa raison dans `pyproject.toml`.

Les captures larges (`except Exception`) qui sont **volontaires** — les frontières où un
échec ne doit pas tout emporter — sont marquées une par une par un `noqa` commenté,
plutôt que désactivées globalement. Une négligence future sera donc signalée.

259 tests, couvrant le schéma DSL, le compilateur, le pavage, le validateur, les
primitives, la nomenclature, le découpage en étapes, la notice PDF, les règles de
relance de l'API et la détection du piège LM Studio.

Les règles de l'API sont testées via `server/regles.py`, qui n'importe ni FastAPI ni la
base : `fastapi` n'est installé que dans le conteneur, donc un test qui importerait
`server/app.py` ne tournerait nulle part sur l'hôte. Un autre test vérifie que
l'interface n'appelle que des routes réellement déclarées — les deux côtés vivent dans
des fichiers séparés et aucun build ne les relie, une faute de frappe dans une URL ne
se verrait sinon qu'à l'exécution, sous la forme d'un bouton qui ne fait rien.

L'interface elle-même est vérifiée en chargeant son vrai script avec un `Vue.createApp`
de substitution : le framework est absent, le code testé est bien celui du fichier servi.
`node` sert là d'interpréteur de vérification, pas de dépendance du projet — l'image ne
contient toujours pas node et ces tests se sautent s'il manque. Un harnais frontend
complet reste à faire (US V2-10).

Trois d'entre eux valent d'être signalés : l'un verrouille le fait que le DSL exprime
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
      wanted_list.xml             # liste d'achat BrickLink, importable telle quelle
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
| #2 | mesure figée du jeu primitives | fait — 9 formes, IoU 0,706 → 0,774 |
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
décalage `z`. Mesure figée **complète** : les 9 formes, 3 tentatives chacune, via le
vrai planificateur.

| | avant | après |
|---|---|---|
| IoU global | 0,706 | **0,774** |
| IoU formes en `box` | 0,769 | **0,830** |
| IoU formes courbes | 0,619 | **0,698** |
| **ne touchent pas le sol** | **9/26** | **0/26** |
| durée moyenne par génération | 78 s | **52 s** |
| JSON valide | 26/27 | 26/27 |

| forme | avant | après | |
|---|---|---|---|
| `prim_cube` | 0,875 | **1,000** | |
| `prim_pave` | 0,833 | **1,000** | |
| `prim_cylindre` | 0,750 | **1,000** | opération `cylinder` adoptée |
| `prim_anneau` | 0,838 | **0,947** | `cylinder` + `carve` |
| `prim_croix` | 1,000 | 1,000 | |
| `prim_tour` | 1,000 | 1,000 | |
| `prim_pyramide` | 0,330 | 0,384 | |
| `prim_escalier` | 0,139 | 0,149 | reste le pire cas du jeu |
| `prim_sphere` | 0,527 | 0,342 | voir ci-dessous |

**Le décalage `z` venait du planificateur, pas du designer.** Son plan écrivait
`z=1..8` et le designer obéissait au plan reçu. Corriger le prompt du designer n'a rien
changé (8/26 → 9/26) ; la consigne appartient à `prompts/planner_system.txt`.

Ce défaut n'était pas cosmétique. En démarrant à `z=1` dans une boîte de hauteur *n*, le
modèle se faisait tronquer d'une plate contre son propre bbox : les IoU observés valaient
exactement (n−1)/n — 7/8 pour le cube, 5/6 pour le pavé, 3/4 pour le cylindre. Une
couche entière perdue à chaque génération. C'est pourquoi trois formes passent d'un coup
à l'IoU parfait.

**Le recul de `prim_sphere` n'est pas une régression.** Sur trois tentatives, une échoue
(elle échouait déjà avant) et les deux autres divergent : l'une reproduit exactement le
résultat d'avant (0,527 avec `layer`), l'autre bascule sur `box` + `carve` et tombe à
0,157 — forcément, un cube ne fait pas une sphère. La moyenne repose donc sur **deux
générations exploitables**, dont une qui joue à pile ou face sur la méthode. Le chiffre
n'est pas statistiquement solide.

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
