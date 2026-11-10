"""Signe de vie de l'agent (agent.py).

Pourquoi ce test existe : le signal etait emis par la boucle principale, qui est
bloquee pendant un rendu Blender de plusieurs minutes. Constate en conditions reelles
— 3 min 05 sans signal pendant un rendu d'etapes, pour un delai cote API de 90 s :
l'interface affichait "aucun agent actif" alors que l'agent travaillait.

Tout est teste sans base ni thread reel : `boucle_signe_de_vie` accepte une fabrique
de session et une fonction d'emission injectables.
"""

import threading
from pathlib import Path


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


# --- Reprise d'un job abandonne --------------------------------------------------

class _VarianteFactice:
    def __init__(self, indice, is_chosen=False):
        self.indice = indice
        self.is_chosen = is_chosen


class _JobFactice:
    def __init__(self, variantes, status="en_cours"):
        self.variantes = variantes
        self.status = status
        self.claimed_at = "ancien"
        self.etape = None


class _SessionReprise:
    """Session factice : on observe les suppressions et le commit, rien d'autre."""

    def __init__(self, jobs):
        self._jobs = jobs
        self.supprimes = []
        self.commits = 0

    def scalars(self, _requete):
        return self

    def all(self):
        return self._jobs

    def delete(self, obj):
        self.supprimes.append(obj)

    def add(self, _obj):
        pass

    def commit(self):
        self.commits += 1


def test_reprise_phase_1_repart_de_zero():
    """Les variantes partielles rejoueraient les memes indices et violeraient
    UNIQUE(job_id, indice) : le job echouerait au lieu de reprendre."""
    agent = _agent()
    job = _JobFactice([_VarianteFactice(1), _VarianteFactice(2)])
    session = _SessionReprise([job])

    assert agent.reprendre_les_abandonnes(session, limite="peu importe") == 1
    assert job.status == "en_attente"
    assert len(session.supprimes) == 2, "les variantes partielles doivent etre effacees"
    assert job.claimed_at is None


def test_reprise_phase_2_conserve_la_variante_retenue():
    """Interrompu pendant la notice : le choix de l'utilisateur ne doit pas etre perdu."""
    agent = _agent()
    retenue = _VarianteFactice(2, is_chosen=True)
    job = _JobFactice([_VarianteFactice(1), retenue])
    session = _SessionReprise([job])

    agent.reprendre_les_abandonnes(session, limite="peu importe")
    assert job.status == "en_finition"
    assert session.supprimes == [], "rien ne doit etre efface apres le choix"


def test_aucun_job_abandonne():
    agent = _agent()
    session = _SessionReprise([])
    assert agent.reprendre_les_abandonnes(session, limite="peu importe") == 0


# --- Suivi d'avancement du rendu ---------------------------------------------------

def test_progression_blender_reconnue():
    """Blender annonce "[k/n] nom" : c'est la seule progression reelle de la phase
    la plus longue. Si ce format change, la barre se fige sans rien dire."""
    import pipeline

    for ligne, attendu in (
        ("[1/49] etape_001\n", (1, 49)),
        ("[38/46] 3020_c4\n", (38, 46)),
        ("[7/7] pieces\n", (7, 7)),
    ):
        m = pipeline._PROGRES_BLENDER.match(ligne)
        assert m, f"non reconnu : {ligne!r}"
        assert (int(m.group(1)), int(m.group(2))) == attendu


def test_lignes_non_progression_ignorees():
    """Blender et l'add-on crachent des centaines de lignes : aucune ne doit etre
    prise pour un avancement."""
    import pipeline

    for ligne in ("Info: [importldraw] Loading files\n",
                  "Moteur : CYCLES, resolution : 800x800\n",
                  "Saved: 'render_iso.png'\n",
                  "  [pas en debut de ligne]\n",
                  "[abc/def]\n"):
        assert pipeline._PROGRES_BLENDER.match(ligne) is None, f"faux positif : {ligne!r}"


def test_le_format_de_progression_de_blender_n_a_pas_change():
    """Verrouille le contrat entre blender_script.py et le lecteur de pipeline.py.

    Les deux vivent dans des processus differents : rien d'autre ne relierait un
    changement de format a la barre de progression qui cesserait de bouger.
    """
    import re

    import pipeline

    racine = Path(__file__).resolve().parent.parent
    source = (racine / "src" / "blender_script.py").read_text(encoding="utf-8")
    emission = re.search(r'print\(f"\[\{i\}/\{len\(work_dirs\)\}\][^"]*"', source)
    assert emission, "blender_script.py n'emet plus '[k/n] ...' en debut de ligne"
    # Et le lecteur sait bien lire ce que l'emetteur produit.
    assert pipeline._PROGRES_BLENDER.match("[3/12] quelque_chose")


