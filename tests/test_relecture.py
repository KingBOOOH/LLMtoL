"""Planche de relecture : validation de l'avis, et ce que la page ne doit pas montrer.

Comme pour les autres regles de l'API, le test passe par server/regles.py et jamais
par server/app.py : fastapi n'est installe que dans le conteneur, un import de l'app
echouerait sur l'hote.

La page, elle, est verifiee en texte — plus un controle de syntaxe par node quand il
est la, meme approche que tests/test_frontend.py : rien ne relit ce JavaScript avant
le navigateur.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.regles import COMMENTAIRE_MAX, VERDICTS, verifier_avis

RACINE = Path(__file__).resolve().parent.parent
PAGE = RACINE / "server" / "static" / "relecture.html"
INDEX = RACINE / "server" / "static" / "index.html"
INIT_SQL = RACINE / "db" / "init.sql"
DB_PY = RACINE / "server" / "db.py"

NODE = shutil.which("node")
sans_node = pytest.mark.skipif(NODE is None, reason="node absent, verification sautee")

COLONNES_AVIS = ("correspond", "commentaire", "avis_at")


# --- validation de l'avis ---------------------------------------------------------

def test_un_avis_positif_passe():
    avis, erreur = verifier_avis({"correspond": "oui"})
    assert erreur is None
    assert avis == ("oui", None)


def test_un_avis_negatif_avec_sa_raison():
    avis, erreur = verifier_avis({"correspond": "non",
                                  "commentaire": "  il manque les roues  "})
    assert erreur is None
    assert avis == ("non", "il manque les roues")


def test_les_trois_verdicts_sont_acceptes():
    """Un modele n'est pas bon ou mauvais, il ressemble plus ou moins : « partiel »
    est le cas le plus courant, et un binaire l'aurait perdu."""
    for verdict in VERDICTS:
        avis, erreur = verifier_avis({"correspond": verdict,
                                      "commentaire": "la silhouette est bonne"})
        assert erreur is None, f"{verdict} refuse : {erreur}"
        assert avis[0] == verdict


def test_les_verdicts_vont_du_meilleur_au_pire():
    """L'ordre n'est pas cosmetique : c'est celui du classement. Un verdict gradue
    rend la correlation de rang calculable sur une variante seule, la ou un binaire
    obligerait a ne comparer que des paires."""
    assert VERDICTS == ("oui", "partiel", "non")


def test_partiellement_exige_une_precision():
    """« Partiellement » sans precision ne dit rien d'exploitable : partiellement en
    quoi ? C'est la seule contrainte asymetrique, et c'est voulu."""
    avis, erreur = verifier_avis({"correspond": "partiel"})
    assert avis is None
    assert "précision" in erreur.lower()


def test_oui_et_non_se_suffisent_a_eux_memes():
    """Exiger un commentaire partout couterait du volume de relecture, qui est la
    ressource rare."""
    for verdict in ("oui", "non"):
        avis, erreur = verifier_avis({"correspond": verdict})
        assert erreur is None, f"{verdict} seul devrait passer"
        assert avis == (verdict, None)


def test_un_commentaire_seul_suffit():
    """On n'oblige pas a trancher : une remarque sans verdict reste de la matiere."""
    avis, erreur = verifier_avis({"commentaire": "proportions ecrasees"})
    assert erreur is None
    assert avis == (None, "proportions ecrasees")


def test_un_avis_vide_des_deux_cotes_est_refuse():
    """Un nouvel avis ecrase le precedent : accepter le vide effacerait un vrai avis
    sur un clic de travers."""
    for charge in ({}, {"commentaire": "   "}, {"correspond": None,
                                                "commentaire": ""}):
        avis, erreur = verifier_avis(charge)
        assert avis is None and erreur, f"{charge} aurait du etre refuse"


def test_correspond_n_accepte_que_les_trois_verdicts():
    """Un booleen est refuse EXPRES : « oui » et « partiel » seraient tous deux vrais
    pour un bool() naif, et l'ancien format binaire enregistrerait silencieusement un
    « oui » la ou l'utilisateur a clique « partiellement »."""
    for valeur in (True, False, 0, 1, "peut-etre", "OUI", "", []):
        avis, erreur = verifier_avis({"correspond": valeur})
        assert avis is None and erreur, f"correspond={valeur!r} aurait du etre refuse"


