Objectif du projet

Ce projet génère automatiquement des modèles LEGO en 3D à partir d'un prompt utilisateur,
en **vraies pièces du catalogue LDraw**, rendues dans Blender.

Principe directeur : le LLM ne produit JAMAIS la géométrie finale. Il produit un plan en
texte, puis un programme d'opérations volumétriques (DSL). Du code Python déterministe
compile ce programme en briques réelles. Cela retire au modèle deux responsabilités qu'il
assume mal (garantir la légalité géométrique, poser les briques une à une) et ne lui laisse
que celle où il est bon : imaginer la forme.

La pipeline est :

Prompt utilisateur
  -> planificateur (appel LLM libre)        -> plan.md
  -> designer (appel LLM contraint par DSL) -> design.json
  -> compilateur                            -> grille de voxels
  -> pavage                                 -> pièces du catalogue
  -> export LDraw                           -> model.ldr
  -> Blender (add-on ImportLDraw)           -> 4 rendus orthographiques
  -> assemblage                             -> final.png

Une chaîne antérieure demandait directement au LLM une liste de cubes. Elle a été retirée :
pas de vraies pièces, aucune garantie géométrique, et 15 générations de créatures sur 15
produisaient des membres détachés. Ses mesures de référence restent figées dans
eval/results/ mais ne sont plus regénérables.

Architecture réelle

Point d'entrée principal :

pipeline.py
  Orchestrateur bout-en-bout. Lit prompts.json, génère tous les modèles, puis lance UN
  SEUL processus Blender pour tout rendre, et assemble les planches.

Génération (appels LLM) :

src/llm_client.py
  Seul endroit du projet qui fait un requests.post. Journalise systématiquement requête,
  réponse brute et métadonnées. Gère les retries RÉSEAU uniquement.
src/planner.py
  Étape A : appel libre (pas de response_format), température 0.7. Sort un plan en texte.
src/designer.py
  Étape B : appel contraint par le schéma DSL, few-shot multi-tours, température 0.3.
  Contient la boucle de réparation sémantique (max 2), distincte des retries réseau.

Chaîne déterministe (aucun appel LLM) :