def test_au_demarrage_tout_job_en_cours_est_repris():
    """Un agent qui demarre ne peut avoir aucun job legitimement en vol.

    Sans ca, relancer l'agent laissait son ancien job bloque REPRISE_APRES_S
    (30 min) et l'interface affichait deux generations en cours simultanement.
    """
    import re

    import agent as agent_mod

    source = Path(agent_mod.__file__).read_text(encoding="utf-8")
    corps = source[source.index("def main("):]
    assert re.search(r"reprendre_les_abandonnes\(session,\s*limite=demarrage\)", corps), \
        "main() doit reprendre sans attendre le delai de peremption"


def test_render_all_branche_le_suivi_quand_on_lui_en_donne_un():
    """on_progress etait accepte, documente... et jamais appele.

    Consequence reelle : pendant le rendu — de loin la phase la plus longue — la barre
    de l'interface restait sur la valeur posee avant le lancement. La fonction qui sait
    lire l'avancement existait deja (_rendre_en_suivant) mais rien ne l'appelait.

    Le lancement de Blender est remplace ici parce qu'on teste le branchement, pas le
    rendu : sans agent de substitution, le test exigerait Blender et douze minutes.
    """
    import pipeline

    appels = []
    suivi, brut = pipeline._rendre_en_suivant, pipeline.subprocess.run
    try:
        pipeline._rendre_en_suivant = lambda env, cb: appels.append("suivi")
        pipeline.subprocess.run = lambda *a, **k: appels.append("brut")

        racine = Path(__file__).resolve().parent.parent / "outputs"
        racine.mkdir(exist_ok=True)
        pipeline.render_all([str(racine)], str(racine), assemble_dirs=[],
                            manifest_name="_manifest_test_suivi.txt",
                            on_progress=lambda k, n: None)
        assert appels == ["suivi"], "un on_progress fourni doit passer par le lecteur"

        appels.clear()
        pipeline.render_all([str(racine)], str(racine), assemble_dirs=[],
                            manifest_name="_manifest_test_suivi.txt")
        assert appels == ["brut"], "sans suivi, on garde le simple subprocess.run"
    finally:
        pipeline._rendre_en_suivant, pipeline.subprocess.run = suivi, brut
        (racine / "_manifest_test_suivi.txt").unlink(missing_ok=True)


# --- Regeneration : repartir d'un dossier incomplet --------------------------------

def test_chemins_manquants_sur_un_dossier_vide(tmp_path):
    """Une regeneration peut partir d'un dossier efface : tout est a refaire."""
    import agent as agent_mod

    manquant = agent_mod.chemins_manquants(str(tmp_path))
    assert manquant == {"ldr": True, "rendus": True}


def test_chemins_manquants_sur_un_dossier_complet(tmp_path):
    """Le rendu des quatre vues coute plusieurs minutes : on ne le refait pas pour
    rien quand il est deja la."""
    import agent as agent_mod

    (tmp_path / "model.ldr").write_text("0 test\n", encoding="utf-8")
    (tmp_path / "render_iso.png").write_bytes(b"\x89PNG")
    assert agent_mod.chemins_manquants(str(tmp_path)) == {"ldr": False, "rendus": False}


def test_un_ldr_present_sans_rendu_ne_fait_refaire_que_le_rendu(tmp_path):
    """Cas du changement de moteur de rendu : le modele est bon, les images non."""
    import agent as agent_mod

    (tmp_path / "model.ldr").write_text("0 test\n", encoding="utf-8")
    assert agent_mod.chemins_manquants(str(tmp_path)) == {"ldr": False, "rendus": True}


def test_la_phase_2_reconstruit_avant_de_composer():
    """Verrouille l'ordre : si la reconstruction passait APRES preparer_notice, une
    regeneration sur dossier efface echouerait au lieu de se reparer."""
    import re

    import agent as agent_mod

    source = Path(agent_mod.__file__).read_text(encoding="utf-8")
    corps = source[source.index("def finir("):source.index("def traiter_un(")]
    i_recons = corps.index("chemins_manquants(iter_dir)")
    i_notice = corps.index("pipeline.preparer_notice(")
    assert i_recons < i_notice, "la reconstruction doit preceder la notice"
    assert re.search(r"os\.makedirs\(iter_dir", corps), \
        "le dossier doit etre recree s'il a disparu"


# --- Propositions supplementaires : ne reproduire que ce qui manque ----------------

class _VarianteIndice:
    def __init__(self, indice, design_json=None):
        self.indice = indice
        self.design_json = design_json


