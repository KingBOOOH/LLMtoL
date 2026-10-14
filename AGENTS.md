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

Les CHIFFRES de toutes les mesures du projet sont dans docs/mesures.md, pas ici : ce
fichier est chargé à chaque session, et on consulte un tableau de mesures une fois par
mois. Ici ne restent que les règles et les pièges — ce qu'on peut enfreindre sans le
savoir.

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
  Prend les jobs déposés en base par l'API web et les traite (Blender et LM Studio).
  Tourne sur l'hôte, ou dans le conteneur de génération (profil "conteneur").
  Reprend un job abandonné après une coupure mémoire. Sert aussi de chemin de
  RÉGÉNÉRATION : l'API remet un job terminé en `en_finition` et toute la chaîne en aval
  du design est rejouée, sans aucun appel LLM.

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
src/catalog.py       26 pièces + palette + correspondance BrickLink, toutes vérifiées
src/wanted_list.py   nomenclature -> liste d'achat BrickLink (XML)
src/validator.py     contrôles géométriques -> report.json
src/primitives.py    formes de référence analytiques + métrique IoU
src/blender_script.py  model.ldr -> 4 rendus (s'exécute DANS Blender)
src/img_output.py    4 rendus -> planche final.png

Pile web :

server/app.py        API FastAPI + service de l'interface. NE GÉNÈRE RIEN.
server/regles.py     règles métier, sans FastAPI ni base (donc testables sur l'hôte)
server/db.py         modèles SQLAlchemy, PARTAGÉS entre l'API et l'agent
server/static/       interface Vue 3 chargée par CDN, sans chaîne de build

Outils :

tools/check_env.py        diagnostic environnement, lecture seule
tools/build_from_design.py  design.json -> model.ldr, sans appel LLM
tools/render_batch.py     rend N modèles en un seul processus Blender
tools/recover_eval.py     reconstruit un CSV depuis les design.json survivants

Évaluation :

eval/run_eval.py             harnais, avec --resume et --compare
eval/testset.json            20 prompts sémantiques figés
eval/primitives_testset.json 11 formes primitives, avec référence calculable

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

La taille demandée (--taille / bbox_max) est une GARANTIE par le schéma, pas une
consigne : build_schema() resserre bbox ET coordonnées. Mais c'est un PLAFOND, pas une
cible, et choisir `grande` n'a aucun effet observable. Chiffres : docs/mesures.md.

Règles de validation (src/validator.py)

Bloquant :
  modèle vide (sinon "ne rien produire" est la stratégie gagnante triviale)
  collisions entre pièces
  structure disjointe (plus d'une composante connexe)

Avertissement, pénalisé au score :
  pièce flottante, dépassement du bbox déclaré, symétrie annoncée non vérifiée,
  taux de remplissage > 85 %, centre de masse non soutenu, joints alignés

"Pièce flottante" n'est PAS bloquant. Le contrôle se déclenche sur des constructions
parfaitement normales (les plaques centrales d'un toit plat au-dessus d'une pièce
creuse) ; utilisé comme barrière, il faisait échouer quasiment toute génération.

Pièges à connaître

LM STUDIO réinjecte silencieusement le system prompt et le Structured Output configurés
sur le modèle dans son interface, dès qu'une requête n'envoie pas son propre
response_format. Le réglage REVIENT à chaque rechargement du modèle. Symptôme : le modèle
répond la même structure JSON quel que soit le prompt, même sans rapport. Ce piège a coûté
du temps trois fois. Vérifier avant tout run long.
  Détail : le modèle qwen3-coder-30b garde un Structured Output hérité de la chaîne v0
  (llm.prediction.structured + llm.prediction.systemPrompt dans
  .lmstudio/.internal/user-concrete-model-default-config/qwen/qwen3-coder-30b.json). Ni
  un system prompt à nous ni un response_format {"type":"text"} ne le neutralisent —
  seul un json_schema explicite le fait. Le designer est donc immunisé, le planificateur
  NON. Se vide dans My Models > le modèle > vider System Prompt et Structured Output.
  Tant que ce n'est pas fait, eval/run_eval.py est inutilisable.
  VÉRIFIÉ, et scriptable : les deux réglages sont deux entrées de la liste
  `operation.fields` du JSON ci-dessus, repérées par leur clé. Les retirer de la liste
  (en gardant `llm.prediction.promptTemplate`, qui est le template de chat légitime de
  Qwen), décharger puis recharger le modèle, et `check_env.preflight_llm()` repasse au
  vert. Fait une fois : le réglage contenait bien le générateur de cubes de la chaîne v0
  supprimée. Sauvegarder le fichier avant (`.bak`) — c'est la config de LM Studio, pas
  la nôtre.

LE DÉFAUT VIENT SOUVENT DU PLANIFICATEUR, pas du designer. Deux fois sur deux : le
décalage z (son plan écrivait "z=1..8") et la confusion plate/brique de prim_escalier.
Le designer obéit fidèlement au plan qu'on lui donne. Corriger designer_system.txt n'a
rien changé dans les deux cas. Chiffres : docs/mesures.md.

NE JAMAIS COURT-CIRCUITER UNE ÉTAPE DE LA CHAÎNE pour mesurer l'effet d'un changement
sur cette chaîne. Une "mesure" du correctif z contournait le planificateur avec un plan
écrit à la main contenant "poser au sol (z=0)" : elle mesurait cette phrase.

prim_escalier est DIAGNOSTIQUÉ : la forme est exacte, le score vient d'une confusion
plate/brique et d'une rotation que la métrique ne tolère pas. DEUX correctifs de prompt
ont été essayés et mesurés, sans effet : NE PAS LES RETENTER. Voir docs/mesures.md.

LA GRILLE N'EST PAS ISOTROPE : un tenon fait 20 LDU de large, une plate 8 LDU de haut.
Une grille cubique est donc un objet écrasé 2,5x à l'écran. Constantes nommées dans
src/primitives.py (LDU_PAR_TENON, LDU_PAR_PLATE) — ne pas recopier le 2,5 ailleurs.
  LA SPHÈRE EST CORRIGÉE et ne doit pas être "re-simplifiée". Son `diameter` est un
  diamètre RÉEL en tenons : elle occupe hauteur_sphere(d) ~= 2,5*d plates en Z, et
  l'appartenance se teste EN LDU. Une sphère de 7 tenons fait donc 18 plates de haut.
  La référence d'IoU et le rastériseur du DSL sont la MÊME fonction : les changer là
  les change ensemble, et c'est tout l'intérêt. build_schema borne `diameter` par la
  hauteur disponible, sinon le bbox cesserait d'être une garantie.
  LE RESTE EST TOUJOURS EN UNITÉS DE GRILLE, et c'est voulu : cube, box et cylinder
  reçoivent leurs dimensions axe par axe, donc l'utilisateur décide. Un "cube" 8x8x8
  est écrasé à l'écran (160x64 LDU) et un cylindre 9 tenons x 4 plates est une galette
  (180x32) — mais les prompts disent bien "8 plates de haut", il n'y a pas d'ambiguïté
  à lever, seulement un nom trompeur. La sphère était différente : isotrope PAR
  DÉFINITION, elle n'a qu'un paramètre, qui ne peut pas valoir deux longueurs.
  DONC ne jamais lire un IoU élevé comme "le rendu ressemble à l'objet nommé" : il dit
  "le rendu respecte la spécification". Regarder le render_iso.png avant de conclure.
  C'est un juge visuel qui a levé le lièvre, en notant 0/10 des sphères à IoU 1,000.
  ATTENTION À LA RÉSOLUTION avant de crier au bug : à 7 tenons de diamètre une sphère
  en briques ressemble à un cube aux coins coupés, et c'est normal. À 16 tenons (40
  plates, 1120 pièces) elle est franchement ronde. Vérifié sur rendu.

UNE PENTE NE REMPLIT PAS SA BOÎTE ENGLOBANTE. w et d décrivent sa seule EMPREINTE
HAUTE ; la rampe déborde d'une rangée qui doit être vide, dans la grille, et non déjà
réservée (deux pentes face à face se percuteraient dans le creux sans que le validateur
le voie — il ne connaît que l'empreinte haute). Géométrie vérifiée sur les sommets de
3040b.dat : l'extension en Z n'est PAS centrée sur l'origine, la pièce est centrée sur
son tenon HAUT. Avec w/d = empreinte haute la formule de centrage de ldraw_writer
s'applique sans changement ; avec l'emprise totale elle décalerait tout d'un demi-tenon.
Quatre orientations (champ `retournee` en plus de `rotated`), sens de descente RELEVÉ
SUR UN RENDU : identité -> -y, 90 -> -x, 180 -> +y, 270 -> +x.

verify_catalog() ne testait que la PRÉSENCE du fichier .dat, et laissait donc passer
3023 après que la mise à jour LDraw 2024-07 a renommé la pièce en 3023b (3023.dat n'est
plus qu'un stub "0 ~Moved to 3023b"). Il rejette désormais ces stubs. Même piège sur
3040 -> 3040b.

NE JAMAIS DÉDUIRE LE NUMÉRO BRICKLINK DU NUMÉRO LDRAW. 4 pièces sur 26 diffèrent, et les
couleurs utilisent les mêmes nombres pour des couleurs différentes. Il n'existe aucun
fichier de correspondance officiel. verify_bricklink() échoue si une pièce du catalogue
n'a pas de correspondance explicite : élargir le catalogue oblige à traiter la question.
Chiffres : docs/mesures.md.
  Format de la wanted list : vérifié sur help.asp?helpID=207. Le fichier ne doit PAS
  porter de déclaration <?xml ?>, et seuls ITEMTYPE et ITEMID sont obligatoires. La page
  Want > Upload a DEUX options : le sélecteur de fichier n'accepte que .bsx .ldr .lxf
  .io (donc model.ldr), le XML ne passe que par la zone de COLLAGE.

EEVEE rend les pièces en aplat uniforme (groupes de nœuds de l'add-on écrits pour Blender
2.81). Utiliser Cycles, ou Workbench pour itérer vite.

BLENDER 5.1 ne charge PAS les add-ons hérités depuis son dossier d'installation. Posé
sous /opt/blender/5.1/scripts/addons, l'add-on figure pourtant dans
bpy.utils.script_paths(subdir='addons') et addon_enable répond quand même "No module
named". Il faut bpy.utils.user_resource('SCRIPTS').

L'ADD-ON CHERCHAIT LA BIBLIOTHÈQUE dans ses PRÉFÉRENCES ENREGISTRÉES : correct sur un
poste configuré à la main, "Could not find LDraw Part Library" partout ailleurs. Le
chemin résolu par src.catalog est transmis à Blender par LEGO_LDRAW_DIR et passé
explicitement en ldrawPath. Vaut aussi pour l'hôte avec un profil Blender neuf.

CHROMIUM REFUSE DE DÉMARRER EN ROOT sans --no-sandbox, et l'image de génération tourne
en root. L'option n'est ajoutée QUE dans ce cas : la désactiver sur un poste de travail
affaiblirait réellement le navigateur pour un gain nul. --disable-dev-shm-usage est là en
précaution, pas comme correctif — la première hypothèse désignait /dev/shm, et elle était
fausse. Au passage : subprocess.TimeoutExpired n'est ni RuntimeError ni OSError, sans
rattrapage elle échappait au except de agent.finir et faisait échouer tout le job pour un
PDF manquant. Et la sortie du navigateur était capturée puis JETÉE, d'où une erreur qui
ne disait jamais pourquoi.

LES RUNS LONGS EN ARRIÈRE-PLAN se font tuer par la pression mémoire (LM Studio retient
~18 Go). Lancer en processus détaché via Start-Process, et utiliser --resume.
  CAUSE DES ~18 Go, identifiée : le pré-contrôle de l'agent (agent.main ->
  check_env.preflight_llm) envoie une VRAIE requête de complétion à LM_STUDIO_MODEL, et
  le JIT loading de LM Studio charge donc le 30B pour répondre à un test de
  disponibilité. Pire, l'agent conteneur a `restart: unless-stopped` : son refus
  DÉLIBÉRÉ de démarrer est vu par Docker comme un plantage, d'où un rechargement de
  18,63 Go toutes les quelques secondes. Avant une passe qui n'a pas besoin du
  générateur (juge visuel, rendu, calibration) : `docker stop v0_0_5-agent-1` puis
  `lms unload --all`. Vérifier avec `lms ps` que le 30B ne revient pas — s'il revient,
  quelque chose l'appelle.

SCHÉMA DE LA BASE : db/init.sql n'est joué qu'à la CRÉATION du volume Postgres. Un ajout
de colonne sur une base en service passe par server.db.assurer_schema(), idempotent,
appelé au démarrage de l'API et de l'agent. Pas d'Alembic. jobs.origine_id relie les
essais successifs d'un même prompt et pointe TOUJOURS la racine, jamais l'essai précédent.

CONFIG PORTABLE : config.py lit toutes ses valeurs dans l'environnement, les valeurs
écrites restant les défauts de l'hôte Windows (LEGO_BLENDER_EXE, LM_STUDIO_URL,
LM_STUDIO_MODEL, LEGO_NAVIGATEUR_PDF). src.catalog cherche la bibliothèque dans
$LEGO_LDRAW_DIR d'abord. Ne JAMAIS faire d'un chemin de conteneur un défaut : ça
marcherait en conteneur et casserait le poste de l'utilisateur.

AVANCEMENT AFFICHÉ : l'agent écrit son étape en texte libre dans jobs.etape ; la
traduction en identifiants stables se fait côté serveur (server/regles.PHASES_PAR_ETAPE),
et l'interface n'interprète rien. Un test relit les chaînes écrites dans agent.py et
server/app.py et exige qu'elles soient toutes connues.

L'INTERFACE N'A QU'UN PANNEAU, qui suit ce sur quoi on a cliqué, sinon ce qui travaille.
L'ordre précédent (ce qui attend un choix d'abord) avait un défaut vicieux : une demande
en attente de choix masquait en permanence celle que l'agent traitait.

agent.indices_a_produire rend produire_variantes REPRENABLE : seules les propositions
sans design_json sont rejouées, en réutilisant leur indice (leur ligne est effacée avant,
sinon UNIQUE(job_id, indice) sauterait). Après cette suppression il FAUT
session.refresh(job) : la session a expire_on_commit=False, donc job.variantes garderait
les lignes supprimées et la première écriture lèverait "Instance has been deleted".

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
élargir le catalogue sans vérifier chaque pièce contre la bibliothèque LDraw, sans
  relever ses dimensions empiriquement, et sans mesurer l'effet sur les designs archivés ;
corriger un mauvais alignement en allongeant le system prompt sans mesurer d'abord ;
supprimer un fichier sans validation ;
ignorer les erreurs silencieusement ;
remplacer une erreur claire par un try/except trop large ;
casser la compatibilité Windows (attention à l'encodage : sortie redirigée = cp1252).

Priorités techniques actuelles

Priorité 1 :
  similarité sémantique. Sur un objet non primitif, aucune métrique ne dit aujourd'hui si
  le résultat ressemble au prompt. C'est le dernier grand trou : on sait mesurer la
  validité géométrique et les formes primitives, rien entre les deux. TOUJOURS OUVERT.
  UN VLM QUI NOTE DE 0 À 10 A ÉTÉ CONSTRUIT, CALIBRÉ ET REFUSÉ (US V2-01). Ne pas le
  refaire. Corrélation de rang de 0,41 contre l'IoU connu sur 91 rendus, barrière annoncée
  avant la mesure à 0,55. Cause diagnostiquée en regardant les rendus : le juge CHANGE DE
  CRITÈRE — sur une boîte il vérifie les dimensions du prompt et valide, sur une courbe il
  juge le nom et refuse. Trois barèmes essayés, tous entre 0,38 et 0,43 : le barème n'est
  pas le levier, et un quatrième reviendrait à optimiser un prompt contre le jeu de test.
  Le code reste, désactivé par défaut (LEGO_VLM_MODEL vide) : src/juge_vlm.py,
  tools/calibrer_juge.py. Chiffres et images : docs/mesures.md, README.
  Deux pistes restent, aucune essayée :
    - CLIP (plonger image et prompt, cosinus) : coûte torch, ~2,5 Go, pour un projet qui a
      quatre dépendances. À peser.
    - faire DÉCRIRE l'image par le VLM, puis comparer la description au prompt par une
      similarité de texte. Décrire est ce qu'un VLM fait bien ; noter est ce qu'il fait
      mal, c'est mesuré. Plus de barème à régler, et un score continu.
  Ne PAS mettre un modèle texte à vérifier la géométrie : src/validator.py le fait déjà,
  exactement et gratuitement.

Priorité 2 : RÉSOLUE, et la décision est prise.
  sphere n'était pas adopté (0 fois sur 7) et prim_sphere plafonnait à 0,365. CAUSE
  DÉMONTRÉE, à variable unique : la phrase du prompt figé qui dicte la MÉTHODE ("chaque
  couche horizontale est un disque, le plus large au milieu, retrecissant..."). En la
  retirant, et en ne changeant rien d'autre : adoption 3/3, IoU 1,000, design d'UNE ligne,
  zéro réparation. Ce n'est pas l'unité en Z manquante — ça, c'est un autre défaut, réel
  et corrigé au passage, mais qui ne change pas l'adoption (0/3).
  Ne PAS retenter : un exemple few-shot avec sphere (mesuré, sans effet, IoU dégradé) ni
  un allongement de planner_system.txt (la consigne d'unité y est DÉJÀ deux fois, dont une
  en gabarit de sortie — le modèle recopie l'unité du prompt utilisateur par-dessus).
  Décision appliquée : le cas figé prim_sphere reste INTACT (l'historique vaut plus que
  son score), et un 10e cas prim_sphere_libre a été ajouté à côté avec le prompt mesuré.
  Le jeu a donc deux cas de sphère qui mesurent deux choses : prim_sphere mesure
  l'OBÉISSANCE à une méthode imposée, prim_sphere_libre mesure la construction d'une
  sphère. Ne pas confondre les deux en lisant une moyenne.
  CONFIRMÉ AUSSI SUR LE CYLINDRE, et c'est plus général qu'une dictée de méthode. Son
  prompt se contente de DÉCRIRE la forme ("la section horizontale est un disque") — pas
  de recette, pas d'ordre de montage. Ça suffit : 6 adoptions sur 9, IoU 0,674. En
  retirant cette seule clause : 3/3 et 1,000. Au passage, l'erreur de hauteur disparaît
  elle aussi (trois tentatives figées posaient z=0..4, soit 5 plates là où le plan disait
  4) — en appelant l'op, le modèle n'a plus de bornes à calculer.
  RÈGLE À EN TIRER pour tout prompt de jeu de test visant une forme que le DSL sait
  rastériser : donner le NOM et les DIMENSIONS, et rien de plus. Décrire la géométrie,
  même sans prescrire de méthode, pousse le modèle à la construire à la main — et il la
  construit moins bien que l'op, voire moins bien qu'une boîte posée bêtement.

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
        wanted_list.xml         liste d'achat BrickLink
        planner_request.json    payload exact envoyé
        planner_raw.txt         réponse brute
        planner_meta.json       tokens, durée, température, seed, tentatives
        designer_*.json/.txt    idem pour l'étape B (+ _repair1, _repair2)
        render_{top,front,right,iso}.png
        final.png               planche 2x2
        log.txt                 journal de l'itération
        error.txt               uniquement en cas d'échec

Deux exécutions successives ne s'écrasent jamais. La pile web range ses sorties dans
outputs/job_<uuid>/variante_NN/ selon la même convention.

Environnement

Windows. Blender installé localement, LM Studio en local. Chemins centralisés dans
config.py, tous surchargeables par l'environnement. La bibliothèque LDraw est cherchée
automatiquement (src/catalog.py), y compris un sous-dossier ldraw/ imbriqué et un
Documents/ redirigé par OneDrive.

La génération peut aussi tourner en conteneur (Dockerfile.agent, profil compose
"conteneur") : l'image embarque Blender, l'add-on et la bibliothèque LDraw à des versions
figées. LM Studio reste sur l'hôte, joint par host.docker.internal.

Prérequis vérifiables d'un coup : python tools/check_env.py

Commandes utiles

python -m pytest tests/ -q                      tests (281)
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

docker compose up -d                            base, API, interface
docker compose --profile conteneur up -d        + la génération (Blender compris)

Vérification syntaxe :
Get-ChildItem -Recurse -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }
