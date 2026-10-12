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

Points d'entrée :

lego.py
  Un prompt -> un dossier complet (plan, design, model.ldr, 4 rendus, planche,
  nomenclature, notice). Options : --taille, --iterations, --sans-notice.
pipeline.py
  Orchestrateur de lot. Lit prompts.json, génère tous les modèles, puis lance UN
  SEUL processus Blender pour tout rendre, et assemble les planches. Expose
  generer_modele / preparer_notice / render_all, réutilisés par lego.py et agent.py :
  un seul chemin de génération dans le projet.
agent.py
  Prend les jobs déposés en base par l'API web et les traite sur l'hôte (Blender et
  LM Studio y sont). Reprend un job abandonné après une coupure mémoire.
  Sert aussi de chemin de RÉGÉNÉRATION : l'API remet un job terminé en `en_finition`
  et toute la chaîne en aval du design est rejouée, sans aucun appel LLM. L'agent
  reconstruit ce qui manque sur disque (chemins_manquants) plutôt que de refuser de
  partir d'un dossier incomplet.

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

Schema de la base : db/init.sql n'est joue qu'a la CREATION du volume Postgres. Un
ajout de colonne sur une base en service passe donc par server.db.assurer_schema(),
idempotent, appele au demarrage de l'API et de l'agent. Pas d'Alembic : une colonne ne
justifie pas un outil de migration et son etat a garder coherent entre le conteneur et
l'hote. jobs.origine_id relie les essais successifs d'un meme prompt et pointe TOUJOURS
la racine, jamais l'essai precedent.

Problèmes connus à surveiller

LM Studio réinjecte silencieusement le system prompt et le Structured Output configurés
sur le modèle dans son interface, dès qu'une requête n'envoie pas son propre
response_format. Le réglage REVIENT à chaque rechargement du modèle. Symptôme : le modèle
répond la même structure JSON quel que soit le prompt, même sans rapport. Ce piège a coûté
du temps trois fois. Vérifier avant tout run long.

Décalage d'indice z : RÉSOLU, mais pas là où on le croyait. Le défaut venait du
PLANIFICATEUR, pas du designer. Son plan écrivait "z=1..8" et le designer obéissait au
plan qu'on lui donne. Corriger prompts/designer_system.txt n'a rien changé (mesuré :
8/26 avant, 9/26 après). La consigne a été déplacée dans prompts/planner_system.txt :
"Z IS 0-INDEXED AND z=0 IS THE GROUND", plus un z=0 figé dans le gabarit de sortie.

Mesuré sur le jeu complet (9 formes, 3 tentatives) : 9/26 hors sol -> 0/26. Les trois
formes concernées passent de 0.819 a 1.000 d'IoU.

Le décalage n'était PAS cosmétique : en démarrant à z=1 dans un bbox de hauteur n, le
modèle se faisait tronquer d'une plate par le haut contre son propre bbox. Les IoU
observés valaient exactement (n-1)/n — 7/8=0.875 pour le cube, 5/6=0.833 pour le pavé,
3/4=0.750 pour le cylindre. Une couche entière perdue à chaque fois.

Leçon de méthode : la première "mesure" de cette correction était fausse parce qu'elle
contournait le planificateur avec un plan écrit à la main contenant "poser au sol
(z=0)". Elle mesurait cette phrase, pas la correction. Ne jamais court-circuiter une
étape de la chaîne pour mesurer l'effet d'un changement sur cette chaîne.

LM Studio garde un Structured Output collé au modèle qwen3-coder-30b, hérité de la
chaîne v0 supprimée (llm.prediction.structured + llm.prediction.systemPrompt dans
.lmstudio/.internal/user-concrete-model-default-config/qwen/qwen3-coder-30b.json). Il
force la réponse au schéma action/cube/rectangle. Vérifié : ni un system prompt à nous,
ni un response_format {"type":"text"} ne le neutralisent — seul un json_schema explicite
le fait. Le designer est donc immunisé, le planificateur NON : il répond
[{"action":"create","object":"cube"}] à n'importe quoi. Se vide dans l'interface LM
Studio (My Models > le modèle > vider System Prompt et Structured Output). Tant que ce
n'est pas fait, eval/run_eval.py est inutilisable (il passe par le planificateur).