def test_on_ne_reproduit_que_les_propositions_ratees():
    """Refaire les reussies reviendrait a jeter plusieurs minutes de rendu Blender,
    et a remplacer un resultat que l'utilisateur voyait deja."""
    import agent as agent_mod

    variantes = [_VarianteIndice(1), _VarianteIndice(2, design_json={"ok": True}),
                 _VarianteIndice(3), _VarianteIndice(4, design_json={"ok": True})]
    assert agent_mod.indices_a_produire(variantes, 4) == [1, 3]


def test_un_job_neuf_produit_tout():
    import agent as agent_mod

    assert agent_mod.indices_a_produire([], 3) == [1, 2, 3]


def test_plus_rien_a_produire_quand_tout_a_abouti():
    import agent as agent_mod

    variantes = [_VarianteIndice(i, design_json={"ok": True}) for i in (1, 2)]
    assert agent_mod.indices_a_produire(variantes, 2) == []


def test_les_indices_rates_sont_reutilises_et_leurs_lignes_effacees():
    """Sans effacement prealable, reutiliser l'indice violerait
    UNIQUE(job_id, indice) et ferait echouer le job au lieu de le completer."""
    import re

    import agent as agent_mod

    source = Path(agent_mod.__file__).read_text(encoding="utf-8")
    corps = source[source.index("def produire_variantes("):source.index("def finir(")]
    assert re.search(r"session\.delete\(v\)", corps), \
        "les lignes des tentatives ratees doivent etre effacees avant d'etre rejouees"
    assert corps.index("session.delete(v)") < corps.index("Variante(job_id=job.id")


def test_un_echec_total_ne_jette_pas_les_propositions_deja_acquises():
    """Sur une demande de propositions supplementaires, les anciennes restent
    valables meme si toutes les nouvelles echouent."""
    import agent as agent_mod

    source = Path(agent_mod.__file__).read_text(encoding="utf-8")
    corps = source[source.index("def produire_variantes("):source.index("def finir(")]
    i_garde = corps.index("if not acquises:")
    i_echec = corps.index('raise RuntimeError("aucune variante n\'a abouti")')
    assert i_garde < i_echec, "l'echec doit porter sur l'ensemble, pas sur le lot du jour"


# ---------- pre-controle LM Studio ----------

def test_un_precontrole_en_echec_attend_au_lieu_de_mourir(monkeypatch):
    """Le piege mesure : SystemExit ici etait vu par Docker comme un plantage
    (restart: unless-stopped), le conteneur redemarrait, le pre-controle redemandait
    le modele de generation, et le JIT loading de LM Studio rechargeait 18,6 Go toutes
    les quelques secondes — jusqu'a faire tuer par pression memoire tout autre travail.
    """
    import agent as agent_mod

    reponses = [(False, "injoignable"), (False, "injoignable"), (True, "ok")]
    attentes = []
    monkeypatch.setattr(agent_mod.time, "sleep", attentes.append)
    monkeypatch.setattr(agent_mod, "log", lambda _: None)

    agent_mod.attendre_lm_studio(lambda: reponses.pop(0))

    assert reponses == [], "le controle doit etre rejoue jusqu'a reussir"
    assert attentes == [agent_mod.ATTENTE_LM_MIN_S, agent_mod.ATTENTE_LM_MIN_S * 2], \
        "le recul doit croitre : un chargement de 18,6 Go doit avoir le temps d'aboutir"


def test_le_recul_est_plafonne(monkeypatch):
    """Sans plafond, l'agent finirait par ne plus reessayer qu'une fois par jour."""
    import agent as agent_mod

    attentes = []
    monkeypatch.setattr(agent_mod.time, "sleep", attentes.append)
    monkeypatch.setattr(agent_mod, "log", lambda _: None)

    essais = [(False, "injoignable")] * 12 + [(True, "ok")]
    agent_mod.attendre_lm_studio(lambda: essais.pop(0))

    assert max(attentes) == agent_mod.ATTENTE_LM_MAX_S


def test_une_passe_unique_ne_doit_pas_attendre(monkeypatch):
    """`--une-fois` est lance a la main : on veut voir l'erreur, pas une attente."""
    import pytest

    import agent as agent_mod

    monkeypatch.setattr(agent_mod, "log", lambda _: None)
    monkeypatch.setattr(agent_mod.time, "sleep",
                        lambda _: pytest.fail("aucune attente en passe unique"))

    with pytest.raises(SystemExit, match="Agent non demarre"):
        agent_mod.attendre_lm_studio(lambda: (False, "injoignable"), une_fois=True)
