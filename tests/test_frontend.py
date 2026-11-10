"""Interface Vue : syntaxe du script et regroupement des essais.

L'interface est un seul fichier charge par <script> depuis un CDN, sans chaine de
build : rien ne relit ce JavaScript avant le navigateur de l'utilisateur. Une virgule
de trop s'y voyait seulement a l'usage, par une page blanche.

Node sert ici d'interpreteur de verification, pas de dependance du projet : l'image ne
contient toujours pas node, et ces tests se sautent proprement s'il est absent. Un
harnais frontend complet reste a faire (US V2-10).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

import pytest

from src.validator import resume_constructibilite

INDEX = Path(__file__).resolve().parent.parent / "server" / "static" / "index.html"
NODE = shutil.which("node")
sans_node = pytest.mark.skipif(NODE is None, reason="node absent, verification sautee")


def _script_de_l_application():
    """Le plus gros bloc <script> inline : celui qui porte l'application."""
    html = INDEX.read_text(encoding="utf-8")
    blocs = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
    assert blocs, "aucun script inline dans index.html"
    return max(blocs, key=len)


def _node(script):
    """Execute du JavaScript et retourne sa sortie, ou leve avec l'erreur de node."""
    with tempfile.TemporaryDirectory() as d:
        chemin = os.path.join(d, "verif.mjs")
        Path(chemin).write_text(script, encoding="utf-8")
        out = subprocess.run([NODE, chemin], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60,
                             check=False)
        assert out.returncode == 0, out.stderr or out.stdout
        return out.stdout


def test_le_bloc_de_l_application_est_bien_la():
    """Sans node non plus, on verifie au moins qu'on parle du bon fichier."""
    source = _script_de_l_application()
    assert "Vue.createApp" in source
    assert len(source) > 2000, "le bloc retenu n'est pas l'application"


@sans_node
def test_le_javascript_de_l_interface_est_syntaxiquement_valide():
    with tempfile.TemporaryDirectory() as d:
        chemin = os.path.join(d, "app.js")
        Path(chemin).write_text(_script_de_l_application(), encoding="utf-8")
        out = subprocess.run([NODE, "--check", chemin], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", check=False)
        assert out.returncode == 0, out.stderr


# Charge l'application en remplacant Vue par un faux createApp, puis appelle les
# fonctions a tester. Rien n'est simule du RESULTAT : c'est le vrai code de
# server/static/index.html qui tourne, seul le framework est absent.
_HARNAIS = """
let opt = null;
globalThis.Vue = { createApp: (o) => { opt = o; return { mount() {} }; } };
%(source)s
const sortie = (() => { %(corps)s })();
console.log(JSON.stringify(sortie));
process.exit(0);
"""


def _executer(corps):
    script = _HARNAIS % {"source": _script_de_l_application(), "corps": corps}
    return json.loads(_node(script).strip().splitlines()[-1])


@sans_node
def test_les_essais_d_une_meme_demande_sont_regroupes():
    """Trois relances du meme texte doivent se lire comme une demande, pas trois."""
    resultat = _executer("""
      const ctx = { jobs: [
        { id: 'c', racine_id: 'a' }, { id: 'b', racine_id: 'a' },
        { id: 'a', racine_id: 'a' }, { id: 'z', racine_id: 'z' } ] };
      return opt.computed.series.call(ctx).map(s => [s.cle, s.essais.map(e => e.id)]);
    """)
    assert resultat == [["a", ["c", "b", "a"]], ["z", ["z"]]]


@sans_node
def test_un_job_sans_racine_reste_sa_propre_serie():
    """Les jobs crees avant l'ajout de la colonne n'ont pas de racine_id : ils ne
    doivent pas se retrouver tous regroupes sous une meme cle vide."""
    resultat = _executer("""
      const ctx = { jobs: [{ id: 'x' }, { id: 'y' }] };
      return opt.computed.series.call(ctx).map(s => s.essais.length);
    """)
    assert resultat == [1, 1]


@sans_node
def test_les_actions_de_relance_existent_et_partent_fermees():
    """Le panneau de confirmation ne doit pas etre ouvert au chargement : son role est
    justement d'annoncer le cout avant de lancer."""
    resultat = _executer("""
      const d = opt.data();
      return { relance: d.relance, n: d.relanceN,
               methodes: ['regenerer', 'ouvrirRelance', 'confirmerRelance']
                 .filter(m => typeof opt.methods[m] === 'function') };
    """)
    assert resultat["relance"] is None
    assert resultat["n"] == 4
    assert resultat["methodes"] == ["regenerer", "ouvrirRelance", "confirmerRelance"]


@sans_node
def test_ouvrir_la_relance_reprend_le_nombre_de_l_essai():
    """Relancer propose par defaut autant de propositions que l'essai d'origine."""
    resultat = _executer("""
      const ctx = { relance: null, relanceN: 4 };
      opt.methods.ouvrirRelance.call(ctx, { id: 'j1', n_variantes: 6 });
      const ouvert = { relance: ctx.relance, n: ctx.relanceN };
      opt.methods.ouvrirRelance.call(ctx, { id: 'j1', n_variantes: 6 });
      return { ouvert, apresSecondClic: ctx.relance };
    """)
    assert resultat["ouvert"] == {"relance": "j1", "n": 6}
    assert resultat["apresSecondClic"] is None, "un second clic doit refermer"


# --- Barre d'etapes ---------------------------------------------------------------

def _etapes(job, jobs=None):
    """Appelle etapesDe sur le vrai script de l'interface.

    Le contexte herite de opt.methods : dans Vue, `this` porte toutes les methodes, et
    etapesDe en appelle une autre (placeDansLaFile). Un objet nu suffisait aux
    fonctions isolees, pas a celles qui s'appellent entre elles.
    """
    return _executer(f"""
      const ctx = Object.assign(Object.create(opt.methods), {{ jobs: {json.dumps(jobs or [job])} }});
      return opt.methods.etapesDe.call(ctx, {json.dumps(job)});
    """)


ORDRE = ["file", "design", "rendu_propositions", "choix", "notice", "rendu_notice", "pdf"]


def _job(**kw):
    base = {"id": "j1", "status": "en_cours", "phase": "design", "n_variantes": 4,
            "etape_fait": None, "etape_total": None, "ordre_phases": ORDRE,
            "created_at": "2026-10-12T10:00:00+00:00"}
    base.update(kw)
    return base


@sans_node
def test_chaque_proposition_a_sa_propre_etape():
    """"proposition 2 sur 4" repond a la seule question qu'on se pose en attendant :
    combien de temps encore. Un libelle "design" ne le disait pas."""
    etapes = _etapes(_job(etape_fait=2, etape_total=4))
    libelles = [e["libelle"] for e in etapes]
    assert "Proposition 1 sur 4" in libelles
    assert "Proposition 4 sur 4" in libelles
    etats = {e["libelle"]: e["etat"] for e in etapes}
    assert etats["Proposition 1 sur 4"] == "faite"
    assert etats["Proposition 2 sur 4"] == "encours"
    assert etats["Proposition 3 sur 4"] == "attente"


@sans_node
def test_un_job_termine_a_toutes_ses_etapes_faites():
    """Sinon la derniere etape traversee resterait affichee comme en cours."""
    etapes = _etapes(_job(status="terminee", phase="fini"))
    assert {e["etat"] for e in etapes} == {"faite"}


@sans_node
def test_une_etape_en_cours_porte_son_compteur():
    """C'est ce qui distingue "ca avance" de "c'est bloque" sur un rendu de 111
    images."""
    etapes = _etapes(_job(phase="rendu_notice", etape_fait=50, etape_total=111))
    courante = next(e for e in etapes if e["etat"] == "encours")
    assert courante["libelle"] == "Images de la notice"
    assert courante["compteur"] == "50 / 111"


@sans_node
def test_une_seule_etape_est_en_cours_a_la_fois():
    for phase in ("rendu_propositions", "choix", "notice", "pdf"):
        etapes = _etapes(_job(phase=phase))
        assert sum(1 for e in etapes if e["etat"] == "encours") == 1, phase


@sans_node
def test_un_job_en_file_annonce_sa_place():
    """Repond a "il se passe quelque chose ?" sans avoir a aller voir les journaux."""
    devant = _job(id="devant", status="en_cours", phase="design",
                  created_at="2026-10-12T09:00:00+00:00")
    attente = _job(id="apres", status="en_attente", phase="file",
                   created_at="2026-10-12T10:00:00+00:00")
    etapes = _etapes(attente, jobs=[devant, attente])
    premiere = etapes[0]
    assert premiere["etat"] == "encours"
    assert "devant" in premiere["compteur"]


@sans_node
def test_un_echec_marque_les_etapes_atteintes_et_pas_les_suivantes():
    etapes = _etapes(_job(status="echec", phase="echec"))
    assert all(e["etat"] in ("ratee", "attente") for e in etapes)


@sans_node
def test_la_barre_ne_s_affiche_pas_quand_on_attend_l_utilisateur():
    """`a_choisir` attend un clic : montrer une barre d'avancement y serait un
    mensonge."""
    resultat = _executer("""
      return ['en_attente', 'en_cours', 'en_finition', 'a_choisir', 'terminee', 'echec']
        .map(s => [s, opt.methods.enTravaux({ status: s })]);
    """)
    assert dict(resultat) == {"en_attente": True, "en_cours": True, "en_finition": True,
                              "a_choisir": False, "terminee": False, "echec": False}


@sans_node
def test_la_raison_d_un_echec_est_raccourcie():
    """Les messages du validateur listent chaque piece fautive sur des dizaines de
    lignes : la carte n'en montre que la premiere."""
    # Le message part en JSON : l'ecrire en toutes lettres dans le JavaScript
    # demanderait d'echapper les sauts de ligne deux fois, et une erreur y serait
    # invisible — c'est exactement ce qui s'est produit en ecrivant ce test.
    message = ("structure disjointe : 2 composantes non connectees\n"
               "piece flottante : 3007 en (6,0,16)\n"
               "piece flottante : 3007 en (14,0,16)")
    resultat = _executer(f"""
      return [opt.methods.raisonCourte({json.dumps(message)}), opt.methods.raisonCourte("court")];
    """)
    assert "\n" not in resultat[0]
    assert len(resultat[0]) <= 90
    assert resultat[1] == "court"


# --- Quelle demande le panneau suit-il ? ------------------------------------------

def _computed(nom, ctx):
    """Evalue une propriete calculee du vrai script, avec un contexte realiste.

    Les proprietes calculees s'appellent entre elles (`focus` lit `actif`,
    `titreDuFocus` lit `focus`) et appellent des methodes : le contexte doit donc
    porter les unes et les autres, comme le `this` que Vue fournit.
    """
    corps = (
        "const ctx = Object.create(opt.methods);"
        "Object.assign(ctx, " + json.dumps(ctx) + ");"
        "for (const [cle, fn] of Object.entries(opt.computed)) {"
        "  Object.defineProperty(ctx, cle, { get: () => fn.call(ctx) });"
        "}"
        "return ctx[" + json.dumps(nom) + "];"
    )
    return _executer(corps)


@sans_node
def test_ce_qui_travaille_passe_avant_ce_qui_attend_un_clic():
    """Le defaut signale : une demande laissee en attente de choix masquait en
    permanence celle que l'agent traitait, et plus rien ne semblait avancer."""
    ctx = {"ouvert": None, "jobs": [
        _job(id="attend", status="a_choisir", phase="choix"),
        _job(id="travaille", status="en_finition", phase="rendu_notice"),
    ]}
    assert _computed("actif", ctx)["id"] == "travaille"


@sans_node
def test_une_demande_en_file_passe_apres_celle_qui_travaille():
    ctx = {"ouvert": None, "jobs": [
        _job(id="file", status="en_attente", phase="file"),
        _job(id="travaille", status="en_cours", phase="design"),
    ]}
    assert _computed("actif", ctx)["id"] == "travaille"


@sans_node
def test_le_clic_de_l_utilisateur_prime_sur_le_choix_automatique():
    """Cliquer une demande dans l'historique doit l'afficher, meme si une autre
    travaille : c'est la seule facon de revoir une demande terminee."""
    ouvert = _job(id="choisi", status="terminee", phase="fini")
    ctx = {"ouvert": ouvert, "jobs": [
        _job(id="travaille", status="en_cours", phase="design"), ouvert]}
    assert _computed("focus", ctx)["id"] == "choisi"


@sans_node
def test_sans_clic_le_panneau_suit_ce_qui_travaille():
    ctx = {"ouvert": None, "jobs": [_job(id="travaille", status="en_cours")]}
    assert _computed("focus", ctx)["id"] == "travaille"


@sans_node
def test_aucun_panneau_quand_il_n_y_a_rien_a_montrer():
    ctx = {"ouvert": None, "jobs": [_job(id="fini", status="terminee", phase="fini")]}
    assert _computed("focus", ctx) is None


@sans_node
def test_le_titre_dit_ce_qu_on_attend_de_l_utilisateur():
    for status, phase, attendu in (
        ("a_choisir", "choix", "Choisis ta proposition"),
        ("en_cours", "design", "Génération en cours"),
        ("en_attente", "file", "Génération en cours"),
        ("echec", "echec", "Cette demande n'a pas abouti"),
        ("terminee", "fini", "Résultat"),
    ):
        ctx = {"ouvert": _job(status=status, phase=phase), "jobs": []}
        assert _computed("titreDuFocus", ctx) == attendu, status


# --- Mode d'emploi de la liste d'achat --------------------------------------------

@sans_node
def test_le_panneau_d_achat_part_ferme_et_se_replie():
    """Question posee a l'usage : le bouton telechargeait un .xml sans dire ou il va.
    Il ouvre maintenant un mode d'emploi, qu'un second clic referme."""
    resultat = _executer("""
      const d = opt.data();
      return { depart: d.achat, aCopie: typeof opt.methods.copierListeAchat === 'function' };
    """)
    assert resultat["depart"] is None
    assert resultat["aCopie"] is True


def test_le_mode_d_emploi_dit_ou_va_le_fichier():
    """Trois choses que l'utilisateur ne pouvait pas deviner : c'est une wanted list
    et pas un panier, l'option a choisir s'appelle "Upload BrickLink XML format", et
    c'est Buy All qui remplit ensuite les paniers."""
    html = INDEX.read_text(encoding="utf-8")
    assert "https://www.bricklink.com/v2/wanted/upload.page" in html
    assert "Upload BrickLink XML format" in html
    assert "Buy All" in html
    assert "wanted list" in html


def test_le_lien_externe_s_ouvre_sans_donner_la_main_a_la_page_cible():
    """target=_blank sans rel=noopener laisse la page ouverte manipuler la notre."""
    html = INDEX.read_text(encoding="utf-8")
    bloc = html[html.index("bricklink.com/v2/wanted/upload.page") - 200:]
    bloc = bloc[:400]
    assert 'target="_blank"' in bloc
    assert 'rel="noopener"' in bloc


@sans_node
def test_le_modele_ldr_est_telechargeable_depuis_l_interface():
    """La page d'import de BrickLink a DEUX options, et son selecteur de fichier
    n'accepte que .bsx .ldr .lxf .io : le XML n'y passe pas, seul le modele y passe.
    Constate a l'usage, apres avoir propose le mauvais fichier."""
    resultat = _executer("""
      return typeof opt.methods.telechargerModele === 'function';
    """)
    assert resultat is True


def test_le_mode_d_emploi_distingue_les_deux_options():
    """Melanger les deux, c'est renvoyer l'utilisateur vers un selecteur qui refuse
    son fichier — exactement ce qui s'est passe."""
    html = INDEX.read_text(encoding="utf-8")
    assert "Upload a file from your computer" in html
    assert "Upload BrickLink XML format" in html
    assert ".bsx .ldr .lxf .io" in html


# ---------- borne exacte dans le formulaire (US V2-03) ----------

def test_le_champ_de_taille_exacte_existe_et_se_devoile():
    """Un plafond nomme est CUBIQUE : un phare demande en « petite » sort en gateau.
    Le champ n'apparait que si on choisit « exacte », pour ne pas charger le formulaire
    de l'immense majorite des demandes."""
    html = INDEX.read_text(encoding="utf-8")

    assert '<option value="exacte">' in html, "l'option de taille exacte a disparu"
    assert 'v-model="bboxMax"' in html, "le champ n'est plus relie a l'etat"
    assert "v-if=\"taille === 'exacte'\"" in html, "le champ doit rester cache par defaut"


def test_la_taille_exacte_et_la_taille_nommee_s_excluent():
    """« exacte » n'est pas une taille nommee : l'envoyer telle quelle au serveur ferait
    echouer la validation, qui n'accepte que petite/moyenne/grande."""
    script = _script_de_l_application()

    assert 'taille: this.taille === "exacte" ? null' in script, \
        "« exacte » doit etre traduit en taille nulle"
    assert 'bbox_max: this.taille === "exacte" ? this.bboxMax : null' in script, \
        "la borne ne doit partir QUE si « exacte » est choisi"
    assert "bboxMax:" in script, "l'etat initial doit declarer bboxMax"


# --- Avertissements de constructibilite (US V2-10) --------------------------------
#
# server/static/index.html REFAIT en JavaScript la traduction de
# src/validator.resume_constructibilite, parce que l'image de l'API ne contient que
# server/ (Dockerfile.api) et ne voit donc pas src/. Les deux formulations doivent
# rester alignees, et rien ne le garantissait cote JavaScript : changer une phrase
# d'un seul cote passait tous les tests.
#
# Ces tests comparent les SORTIES des deux implementations sur les memes metriques,
# plutot que de relire les deux textes. Meme principe que le test qui compare
# BBOX_MAX entre src/dsl_schema et server/regles : c'est le test qui rend la
# duplication sure, pas la bonne volonte.


def _sans_accents(phrase):
    """L'interface accentue ses phrases, le code Python du projet non.

    La difference est voulue et ne doit pas faire echouer la comparaison : on compare
    le TEXTE, pas l'orthotypographie.
    """
    return "".join(c for c in unicodedata.normalize("NFD", phrase)
                   if unicodedata.category(c) != "Mn")


def _les_deux(n_components=1, z_min=0, n_floating=0, fill_rate=0.4):
    """Passe les memes metriques aux deux implementations. Retourne (python, js).

    Cote Python le compte des flottantes se lit sur la LISTE `flottantes`, cote
    JavaScript sur l'entier `n_floating` que l'API a deja compte (agent.py le derive
    des avertissements du rapport). D'ou la liste factice : ce qui est compare est la
    phrase produite, pas la facon de compter.
    """
    py = resume_constructibilite({"metrics": {
        "n_components": n_components, "z_min": z_min,
        "flottantes": [[0, 0, 0]] * n_floating, "fill_rate": fill_rate,
    }})["phrases"]

    # Un vrai job, pour que retenueDe fasse son travail au lieu d'etre court-circuite.
    job = {"variantes": [
        {"id": "v1", "is_chosen": False, "n_components": 99, "z_min": 99,
         "n_floating": 99, "fill_rate": 0.99},
        {"id": "v2", "is_chosen": True, "n_components": n_components, "z_min": z_min,
         "n_floating": n_floating, "fill_rate": fill_rate},
    ]}
    js = _executer(f"""
      const ctx = Object.create(opt.methods);
      return opt.methods.avertissements.call(ctx, {json.dumps(job)});
    """)
    return [_sans_accents(p) for p in py], [_sans_accents(p) for p in js]


@sans_node
def test_un_modele_sain_n_avertit_de_rien_des_deux_cotes():
    """Une liste vide doit rester vide : l'appelant dit alors explicitement que tout
    va bien, plutot que d'afficher un encadre vide."""
    py, js = _les_deux()
    assert py == []
    assert js == []


@sans_node
@pytest.mark.parametrize("metriques", [
    {"n_components": 3},
    {"z_min": 2},
    {"n_floating": 5},
    {"fill_rate": 0.92},
])
def test_chaque_avertissement_est_formule_pareil_des_deux_cotes(metriques):
    """Pris un par un, pour que l'echec designe l'avertissement qui a divergé."""
    py, js = _les_deux(**metriques)
    assert len(py) == 1, f"cas {metriques} : le Python n'avertit pas"
    assert py == js


@sans_node
def test_les_avertissements_sortent_dans_le_meme_ordre():
    """L'ordre est ce que l'utilisateur lit en premier : il fait partie du contrat."""
    py, js = _les_deux(n_components=2, z_min=3, n_floating=7, fill_rate=0.9)
    assert len(py) == 4
    assert py == js


@sans_node
@pytest.mark.parametrize("taux, avertit", [(0.85, False), (0.86, True)])
def test_le_seuil_de_remplissage_est_le_meme_des_deux_cotes(taux, avertit):
    """85 % exactement ne declenche rien : le seuil est STRICT des deux cotes. Un
    `>=` glisse d'un seul cote ferait avertir l'interface et pas le rapport."""
    py, js = _les_deux(fill_rate=taux)
    assert bool(py) is avertit
    assert py == js


@sans_node
def test_le_pourcentage_de_remplissage_s_arrondit_pareil():
    """Python formate avec `:.0%`, le JavaScript avec Math.round : deux regles
    d'arrondi differentes (pair le plus proche contre superieur). 0,875 est la seule
    valeur exactement a mi-chemin representable au-dessus du seuil — les deux doivent
    y tomber d'accord."""
    py, js = _les_deux(fill_rate=0.875)
    assert "88%" in py[0]
    assert py == js


# --- Cas limites des fonctions d'affichage (US V2-10) -----------------------------
#
# Les valeurs que l'API peut legitimement envoyer avant qu'une etape soit connue :
# un total nul, un champ absent, une liste vide, un statut qu'on n'a pas prevu. Ce
# sont les entrees ou un defaut est SILENCIEUX — la page s'affiche, mais elle ment.


@sans_node
@pytest.mark.parametrize("job, attendu", [
    ({"etape_total": 0, "etape_fait": 0}, None),
    ({"etape_total": None, "etape_fait": 3}, None),
    ({}, None),
    ({"etape_total": 4, "etape_fait": 1}, 25),
    ({"etape_total": 3, "etape_fait": 9}, 100),
])
def test_pourcent_ne_divise_pas_par_zero_et_plafonne_a_cent(job, attendu):
    """`null` fait passer la barre en indetermine ; un nombre la remplit.

    Un total a zero donnerait Infinity ou NaN, affiche tel quel dans la page. Et un
    fait superieur au total (reprise apres interruption) depasserait la barre.
    """
    assert _executer(f"return opt.methods.pourcent({json.dumps(job)});") == attendu


@sans_node
@pytest.mark.parametrize("job", [{"variantes": []}, {}])
def test_un_job_sans_variante_retenue_n_avertit_de_rien(job):
    """`retenueDe` rend null, et avertissements doit s'arreter la plutot que de lire
    des champs sur null — ce qui viderait la page entiere, Vue stoppant le rendu."""
    assert _executer(f"""
      const ctx = Object.create(opt.methods);
      return opt.methods.avertissements.call(ctx, {json.dumps(job)});
    """) == []


@sans_node
def test_un_statut_inconnu_s_affiche_tel_quel_sans_casser():
    """Un statut ajoute cote serveur et pas encore traduit ici doit se voir, pas
    disparaitre : une case vide dans l'historique ne se diagnostique pas."""
    resultat = _executer("""
      return { libelle: opt.methods.libelle('statut_invente'),
               classe: opt.methods.classeEtat('statut_invente') };
    """)
    assert resultat["libelle"] == "statut_invente"
    assert resultat["classe"] == ""