Le modèle ne sait pas rastériser un cercle. D'où les opérations cylinder/sphere, qui
déplacent le rastérisage vers Python (src/primitives.py). MESURE FIGÉE COMPLÈTE, 9
formes x 3 tentatives via le vrai planificateur. Référence avant cylinder/sphere :
v1prim_20260923_093512 ; après : v1prim_20261010_185403 ; après correction du z :
v1primz_* + v1primB_* + v1primC_*.

  cylinder/sphere seuls   IoU global 0.614 -> 0.706   courbes 0.391 -> 0.619
  puis correction du z    IoU global 0.706 -> 0.774   courbes 0.619 -> 0.698
                          box 0.769 -> 0.830          durée 78s -> 52s
                          hors sol 9/26 -> 0/26

  cube     0.875 -> 1.000     pave     0.833 -> 1.000     cylindre 0.750 -> 1.000
  anneau   0.838 -> 0.947     croix/tour 1.000 (inchangé) pyramide 0.330 -> 0.384
  escalier 0.139 -> 0.149 (pire cas du jeu)              sphere   0.527 -> 0.342

Opérations réellement employées : box, cylinder, layer, carve, mirror. cylinder est
adopté, sphere NON.

Le recul de prim_sphere n'est PAS une régression : sur 3 tentatives, une échoue (déjà
avant), une reproduit exactement l'ancien résultat (0.527 avec layer) et une bascule sur
box+carve et tombe à 0.157. La moyenne repose sur deux générations exploitables, dont
une qui joue à pile ou face sur la méthode. Chiffre non solide statistiquement.

Conséquence sur la métrique : un IoU de 1.0 sur prim_cylindre mesure désormais que le
modèle a choisi la bonne opération et les bons paramètres, plus qu'il a su tracer un
cercle. Ce n'est pas la même chose, et c'est voulu.

prim_escalier (0.149, pire cas du jeu) : DIAGNOSTIQUE. Le modele construit un escalier
GEOMETRIQUEMENT PARFAIT. Deux causes distinctes expliquent le score, et une seule est de
sa faute.

1. ERREUR DU MODELE — 3 plates par marche au lieu d'une. Il raisonne en briques
   (1 brique = 3 plates), alors que le prompt dit explicitement "la premiere marche fait
   1 plate de haut". Volume produit : 180 voxels contre 60 attendus, exactement 3x.
   C'est ENCORE le planificateur, pas le designer : les plans ecrivent z=0..2, z=3..5,
   ... et une boite de 15 plates ; le designer obeit fidelement. Meme schema que le
   decalage z.

2. ARTEFACT DE MESURE — l'escalier est tourne de 270 degres par rapport a la reference.
   Le prompt n'impose AUCUNE orientation. voxel_iou recadre sur le contenu mais ne
   tourne pas : il punissait une reponse correcte.

Decomposition mesuree, tentative 1 :
  tel quel                              0.200
  rotation autorisee                    0.333
  hauteur corrigee                      0.429
  hauteur corrigee + rotation autorisee 1.000   <- la forme etait exacte

D'ou src/primitives.voxel_iou_oriente() : meilleur IoU parmi les 8 orientations du plan
horizontal (le haut et le bas restent fixes, un modele a l'envers ne tiendrait pas
debout). Rapportee EN PLUS de voxel_iou, colonne iou_oriente du CSV — jamais a sa place,
remplacer la metrique principale casserait la comparabilite de tout l'historique.

DEUX CORRECTIONS DE PROMPT ONT ETE ESSAYEES ET MESUREES. Les deux sont SANS EFFET, ne
pas les retenter :
  a) consigne explicite "quand la description donne une hauteur en plates, utilise
     exactement ce nombre, ne l'arrondis pas en briques" -> 0.200 (bruit). Verifie que
     la consigne partait bien dans la requete : elle y etait, le modele l'a ignoree 3
     fois sur 3.
  b) reformulation faisant de la plate l'unite par defaut et de la brique une
     information secondaire -> 0.198. Plan produit mot pour mot identique.
Le prompt a ete restaure a l'identique : une ligne qui ne fait rien coute des tokens et
laisse croire que le cas est traite. Temoin prim_tour : 1.000 dans les deux essais, donc
ces formulations ne degradaient rien ailleurs non plus.

Ce qui reste a tenter, si on y revient : agir cote jeu de test (le prompt figé dit
"1 plate", le modele comprend "1 niveau") ou accepter que ce cas mesure la capacite du
modele a respecter une unite, ce qu'il ne fait pas ici.

La sphère est ronde dans la grille mais APLATIE à l'écran (~2.5x) : `diameter` vaut
autant de tenons en X/Y que de plates en Z, or un tenon fait 20 LDU et une plate 8.
src/primitives.py assume ce choix et les références d'IoU l'utilisent ; changer l'un
sans l'autre rendrait la métrique fausse.

La taille demandee (--taille / bbox_max) est une garantie par le schema, pas une
consigne : build_schema() resserre bbox ET coordonnees. Mesure, 3 generations par
taille : 0 depassement sur 9. MAIS la borne est un PLAFOND, pas une cible — `petite`
sature sa borne (16x16x20 aux 3 essais), `grande` ne la touche jamais (16x16x24,
24x24x24, 16x16x12) parce que le planificateur choisit ce qu'il juge raisonnable.
Choisir `grande` n'a donc aucun effet observable aujourd'hui. Et le nombre de pieces
ne suit pas la taille (petite 50-143, moyenne 65-241, grande 137-189) : il depend du
creux du modele. La garantie porte sur la boite englobante, pas sur le compte.

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
faire passer le linter avant de livrer (python -m ruff check .) ;
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
  similarité sémantique (CLIP/VLM). Sur un objet non primitif, aucune métrique ne dit
  aujourd'hui si le résultat ressemble au prompt. C'est le dernier grand trou : on sait
  mesurer la validité géométrique et les formes primitives, rien entre les deux.
  LM Studio sert plusieurs modèles à la fois (champ `model` de la requête, llm_client.py
  l'envoie déjà) : un VLM qui note render_iso.png est la bonne forme. À calibrer d'abord
  sur le jeu primitives, où l'IoU donne la vérité terrain — si la note du VLM ne corrèle
  pas avec l'IoU connu, le juge est inutilisable et ça se sait sans dépenser une
  génération.
  Ne PAS mettre un modèle texte à vérifier la géométrie : src/validator.py le fait déjà,
  exactement et gratuitement.

Priorité 2 :
  sphere n'est pas adopté, le modèle garde layer (IoU 0.527). Cause identifiée : le
  prompt figé du jeu de test dicte la méthode — "chaque couche horizontale est un
  disque, le plus large au milieu, rétrécissant vers le haut et vers le bas". Le modèle
  obéit au prompt. Celui du cylindre ne prescrit rien, d'où l'adoption immédiate
  (0.272 -> 0.750). Ajouter un exemple few-shot avec sphere a été essayé et MESURÉ :
  sans effet sur l'adoption, IoU dégradé (0.526 -> 0.258), exemple retiré.
  Décision à prendre : reformuler ce prompt du jeu figé (ce qui casse la comparabilité
  de tout l'historique) ou accepter que ce cas mesure l'obéissance au prompt.

  prim_escalier : diagnostiqué (voir plus haut). La forme est exacte ; le score
  vient d'une confusion plate/brique du planificateur et d'une rotation que la
  métrique ne tolère pas. Deux correctifs de prompt essayés, mesurés, sans effet.

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

python -m pytest tests/ -q                      tests (169)
python -m ruff check .                          linter (pip install -r requirements-dev.txt)
python tools/check_env.py                       diagnostic environnement
python lego.py "<prompt>"                       UN modèle complet, en une commande
python lego.py "<prompt>" --taille petite --iterations 3
python pipeline.py                              lot depuis prompts.json
python -m src.designer "<prompt>" <dossier>     design seul, sans rendu ni notice
python -m tools.build_from_design <design.json> design -> .ldr, sans LLM
python -m tools.render_batch --glob "<motif>" --assemble
python -m eval.run_eval --version v1 --n 3
python -m eval.run_eval --version v1prim --n 3 --testset eval/primitives_testset.json
python -m eval.run_eval --compare <csv_a> <csv_b>

Vérification syntaxe :
Get-ChildItem -Recurse -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }
