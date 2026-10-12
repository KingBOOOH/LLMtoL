"""Configuration portable hote / conteneur.

Le meme code doit tourner sur l'hote Windows et dans le conteneur de generation, ou
Blender est sous /opt, la bibliotheque LDraw sous /opt/ldraw, et LM Studio reste sur
l'hote, joint par host.docker.internal. Ces tests verifient le mecanisme de surcharge
par variables d'environnement et le contenu du Dockerfile, sans avoir besoin de Docker.

Ils protegent surtout une chose : que le MODE HOTE reste intact. Une surcharge mal
ecrite — un chemin Linux devenu le defaut, par exemple — ne se verrait pas en
conteneur, seulement sur le poste de l'utilisateur.
"""

import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RACINE = Path(__file__).resolve().parent.parent
DOCKERFILE = RACINE / "Dockerfile.agent"
COMPOSE = RACINE / "docker-compose.yml"


def _config_avec(env):
    """Valeurs de config.py sous un environnement donne, environnement remis apres.

    Retourne une COPIE des valeurs et non le module : `importlib.reload` remplace le
    contenu du module en place, donc rendre le module ferait lire a l'appelant l'etat
    d'apres la remise en ordre, pas celui qu'il voulait mesurer.
    """
    import config

    anciens = {k: os.environ.get(k) for k in env}
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        recharge = importlib.reload(config)
        return SimpleNamespace(
            BLENDER_EXE=recharge.BLENDER_EXE,
            LM_STUDIO_URL=recharge.LM_STUDIO_URL,
            LM_STUDIO_MODEL=recharge.LM_STUDIO_MODEL,
            NAVIGATEURS_PDF=list(recharge.NAVIGATEURS_PDF),
        )
    finally:
        for k, v in anciens.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(config)


# --- surcharge par l'environnement ------------------------------------------------

def test_les_defauts_restent_ceux_de_l_hote():
    """Le piege de cette US : faire d'un chemin de conteneur le defaut. Ca marcherait
    en conteneur et casserait le poste de l'utilisateur."""
    c = _config_avec({"LEGO_BLENDER_EXE": None, "LM_STUDIO_URL": None,
                      "LM_STUDIO_MODEL": None})
    assert c.BLENDER_EXE.endswith("blender.exe")
    assert "localhost" in c.LM_STUDIO_URL
    assert c.LM_STUDIO_MODEL == "qwen/qwen3-coder-30b"


def test_blender_et_lm_studio_sont_surchargeables():
    c = _config_avec({"LEGO_BLENDER_EXE": "/usr/local/bin/blender",
                      "LM_STUDIO_URL": "http://host.docker.internal:1234/v1/chat/completions"})
    assert c.BLENDER_EXE == "/usr/local/bin/blender"
    assert "host.docker.internal" in c.LM_STUDIO_URL


def test_le_navigateur_pdf_surcharge_passe_en_tete_sans_effacer_les_autres():
    """En tete et non a la place : si le chemin fourni est faux, les secours doivent
    encore jouer plutot que de faire echouer la notice."""
    c = _config_avec({"LEGO_NAVIGATEUR_PDF": "/usr/bin/chromium"})
    assert c.NAVIGATEURS_PDF[0] == "/usr/bin/chromium"
    assert len(c.NAVIGATEURS_PDF) > 1


def test_la_bibliotheque_ldraw_est_cherchee_dans_la_variable_d_abord():
    from src.catalog import LDRAW_CANDIDATE_DIRS

    assert LDRAW_CANDIDATE_DIRS[0] == "$LEGO_LDRAW_DIR"
    assert "/opt/ldraw" in LDRAW_CANDIDATE_DIRS


def test_une_variable_absente_ne_devient_pas_un_nom_de_dossier(tmp_path):
    """expandvars laisse "$NOM" tel quel quand la variable n'existe pas. Sans garde-fou
    on irait chercher un dossier reellement nomme "$LEGO_LDRAW_DIR", et la recherche
    echouerait de facon incomprehensible."""
    from src import catalog

    ancien = os.environ.pop("LEGO_LDRAW_DIR", None)
    try:
        # Une vraie bibliotheque minimale, pour que la recherche ait quelque chose a
        # trouver APRES le candidat non resolu.
        (tmp_path / "LDConfig.ldr").write_text("0 fake\n", encoding="utf-8")
        sauve = catalog.LDRAW_CANDIDATE_DIRS
        catalog.LDRAW_CANDIDATE_DIRS = ["$LEGO_LDRAW_DIR", str(tmp_path)]
        try:
            assert catalog._find_ldraw_library() == str(tmp_path)
        finally:
            catalog.LDRAW_CANDIDATE_DIRS = sauve
    finally:
        if ancien is not None:
            os.environ["LEGO_LDRAW_DIR"] = ancien


