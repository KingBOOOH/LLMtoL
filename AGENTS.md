Objectif du projet

Ce projet génère automatiquement des structures LEGO en 3D à partir d’un prompt utilisateur.

La pipeline cible est :

Prompt utilisateur
  -> LM Studio
  -> réponse brute du LLM
  -> extraction JSON
  -> validation JSON
  -> génération Blender
  -> rendus multi-vues
  -> assemblage final

Le LLM ne doit pas générer directement du code Blender.
Il doit uniquement produire une structure JSON décrivant les briques LEGO.

Architecture attendue

Fichiers principaux possibles :

Prompt.py
Appel au modèle local via LM Studio.
Envoi du prompt.
Récupération de la réponse brute du LLM.
pipeline.py
Point d’entrée principal.
Orchestre toute la génération.
Appelle LM Studio.
Extrait le JSON.
Valide le JSON.
Lance Blender.
Gère les prompts multiples et les itérations.
blender_script.py
Script exécuté par Blender.
Lit le JSON généré.
Crée les briques/cubes dans la scène.
Applique les positions, tailles et couleurs.
Configure les caméras.
Lance les rendus.
img_output.py
Assemble les rendus générés.
Produit une image finale en grille 2x2.
Ajoute des séparateurs et des labels.

Codex doit confirmer les fichiers réellement présents avant de modifier quoi que ce soit.

Pipeline fonctionnelle cible

La pipeline complète doit suivre cette logique :

1. Lire un prompt utilisateur ou un fichier de prompts
2. Envoyer le prompt à LM Studio
3. Sauvegarder la réponse brute du LLM
4. Extraire un JSON propre depuis la réponse
5. Valider le JSON
6. Sauvegarder le JSON extrait
7. Lancer Blender en arrière-plan
8. Générer la scène LEGO
9. Produire plusieurs rendus
10. Assembler les rendus en une image finale
11. Sauvegarder tous les outputs dans un dossier clair
Format JSON cible

Format minimal attendu :

{
  "bricks": [
    {
      "type": "cube",
      "position": [0, 0, 0],
      "size": [1, 1, 1],
      "color": "red"
    }
  ]
}

Chaque brique doit contenir au minimum :

type
position
size
color

Règles de validation :

bricks doit exister.
bricks doit être une liste.
bricks ne doit pas être vide.
Chaque brique doit avoir type, position, size, color.
position doit être une liste de 3 nombres.
size doit être une liste de 3 nombres strictement positifs.
color doit être une chaîne de caractères.
Le JSON invalide doit être refusé avant d’appeler Blender.
Problèmes connus à surveiller

Le modèle peut retourner :

du texte avant ou après le JSON ;
un bloc Markdown ```json ;
plusieurs objets JSON ;
un JSON incomplet ;
un JSON invalide ;
une liste de briques vide ;
des positions incohérentes ;
des tailles invalides ;
une structure impossible à rendre proprement.

Codex doit toujours chercher à corriger la cause racine, pas seulement masquer l’erreur.

Règles de développement

Codex doit :

inspecter le code existant avant toute modification ;
identifier le point d’entrée principal ;
identifier les commandes de lancement ;
identifier les commandes de test ou de vérification ;
proposer un plan avant une modification importante ;
modifier par petites étapes ;
relancer une commande de vérification après chaque correction ;
expliquer clairement les erreurs rencontrées ;
préserver les fonctionnalités existantes ;
éviter les refontes inutiles.

Codex ne doit pas :

repartir de zéro si une structure existe déjà ;
supprimer un fichier sans validation ;
changer le format JSON sans validation ;
modifier lourdement pipeline.py sans validation ;
hardcoder un chemin local partout ;
ignorer les erreurs silencieusement ;
remplacer une erreur claire par un try/except trop large ;
casser la compatibilité Windows.
Priorités techniques

Priorité 1 :

extraction JSON robuste ;
validation JSON avant Blender ;
messages d’erreur lisibles ;
sauvegarde de la réponse brute et du JSON extrait.

Priorité 2 :

gestion correcte des itérations ;
gestion de plusieurs prompts ;
organisation propre des outputs.

Priorité 3 :

stabilisation du rendu Blender ;
génération multi-vues ;
assemblage final des images.
Organisation des outputs souhaitée

Structure recommandée :

outputs/
  prompt_001/
    iteration_001/
      prompt.txt
      raw_response.txt
      model.json
      render_top.png
      render_bottom.png
      render_left.png
      render_right.png
      final.png
      log.txt
    iteration_002/
      ...
  prompt_002/
    iteration_001/
      ...

Les outputs ne doivent pas s’écraser entre deux générations.

Environnement

Le projet tourne principalement sous Windows.

Blender est installé localement.

Chemin Blender probable :

C:\Program Files\Blender Foundation\Blender 5.1\blender.exe

LM Studio tourne localement.

Codex doit vérifier les chemins réellement utilisés dans le projet avant de les modifier.

Commandes utiles possibles

Codex doit d’abord vérifier les commandes existantes dans le projet.

Commandes possibles :

python -m py_compile Prompt.py pipeline.py blender_script.py img_output.py
Get-ChildItem -Recurse -Filter *.py | ForEach-Object { python -m py_compile $_.FullName }
python pipeline.py

Commande Blender possible :

& "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --python blender_script.py