src/dsl_schema.py    Schéma JSON du DSL + palette de 12 couleurs
src/compiler.py      DSL -> grille de voxels numpy
src/tiler.py         voxels -> pièces du catalogue, avec décalage des joints
src/ldraw_writer.py  pièces -> model.ldr
src/catalog.py       24 pièces + palette, vérifiées contre la bibliothèque LDraw
src/validator.py     contrôles géométriques -> report.json
src/primitives.py    formes de référence analytiques + métrique IoU
src/blender_script.py  model.ldr -> 4 rendus (s'exécute DANS Blender)
src/img_output.py    4 rendus -> planche final.png

Outils :

tools/check_env.py        diagnostic environnement, lecture seule
tools/build_from_design.py  design.json -> model.ldr, sans appel LLM
tools/render_batch.py     rend N modèles en un seul processus Blender
tools/recover_eval.py     reconstruit un CSV depuis les design.json survivants

Évaluation :

eval/run_eval.py             harnais, avec --resume et --compare
eval/testset.json            20 prompts sémantiques figés
eval/primitives_testset.json 9 formes primitives, avec référence calculable

Codex doit confirmer les fichiers réellement présents avant de modifier quoi que ce soit.

Format DSL cible

Un design est un PROGRAMME d'opérations volumétriques, pas une liste de briques : 20 lignes
décrivent 400 pièces. Coordonnées entières, X/Y en tenons, Z en plates (1 brique = 3 plates).

{
  "name": "bateau",
  "bbox": [16, 6, 8],
  "ops": [
    {"op": "box",    "from": [0, 0, 0], "to": [15, 5, 1], "color": "reddish_brown"},
    {"op": "box",    "from": [4, 1, 2], "to": [11, 4, 3], "color": "tan"},
    {"op": "column", "at": [7, 2, 4], "height": 4, "color": "white"}
  ]
}

Opérations : box, carve, column, layer (grille ASCII), cylinder, sphere, mirror,
translate_group. cylinder et sphere sont rastérisées par src/primitives.py — les
MÊMES fonctions qui servent de référence à l'IoU, pour que les deux ne divergent
jamais. Leur champ `at` est le coin bas de la boîte englobante, pas le centre.
translate_group n'accepte PAS cylinder/sphere en imbriqué : chaque variante
grossit la grammaire que LM Studio compile, et l'inférence est limitée par le CPU.
Palette fermée de 12 couleurs (voir src/dsl_schema.py).

Le schéma JSON est le principal outil d'alignement : bien borné, il rend le modèle
INCAPABLE d'émettre une coordonnée aberrante ou une couleur hors palette. Ne pas le
relâcher sans mesurer l'effet avec le harnais.

Règles de validation (src/validator.py)

Bloquant :
  modèle vide (sinon "ne rien produire" est la stratégie gagnante triviale)
  collisions entre pièces
  structure disjointe (plus d'une composante connexe)

Avertissement, pénalisé au score :
  pièce flottante, dépassement du bbox déclaré, symétrie annoncée non vérifiée,
  taux de remplissage > 85 %, centre de masse non soutenu, joints alignés

Écart assumé avec la version précédente de ce document : "pièce flottante" n'est PAS
bloquant. Le contrôle se déclenche sur des constructions parfaitement normales (les
plaques centrales d'un toit plat au-dessus d'une pièce creuse) ; utilisé comme barrière,
il faisait échouer quasiment toute génération.

Problèmes connus à surveiller

LM Studio réinjecte silencieusement le system prompt et le Structured Output configurés
sur le modèle dans son interface, dès qu'une requête n'envoie pas son propre
response_format. Le réglage REVIENT à chaque rechargement du modèle. Symptôme : le modèle
répond la même structure JSON quel que soit le prompt, même sans rapport. Ce piège a coûté
du temps trois fois. Vérifier avant tout run long.

Le modèle décalait ses indices (z=1 au lieu de z=0) : 10 designs sur 87 tous jeux
confondus, 8 sur 26 sur le jeu primitives. Le system prompt dit maintenant explicitement
que z=0 est le sol, et src/validator.py le détecte nommément (check_ground_contact,
métrique z_min, colonne z_min du CSV d'éval). Remesuré : 0 sur 9 (voir la réserve
méthodologique ci-dessous).

LM Studio garde un Structured Output collé au modèle qwen3-coder-30b, hérité de la
chaîne v0 supprimée (llm.prediction.structured + llm.prediction.systemPrompt dans
.lmstudio/.internal/user-concrete-model-default-config/qwen/qwen3-coder-30b.json). Il
force la réponse au schéma action/cube/rectangle. Vérifié : ni un system prompt à nous,
ni un response_format {"type":"text"} ne le neutralisent — seul un json_schema explicite
le fait. Le designer est donc immunisé, le planificateur NON : il répond
[{"action":"create","object":"cube"}] à n'importe quoi. Se vide dans l'interface LM
Studio (My Models > le modèle > vider System Prompt et Structured Output). Tant que ce
n'est pas fait, eval/run_eval.py est inutilisable (il passe par le planificateur).

Le modèle ne sait pas rastériser un cercle. Formes en box : IoU 0.78 de moyenne. Formes
courbes : 0.39, pour un temps de génération 9x supérieur. Le DSL PEUT les exprimer
exactement (test verrouillé, IoU aller-retour = 1.0) : le goulot était le modèle, pas le
langage — d'où les opérations cylinder/sphere, qui déplacent le rastérisage vers Python.
Conséquence sur la métrique : un IoU de 1.0 sur prim_cylindre mesure désormais que le
modèle a choisi la bonne opération et les bons paramètres, plus qu'il a su tracer un
cercle. Ce n'est pas la même chose, et c'est voulu.

La sphère est ronde dans la grille mais APLATIE à l'écran (~2.5x) : `diameter` vaut
autant de tenons en X/Y que de plates en Z, or un tenon fait 20 LDU et une plate 8.
src/primitives.py assume ce choix et les références d'IoU l'utilisent ; changer l'un
sans l'autre rendrait la métrique fausse.

EEVEE rend les pièces en aplat uniforme (groupes de nœuds de l'add-on écrits pour Blender
2.81). Utiliser Cycles, ou Workbench pour itérer vite.

Les runs longs en arrière-plan se font tuer par la pression mémoire (LM Studio retient
~18 Go). Lancer en processus détaché via Start-Process, et utiliser --resume.

Codex doit toujours chercher à corriger la cause racine, pas seulement masquer l'erreur.

Règles de développement

Codex doit :

inspecter le code existant avant toute modification ;
vérifier empiriquement toute convention externe (LDraw, API Blender, comportement d'un
  modèle) plutôt que de la supposer, puis figer le résultat dans une constante commentée ;
mesurer avant et après une modification d'alignement, avec eval/run_eval.py ;
proposer un plan avant une modification importante ;
modifier par petites étapes ;
relancer les tests après chaque correction (python -m pytest tests/ -q) ;
signaler un écart entre une spec et la réalité du code plutôt que de forcer ;
expliquer clairement les erreurs rencontrées ;
préserver les fonctionnalités existantes.

Codex ne doit pas :

demander au LLM de produire directement des briques ou des coordonnées finales ;
simuler ou mocker un résultat pour faire passer un test — si une étape ne peut pas être
  testée, le dire ;
élargir le DSL au-delà des huit opérations actuelles sans une mesure qui montre
  laquelle manque ;
élargir le catalogue de pièces avant que la géométrie ne soit fiable ;
corriger un mauvais alignement en allongeant le system prompt sans mesurer d'abord ;
supprimer un fichier sans validation ;
ignorer les erreurs silencieusement ;
remplacer une erreur claire par un try/except trop large ;
casser la compatibilité Windows (attention à l'encodage : sortie redirigée = cp1252).

Priorités techniques actuelles

Priorité 1 :
  REFAIRE LA MESURE FIGÉE. Le décalage z et cylinder/sphere sont mesurés, mais avec le
  designer appelé directement, un plan écrit à la main et UNE tentative par forme — la
  référence, elle, fait 3 tentatives avec le vrai planificateur. C'est une indication
  solide, pas la mesure comparable. Vider le Structured Output dans LM Studio, puis :
    python -m eval.run_eval --version v1prim --n 3 --testset eval/primitives_testset.json
  Référence : eval/results/v1prim_20260923_093512.csv.

  Mesuré (indication, 1 tentative par forme) :
    IoU boîte   0.777 -> 0.886
    IoU courbe  0.391 -> 0.835
    durée courbe  339s -> 29s
    ne touchent pas le sol  8/26 -> 0/9
    cylindre 0.272 -> 1.000 (op cylinder adoptée) ; anneau 0.698 -> 0.947 (cylinder+carve)

  Reste ouvert : le modèle n'adopte PAS sphere, il garde layer (IoU 0.526). Cause
  identifiée : le prompt figé du jeu de test dicte la méthode — "chaque couche
  horizontale est un disque, le plus large au milieu, rétrécissant vers le haut et vers
  le bas". Le modèle obéit au prompt. Celui du cylindre ne prescrit rien, d'où
  l'adoption. Ajouter un exemple few-shot avec sphere a été essayé et MESURÉ : sans
  effet sur l'adoption, et IoU dégradé (0.526 -> 0.258) — exemple retiré. La vraie
  question est de savoir si on reformule un prompt du jeu figé (ce qui casse la
  comparabilité) ou si on accepte que ce cas mesure l'obéissance au prompt.

Priorité 2 :
  similarité sémantique (CLIP/VLM). Sur un objet non primitif, aucune métrique ne dit
  aujourd'hui si le résultat ressemble au prompt. LM Studio sait servir plusieurs
  modèles à la fois (champ `model` de la requête, llm_client.py l'envoie déjà) : un VLM
  qui note render_iso.png est la bonne forme. À calibrer d'abord sur le jeu primitives,
  où l'IoU donne la vérité terrain — si la note du VLM ne corrèle pas avec l'IoU connu,
  le juge est inutilisable et ça se sait sans dépenser une génération.
  Ne PAS mettre un modèle texte à vérifier la géométrie : src/validator.py le fait déjà,
  exactement et gratuitement.

Priorité 3 :
  LPub3D n'a jamais été évalué avant d'écrire src/instructions.py (le plan le demandait).
  À faire si la notice doit progresser sérieusement.

Organisation des outputs

outputs/
  run_<YYYYMMDD_HHMMSS>/
    prompt_001/
      iteration_001/
        prompt.txt              prompt utilisé
        plan.md                 plan généré (étape A)
        design.json             programme DSL validé (étape B)
        report.json             rapport du validateur
        model.ldr               modèle en vraies pièces
        planner_request.json    payload exact envoyé
        planner_raw.txt         réponse brute
        planner_meta.json       tokens, durée, température, seed, tentatives
        designer_*.json/.txt    idem pour l'étape B (+ _repair1, _repair2)
        render_{top,front,right,iso}.png
        final.png               planche 2x2
        log.txt                 journal de l'itération
        error.txt               uniquement en cas d'échec

Deux exécutions successives ne s'écrasent jamais.

Environnement

Windows. Blender installé localement, LM Studio en local.
Chemins centralisés dans config.py. La bibliothèque LDraw est cherchée automatiquement
(src/catalog.py), y compris un sous-dossier ldraw/ imbriqué et un Documents/ redirigé
par OneDrive.

Prérequis vérifiables d'un coup : python tools/check_env.py

Commandes utiles

python -m pytest tests/ -q                      tests (64)
python tools/check_env.py                       diagnostic environnement
python pipeline.py                              bout-en-bout depuis prompts.json
python -m src.designer "<prompt>" <dossier>     une seule génération
python -m tools.build_from_design <design.json> design -> .ldr, sans LLM
python -m tools.render_batch --glob "<motif>" --assemble
python -m eval.run_eval --version v1 --n 3
python -m eval.run_eval --version v1prim --n 3 --testset eval/primitives_testset.json
python -m eval.run_eval --compare <csv_a> <csv_b>

Vérification syntaxe :
Get-ChildItem -Recurse -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }
