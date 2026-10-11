"""Signe de vie de l'agent (agent.py).

Pourquoi ce test existe : le signal etait emis par la boucle principale, qui est
bloquee pendant un rendu Blender de plusieurs minutes. Constate en conditions reelles
— 3 min 05 sans signal pendant un rendu d'etapes, pour un delai cote API de 90 s :
l'interface affichait "aucun agent actif" alors que l'agent travaillait.

Tout est teste sans base ni thread reel : `boucle_signe_de_vie` accepte une fabrique
de session et une fonction d'emission injectables.
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _SessionFactice:
    """Remplace une session SQLAlchemy : seul le protocole de contexte compte ici."""

    def __init__(self):
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def rollback(self):
        self.rollbacks += 1


def _agent():
    """Importe agent.py sans toucher a la base : DATABASE_URL n'est pas contacte
    tant qu'aucune session n'est reellement ouverte."""
    import agent
    return agent


def test_signal_emis_plusieurs_fois_sans_attendre():
    """Le premier signal part immediatement, pas apres une premiere pause."""
    agent = _agent()
    arret = threading.Event()
    appels = []

    def beat(_session):
        appels.append(1)
        if len(appels) >= 3:
            arret.set()

    agent.boucle_signe_de_vie(arret, intervalle=0.01,
                              session_factory=_SessionFactice, beat=beat)
    assert len(appels) >= 3


def test_une_erreur_de_base_ne_tue_pas_le_thread():
    """Sinon l'agent continuerait a travailler en paraissant mort pour toujours."""
    agent = _agent()
    arret = threading.Event()
    appels = []

    def beat(_session):
        appels.append(1)
        if len(appels) == 1:
            raise RuntimeError("base momentanement injoignable")
        if len(appels) >= 3:
            arret.set()

    agent.boucle_signe_de_vie(arret, intervalle=0.01,
                              session_factory=_SessionFactice, beat=beat)
    assert len(appels) >= 3, "la boucle doit survivre a une erreur"


def test_la_boucle_s_arrete_sur_demande():
    agent = _agent()
    arret = threading.Event()
    arret.set()
    appels = []
    agent.boucle_signe_de_vie(arret, intervalle=30,
                              session_factory=_SessionFactice,
                              beat=lambda _s: appels.append(1))
    assert appels == [1], "un dernier signal part, puis la boucle rend la main"


def test_le_signal_tourne_pendant_un_travail_bloquant():
    """LE test du correctif : un traitement long ne doit pas interrompre le signal.

    On simule un rendu Blender par un sleep dans le thread principal, et on verifie
    que des signaux sont bien partis pendant ce temps.
    """
    agent = _agent()
    arret = threading.Event()
    appels = []

    fil = threading.Thread(
        target=agent.boucle_signe_de_vie,
        args=(arret,),
        kwargs={"intervalle": 0.02, "session_factory": _SessionFactice,
                "beat": lambda _s: appels.append(1)},
        daemon=True)
    fil.start()

    # Le thread principal est occupe, comme pendant un rendu.
    arret.wait(0.3)
    pendant = len(appels)
    arret.set()
    fil.join(timeout=2)

    assert pendant >= 3, (
        f"seulement {pendant} signal(aux) pendant un travail bloquant : "
        "le signe de vie depend encore de la boucle principale")


def test_le_delai_api_laisse_de_la_marge():
    """Le delai cote API doit etre largement superieur a l'intervalle d'emission,
    sinon un hoquet reseau suffirait a declarer l'agent mort."""
    agent = _agent()
    assert agent.AGENT_TIMEOUT_S >= 10 * agent.INTERVALLE_S
