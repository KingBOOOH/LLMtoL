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
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
