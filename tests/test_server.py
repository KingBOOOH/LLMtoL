"""Regles de relance / regeneration, et coherence entre l'interface et l'API.

Ces regles decident si un essai peut etre relance (N appels au modele) ou simplement
regenere (aucun). Se tromper coute soit plusieurs minutes de generation pour rien,
soit deux traitements qui se disputent le meme dossier de sortie.

Elles sont testees via server/regles.py, qui n'importe ni FastAPI ni la base :
fastapi n'est installe que dans le conteneur, donc un test qui importerait
server/app.py ne tournerait nulle part sur l'hote.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.regles import (
    EN_VOL,
    refus_de_regeneration,
    refus_de_relance,
    verifier_n_variantes,
)

RACINE = Path(__file__).resolve().parent.parent


class _Variante:
    def __init__(self, is_chosen=False, design_json=None):
        self.is_chosen = is_chosen
        self.design_json = design_json


class _Job:
    def __init__(self, status="terminee", variantes=(), n_variantes=4):
        self.status = status
        self.variantes = list(variantes)
        self.n_variantes = n_variantes


DESIGN = {"name": "x", "bbox": [4, 4, 4], "ops": []}


# --- relance ----------------------------------------------------------------------

def test_un_essai_termine_est_relancable():
    assert refus_de_relance(_Job(status="terminee")) is None


def test_un_echec_est_relancable():
    """C'est le cas d'usage principal : la demande est restee sans resultat."""
    assert refus_de_relance(_Job(status="echec")) is None


def test_un_essai_en_vol_n_est_pas_relancable():
    """Sinon deux generations sur la meme demande tourneraient en parallele, alors
    que LM Studio est mono-modele sur CPU."""
    for status in EN_VOL:
        assert refus_de_relance(_Job(status=status)), f"{status} devrait etre refuse"


# --- regeneration -----------------------------------------------------------------

def test_regeneration_possible_avec_un_design_retenu():
    job = _Job(variantes=[_Variante(), _Variante(is_chosen=True, design_json=DESIGN)])
    assert refus_de_regeneration(job) is None


def test_regeneration_refusee_sans_choix():
    """Sans variante retenue il n'y a pas de modele a refaire : la phase 2 echouerait
    en cours de route au lieu de refuser tout de suite."""
    job = _Job(variantes=[_Variante(), _Variante()])
    assert refus_de_regeneration(job)


def test_regeneration_refusee_si_le_design_manque():
    """Une variante en echec peut etre marquee retenue par une reprise : son
    design_json est vide, et c'est LUI qui regenere tout le reste."""
    job = _Job(variantes=[_Variante(is_chosen=True, design_json=None)])
    assert refus_de_regeneration(job)


def test_regeneration_refusee_pendant_les_travaux():
    job = _Job(status="en_finition",
               variantes=[_Variante(is_chosen=True, design_json=DESIGN)])
    assert refus_de_regeneration(job)


# --- bornes partagees -------------------------------------------------------------

def test_n_variantes_absent_reprend_le_defaut():
    """Une relance sans precision reprend le nombre de l'essai d'origine."""
    assert verifier_n_variantes(None, 3) == (3, None)


def test_n_variantes_hors_bornes_refuse():
    for valeur in (0, 7, -1, 100):
        n, erreur = verifier_n_variantes(valeur, 4)
        assert n is None and erreur, f"{valeur} devrait etre refuse"


def test_n_variantes_non_entier_refuse():
    for valeur in ("quatre", [], {}):
        n, erreur = verifier_n_variantes(valeur, 4)
        assert n is None and erreur, f"{valeur!r} devrait etre refuse"


def test_n_variantes_texte_numerique_accepte():
    """Le corps JSON peut arriver avec un nombre en texte depuis un client tiers."""
    assert verifier_n_variantes("3", 4) == (3, None)


# --- coherence interface / API ----------------------------------------------------

def _routes_declarees():
    source = (RACINE / "server" / "app.py").read_text(encoding="utf-8")
    return {m.group(1) for m in re.finditer(r'@app\.\w+\("(/api/[^"]*)"', source)}


def _prefixe_connu(appelee, gabarits):
    """L'URL appelee est-elle le debut d'une route declaree ?

    L'interface construit ses URLs par concatenation (`'/api/variantes/' + v.id +
    '/render/iso'`) : on ne recupere donc que des morceaux. Verifier que chaque
    morceau prefixe une route reelle suffit a attraper ce qu'on cherche — une faute
    de frappe dans le nom d'une ressource ou une route renommee d'un seul cote.
    """
    segments = [p for p in appelee.strip("/").split("/") if p]
    for gabarit in gabarits:
        attendus = gabarit.strip("/").split("/")
        if len(segments) > len(attendus):
            continue
        if all(a == b for a, b in zip(segments, attendus) if b != "{}"):
            return True
    return False


def test_l_interface_n_appelle_que_des_routes_existantes():
    """Les deux cotes vivent dans des fichiers separes et aucun build ne les relie :
    une faute de frappe dans une URL ne se verrait qu'a l'execution, sous la forme
    d'un bouton qui ne fait rien du tout.
    """
    html = (RACINE / "server" / "static" / "index.html").read_text(encoding="utf-8")
    gabarits = {re.sub(r"\{[^}]*\}", "{}", r) for r in _routes_declarees()}
    assert gabarits, "aucune route trouvee dans server/app.py"

    appelees = {re.sub(r"\$\{[^}]*\}", "{}", m.group(1))
                for m in re.finditer(r"""["'`](/api/[^"'`?]*)""", html)}
    assert len(appelees) >= 5, f"extraction suspecte : {appelees}"

    for appelee in sorted(appelees):
        assert _prefixe_connu(appelee, gabarits), (
            f"l'interface appelle {appelee}, qui ne prefixe aucune route de "
            f"server/app.py : {sorted(gabarits)}")


def test_les_deux_nouvelles_routes_sont_bien_la():
    declarees = _routes_declarees()
    assert "/api/jobs/{job_id}/relancer" in declarees
    assert "/api/jobs/{job_id}/regenerer" in declarees


# --- avancement lisible -----------------------------------------------------------

def test_chaque_etape_ecrite_par_l_agent_est_traduite():
    """L'agent ecrit son avancement en texte libre. Une etape ajoutee sans etre
    declaree dans PHASES_PAR_ETAPE ferait retomber la barre dans l'inconnu, en
    silence : ce test relit les chaines dans le code et exige qu'elles soient
    toutes connues."""
    from server.regles import PHASES_PAR_ETAPE

    ecrites = set()
    for fichier in ("agent.py", "server/app.py"):
        source = (RACINE / fichier).read_text(encoding="utf-8")
        ecrites |= set(re.findall(r'_etat\(session, job, "([^"]+)"', source))
        ecrites |= set(re.findall(r'job\.etape = "([^"]+)"', source))

    assert ecrites, "aucune etape trouvee : l'extraction est cassee"
    inconnues = ecrites - set(PHASES_PAR_ETAPE)
    assert not inconnues, f"etapes non traduites : {sorted(inconnues)}"


def test_le_statut_prime_sur_l_etape():
    """Un job termine ne doit pas afficher comme en cours la derniere etape
    traversee, et un job en attente n'a pas commence meme s'il porte l'etape d'une
    tentative precedente."""
    from server.regles import phase_de

    assert phase_de("terminee", "rendu des etapes") == "fini"
    assert phase_de("en_attente", "design") == "file"
    assert phase_de("echec", "notice PDF") == "echec"
    assert phase_de("a_choisir", None) == "choix"


def test_une_etape_inconnue_ne_fait_pas_disparaitre_la_barre():
    """Repli volontaire : mieux vaut une barre un peu fausse qu'une interface vide."""
    from server.regles import ORDRE_DES_PHASES, phase_de

    assert phase_de("en_cours", "quelque chose de nouveau") in ORDRE_DES_PHASES


def test_les_phases_traduites_existent_toutes_dans_l_ordre():
    from server.regles import ORDRE_DES_PHASES, PHASES_PAR_ETAPE

    assert set(PHASES_PAR_ETAPE.values()) <= set(ORDRE_DES_PHASES)


# --- vignette d'historique --------------------------------------------------------

class _VarianteV:
    def __init__(self, is_chosen=False, render_dir=None, design_json=None, id="v"):
        self.is_chosen = is_chosen
        self.render_dir = render_dir
        self.design_json = design_json
        self.id = id


def test_la_vignette_retombe_sur_la_premiere_proposition_rendue():
    """Le defaut signale : une demande dont la premiere proposition a echoue
    n'affichait qu'une case grise, alors que les suivantes etaient rendues."""
    from server.regles import variante_vignette

    job = _Job(status="a_choisir", variantes=[
        _VarianteV(id="ratee"),
        _VarianteV(id="bonne", render_dir="job_x/variante_02"),
        _VarianteV(id="autre", render_dir="job_x/variante_03"),
    ])
    assert variante_vignette(job).id == "bonne"


def test_la_vignette_prefere_la_variante_retenue():
    from server.regles import variante_vignette

    job = _Job(status="terminee", variantes=[
        _VarianteV(id="premiere", render_dir="a"),
        _VarianteV(id="retenue", render_dir="b", is_chosen=True),
    ])
    assert variante_vignette(job).id == "retenue"


def test_aucune_vignette_quand_rien_n_est_rendu():
    from server.regles import variante_vignette

    assert variante_vignette(_Job(variantes=[_VarianteV(), _VarianteV()])) is None


# --- propositions supplementaires -------------------------------------------------

def test_on_compte_les_propositions_qui_n_ont_pas_abouti():
    from server.regles import propositions_manquantes

    job = _Job(status="a_choisir", n_variantes=4, variantes=[
        _Variante(design_json=DESIGN), _Variante(design_json=DESIGN), _Variante()])
    assert propositions_manquantes(job) == 2


def test_completion_possible_seulement_au_moment_du_choix():
    """Avant, les propositions sont encore en production ; apres, le choix est fait
    et en ajouter n'aurait plus de sens."""
    from server.regles import refus_de_completion

    manquante = [_Variante(design_json=DESIGN), _Variante()]
    assert refus_de_completion(_Job(status="a_choisir", n_variantes=2,
                                    variantes=manquante)) is None
    for status in ("en_cours", "en_attente", "terminee", "echec", "en_finition"):
        assert refus_de_completion(_Job(status=status, n_variantes=2,
                                        variantes=manquante))


def test_rien_a_completer_quand_tout_a_abouti():
    from server.regles import refus_de_completion

    job = _Job(status="a_choisir", n_variantes=2,
               variantes=[_Variante(design_json=DESIGN), _Variante(design_json=DESIGN)])
    assert refus_de_completion(job)