def test_un_commentaire_trop_long_est_refuse():
    avis, erreur = verifier_avis({"commentaire": "x" * (COMMENTAIRE_MAX + 1)})
    assert avis is None
    assert str(COMMENTAIRE_MAX) in erreur


def test_un_commentaire_a_la_limite_passe():
    avis, erreur = verifier_avis({"commentaire": "x" * COMMENTAIRE_MAX})
    assert erreur is None
    assert avis[1] == "x" * COMMENTAIRE_MAX


def test_un_commentaire_non_textuel_est_refuse():
    avis, erreur = verifier_avis({"commentaire": {"non": "merci"}})
    assert avis is None and erreur


def test_une_charge_qui_n_est_pas_un_objet_est_refusee():
    avis, erreur = verifier_avis(["correspond"])
    assert avis is None and erreur


# --- le schema est declare aux DEUX endroits --------------------------------------

def test_les_colonnes_d_avis_sont_dans_init_sql_et_dans_assurer_schema():
    """db/init.sql n'est joue qu'a la creation du volume Postgres, assurer_schema()
    rattrape les bases deja en service. Une colonne ajoutee d'un seul cote ferait
    diverger une base neuve d'une base migree, sans erreur visible."""
    sql = INIT_SQL.read_text(encoding="utf-8")
    db = DB_PY.read_text(encoding="utf-8")
    for colonne in COLONNES_AVIS:
        assert colonne in sql, f"{colonne} absente de db/init.sql"
        assert f"ADD COLUMN IF NOT EXISTS {colonne}" in db, \
            f"{colonne} absente de assurer_schema()"


# --- la page ----------------------------------------------------------------------

def _script_de_la_page():
    html = PAGE.read_text(encoding="utf-8")
    blocs = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
    assert blocs, "aucun script inline dans relecture.html"
    return max(blocs, key=len)


def test_la_page_existe_et_porte_son_application():
    assert "Vue.createApp" in _script_de_la_page()


@sans_node
def test_le_javascript_de_la_page_est_syntaxiquement_valide():
    """Sans chaine de build, une virgule de trop ne se verrait qu'a l'usage, par une
    page blanche."""
    source = _script_de_la_page()
    with tempfile.TemporaryDirectory() as d:
        chemin = os.path.join(d, "verif.mjs")
        # Vue n'est pas charge ici : on ne verifie que la syntaxe, en remplacant
        # createApp par une fonction qui ne fait rien.
        Path(chemin).write_text(
            "const Vue = { createApp: () => ({ mount: () => {} }) };\n" + source,
            encoding="utf-8")
        out = subprocess.run([NODE, chemin], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60,
                             check=False)
        assert out.returncode == 0, out.stderr or out.stdout


def test_la_page_ne_montre_jamais_le_score():
    """Le coeur de l'US : un score affiche pendant la relecture contaminerait l'avis,
    et une metrique calibree dessus ne ferait que redecouvrir le score."""
    html = PAGE.read_text(encoding="utf-8").lower()
    for interdit in ("v.score", "score }}", "{{ score", "n_pieces", "fill_rate",
                     "joint_alignment"):
        assert interdit not in html, f"la page expose {interdit}"


def test_la_page_enregistre_l_avis_sur_la_bonne_route():
    source = _script_de_la_page()
    assert "/avis" in source
    assert "/api/relecture" in source


def test_la_page_propose_de_ne_voir_que_ce_qui_reste_a_relire():
    """C'est ce qui la rend utilisable au volume : sans ce filtre, on relit deux fois
    les memes et jamais les nouvelles."""
    assert "sans_avis" in _script_de_la_page()


def test_la_page_propose_les_trois_verdicts_dans_le_meme_ordre_que_le_serveur():
    """La liste est recopiee dans la page : impossible de l'importer depuis
    server/regles.py sans chaine de build. C'est ce test qui rend la duplication
    sure — et l'ordre compte, c'est celui du classement."""
    source = _script_de_la_page()
    positions = [source.find(f'valeur: "{v}"') for v in VERDICTS]
    assert all(p != -1 for p in positions), f"verdict absent de la page : {positions}"
    assert positions == sorted(positions), "la page n'ordonne pas comme le serveur"


def test_la_page_annonce_la_precision_exigee_par_partiel():
    """Sinon l'utilisateur clique « partiellement », enregistre, et bute sur un 400
    qu'il n'avait aucun moyen de prevoir."""
    html = PAGE.read_text(encoding="utf-8")
    assert "'partiel'" in html, "la page n'applique pas la regle du partiel"
    assert "Précise ce qui correspond" in html


def test_le_bouton_enregistrer_applique_la_meme_regle_que_le_serveur():
    """La page grise le bouton sur un avis vide ; le serveur le refuse pour de vrai.
    Les deux doivent dire la meme chose, sinon le bouton promet ce qui echouera.

    La definition vit dans le script, l'usage dans l'attribut `:disabled` du gabarit :
    on cherche donc chacun dans sa moitie du fichier."""
    assert "aQuoiDire(b)" in _script_de_la_page(), "la regle n'est pas definie"
    html = PAGE.read_text(encoding="utf-8")
    assert "!aQuoiDire(" in html, "le bouton ne s'appuie pas sur la regle"


def test_on_atteint_la_relecture_depuis_le_panneau_principal():
    """Une page qu'aucun lien ne designe n'existe pas pour l'utilisateur.

    Le lien pointe l'URL sans extension, qui n'existe que par une route explicite de
    server/app.py : StaticFiles ne rendrait /ui/relecture que depuis un dossier. Si
    la route disparaissait, le lien donnerait un 404 — d'ou la verification des deux
    cotes, le seul test possible sur l'hote ou fastapi n'est pas installe.
    """
    index = INDEX.read_text(encoding="utf-8")
    assert 'href="/ui/relecture"' in index
    app = (RACINE / "server" / "app.py").read_text(encoding="utf-8")
    assert '@app.get("/ui/relecture"' in app, "la route sans extension a disparu"

    # Deux entrees, et les deux comptent : la pastille d'en-tete est la navigation,
    # le lien de la rangee de titre est au-dessus des generations passees — le seul
    # endroit ou « relire » veut dire quelque chose. En en-tete seule, elle avait
    # l'allure d'un badge d'etat a cote de celui de l'agent, et passait inapercue.
    assert 'class="lien-action" href="/ui/relecture"' in index, \
        "plus de lien dans le corps de la page de generation"
    assert index.count('href="/ui/relecture"') >= 2


def test_la_saisie_ne_vit_pas_dans_la_reponse_de_l_api():
    """Les champs ecrivent dans un brouillon par variante : sans ca, un rechargement
    de la liste effacerait une saisie en cours."""
    source = _script_de_la_page()
    assert "brouillon" in source


# --- gabarits equilibres ----------------------------------------------------------
#
# Une balise non fermee dans un gabarit Vue ne provoque aucune erreur : elle donne
# une PAGE BLANCHE, et sans chaine de build rien ne la signale avant le navigateur de
# l'utilisateur. Le controle de syntaxe par node ne voit que le <script>, pas le
# gabarit. Celui-ci couvre les deux pages : la verification est generique, et
# index.html en profite gratuitement.

BALISES_VIDES = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                 "meta", "source", "track", "wbr"}


class _Equilibre(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pile = []
        self.fautes = []

    def handle_starttag(self, tag, attrs):
        if tag not in BALISES_VIDES:
            self.pile.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag in BALISES_VIDES:
            return
        if not self.pile:
            self.fautes.append(f"</{tag}> ligne {self.getpos()[0]} sans ouverture")
        elif self.pile[-1][0] != tag:
            ouvert, ligne = self.pile[-1]
            self.fautes.append(f"</{tag}> ligne {self.getpos()[0]} ferme <{ouvert}> "
                               f"ouvert ligne {ligne}")
            self.pile.pop()
        else:
            self.pile.pop()


@pytest.mark.parametrize("page", [PAGE, INDEX], ids=lambda p: p.name)
def test_les_balises_du_gabarit_sont_equilibrees(page):
    analyse = _Equilibre()
    analyse.feed(page.read_text(encoding="utf-8"))
    jamais_fermees = [f"<{t}> ligne {ligne} jamais fermé" for t, ligne in analyse.pile]
    assert not analyse.fautes, analyse.fautes
    assert not jamais_fermees, jamais_fermees
