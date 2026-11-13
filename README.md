# LEGO — modèles en vraies pièces, générés depuis une phrase

Décrire un objet en français et obtenir un modèle LEGO fait de **vraies pièces** du catalogue LDraw : une liste d'achat, une notice de montage et des rendus Blender. Le modèle de langage tourne en local, avec LM Studio.

## Principe

Le LLM ne produit jamais la géométrie finale. Il écrit un plan en texte, puis un programme d'opérations volumétriques (un DSL). Du code Python déterministe transforme ce programme en briques réelles, vérifie la géométrie, puis produit la nomenclature, la notice et les rendus.

## Résultats mesurés

- Formes simples (cube, pavé, cylindre) : IoU de 0,82 à **1,00** après correction d'un décalage d'indice en z.
- Générations posées hors sol : de 9/26 à **0/26**.
- Créatures : structures d'un seul tenant, de 0/15 à **12/14** (v0 → v1).

Le protocole et les chiffres complets sont dans [docs/mesures.md](docs/mesures.md).

## Limites actuelles

Avec un modèle local, les résultats restent inégaux dès que la demande se complique. Les pistes : un modèle distant via clé API ou serveur MCP, ou un fine-tuning du modèle local.

## Installation

Prérequis : Python 3.11+, LM Studio (serveur sur `http://localhost:1234`), Blender, la bibliothèque LDraw et l'add-on ImportLDraw.

```powershell
pip install -r requirements.txt
python tools/check_env.py
```

`check_env.py` vérifie l'environnement en lecture seule.

## Utilisation

```powershell
# un modèle complet : plan, design, .ldr, rendus, nomenclature, notice
python lego.py "Un phare blanc avec une lanterne rouge au sommet."

# en bornant la taille et en proposant trois variantes
python lego.py "Une petite maison" --taille petite --iterations 3

# un lot depuis prompts.json
python pipeline.py
```

Chaque génération est écrite dans un dossier horodaté dans `outputs/`.

## Tests

```powershell
pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest tests/ -q    # 341 tests
```

## Documentation

- [docs/guide-complet.md](docs/guide-complet.md) : pipeline, pile web (base, API, interface), DSL, notice, évaluation.
- [docs/flux_donnees.md](docs/flux_donnees.md) : types exacts et exemples réels à chaque étape.
- [docs/mesures.md](docs/mesures.md) : toutes les mesures figées, avec leur protocole.

## Licence

Code sous licence MIT (voir [LICENSE](LICENSE)). La bibliothèque LDraw n'est pas incluse : elle reste sous sa propre licence.