# --- contenu du Dockerfile --------------------------------------------------------

def test_les_versions_sont_figees():
    """Tout l'interet de l'image : le rendu d'aujourd'hui doit etre le meme dans six
    mois. Une version flottante (latest, une branche) le romprait sans bruit."""
    texte = DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG BLENDER_VERSION=5.1.1" in texte
    assert "ARG IMPORTLDRAW_VERSION=1.2.3" in texte
    assert ":latest" not in texte


def test_la_version_de_blender_est_celle_de_l_hote():
    """Deux versions differentes donneraient deux rendus differents selon le chemin
    emprunte, ce qui viderait la reproductibilite de son sens."""
    import config

    texte = DOCKERFILE.read_text(encoding="utf-8")
    serie = "5.1"
    assert f"ARG BLENDER_SERIE={serie}" in texte
    assert serie in config.BLENDER_EXE, \
        "le defaut de l'hote ne pointe plus vers la meme serie de Blender"


def test_l_attribution_ldraw_est_dans_l_image():
    """La bibliotheque est sous CC BY : la redistribution est permise, l'attribution
    est OBLIGATOIRE. La verification de licence etait le prealable de cette US."""
    texte = DOCKERFILE.read_text(encoding="utf-8")
    assert "/opt/ldraw/ATTRIBUTION" in texte
    assert "Creative Commons Attribution" in texte
    assert "ldraw.org" in texte.lower()


def test_le_controle_lm_studio_n_est_pas_contourne():
    """--skip-env-check dans le CMD transformerait chaque job en dechet le jour ou le
    piege du Structured Output est arme."""
    texte = DOCKERFILE.read_text(encoding="utf-8")
    ligne_cmd = next(ligne for ligne in texte.splitlines() if ligne.startswith("CMD"))
    assert "--skip-env-check" not in ligne_cmd
    assert "--une-fois" not in ligne_cmd


def test_l_image_porte_les_chemins_du_conteneur():
    texte = DOCKERFILE.read_text(encoding="utf-8")
    for var in ("LEGO_BLENDER_EXE=", "LEGO_LDRAW_DIR=/opt/ldraw",
                "LEGO_NAVIGATEUR_PDF="):
        assert var in texte, f"{var} absent du Dockerfile"


# --- compose ---------------------------------------------------------------------

def test_l_agent_est_derriere_un_profil():
    """Sans profil, `docker compose up -d` demarrerait DEUX agents des que
    l'utilisateur garde le sien sur l'hote : ils se disputeraient LM Studio."""
    texte = COMPOSE.read_text(encoding="utf-8")
    bloc = texte[texte.index("  agent:"):]
    assert 'profiles: ["conteneur"]' in bloc


def test_l_agent_joint_lm_studio_sur_l_hote():
    texte = COMPOSE.read_text(encoding="utf-8")
    bloc = texte[texte.index("  agent:"):]
    assert "host.docker.internal" in bloc
    assert "host-gateway" in bloc, "sans extra_hosts, le service ne marche pas hors Docker Desktop"


def test_l_agent_ecrit_dans_outputs_contrairement_a_l_api():
    """L'API monte outputs en lecture seule et c'est voulu ; l'agent doit y ecrire."""
    texte = COMPOSE.read_text(encoding="utf-8")
    api = texte[texte.index("  api:"):texte.index("  agent:")]
    agent = texte[texte.index("  agent:"):]
    assert "./outputs:/outputs:ro" in api
    assert "./outputs:/app/outputs" in agent
    assert "./outputs:/app/outputs:ro" not in agent


def test_le_contexte_de_build_exclut_les_sorties():
    """Sans .dockerignore, chaque build envoie des centaines de Mo de rendus au demon
    avant meme de commencer, et aucun COPY ne les reclame."""
    ignore = (RACINE / ".dockerignore").read_text(encoding="utf-8")
    for motif in ("outputs/", "eval/results/", ".git/", ".env"):
        assert motif in ignore, f"{motif} devrait etre exclu du contexte"
