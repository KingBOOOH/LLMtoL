"""Tests du juge visuel (US V2-01).

Ce qui est verifie ici, c'est ce que le PROJET construit : le payload envoye a LM
Studio et les fichiers ecrits. La note elle-meme vient du modele et n'est pas
simulee : aucun test ici ne pretend mesurer la qualite du juge — c'est le role de
tools/calibrer_juge.py, qui a besoin d'un vrai VLM.
"""

import base64
import json
import os

import pytest

import config
from src import juge_vlm
from src.llm_client import _contenu_user, _payload_pour_trace, call_llm

# PNG 1x1 valide, le plus petit possible : on teste l'encodage, pas l'image.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")


class FausseReponse:
    """Reponse HTTP minimale : transporte un contenu, ne l'invente pas."""

    status_code = 200

    def __init__(self, contenu):
        self.text = json.dumps({"choices": [{"message": {"content": contenu},
                                             "finish_reason": "stop"}], "usage": {}})

    def json(self):
        return json.loads(self.text)


@pytest.fixture
def image(tmp_path):
    chemin = tmp_path / "render_iso.png"
    chemin.write_bytes(PNG_1X1)
    return str(chemin)


# ---------- construction du message vision ----------

def test_sans_image_le_contenu_reste_une_chaine():
    """Le chemin existant ne doit pas changer de forme : planner et designer en vivent."""
    assert _contenu_user("bonjour", None) == "bonjour"
    assert _contenu_user("bonjour", []) == "bonjour"


def test_avec_image_le_contenu_devient_des_parties_avec_data_uri(image):
    parties = _contenu_user("Demande : un cube", [image])
    assert parties[0] == {"type": "text", "text": "Demande : un cube"}
    url = parties[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG_1X1


def test_la_trace_ne_recopie_pas_le_base64(image, tmp_path):
    """Sinon <tag>_request.json pese des centaines de ko par appel, pour rien."""
    payload = {"model": "vlm", "messages": [{"role": "system", "content": "s"},
                                            {"role": "user",
                                             "content": _contenu_user("u", [image])}]}
    trace = json.dumps(_payload_pour_trace(payload, "u", [image]))
    assert "base64," not in trace
    assert "render_iso.png" in trace


# ---------- le modele interroge ----------

def test_le_modele_du_juge_ne_touche_pas_a_celui_de_la_generation(monkeypatch, tmp_path, image):
    """Critere d'acceptation : interroger un second modele sans toucher au premier."""
    envoyes = []

    def faux_post(url, json=None, timeout=None):
        envoyes.append(json)
        return FausseReponse('{"note": 7, "raison": "un cube reconnaissable"}')

    monkeypatch.setattr("src.llm_client.requests.post", faux_post)
    monkeypatch.setattr(config, "LM_STUDIO_VLM_MODEL", "un-vlm")

    juge_vlm.noter("un cube", image, str(tmp_path))

    assert envoyes[0]["model"] == "un-vlm"
    assert config.LM_STUDIO_MODEL != "un-vlm", "le modele de generation reste intact"
    assert envoyes[0]["temperature"] == 0.0, "meme image, meme note d'un run a l'autre"


def test_un_appel_sans_model_garde_le_modele_de_generation(monkeypatch, tmp_path):
    envoyes = []

    def faux_post(url, json=None, timeout=None):
        envoyes.append(json)
        return FausseReponse("du texte")

    monkeypatch.setattr("src.llm_client.requests.post", faux_post)
    call_llm("s", "u", log_dir=str(tmp_path), tag="t")
    assert envoyes[0]["model"] == config.LM_STUDIO_MODEL


# ---------- sortie sur disque ----------

def test_la_note_est_ecrite_a_part_et_relisible(monkeypatch, tmp_path, image):
    """Fichier dedie : report.json est ecrit avant que le rendu existe."""
    monkeypatch.setattr(
        "src.llm_client.requests.post",
        lambda url, json=None, timeout=None: FausseReponse(
            '{"note": 3, "raison": "il manque les tours"}'))
    monkeypatch.setattr(config, "LM_STUDIO_VLM_MODEL", "un-vlm")

    sortie = juge_vlm.noter("un chateau", image, str(tmp_path))

    assert sortie["note"] == 3
    assert juge_vlm.note_existante(str(tmp_path)) == 3
    assert not os.path.exists(tmp_path / "report.json")


def test_une_reponse_hors_schema_est_une_erreur_claire(monkeypatch, tmp_path, image):
    """Un modele servi sans support du schema doit se voir, pas donner une note 0."""
    monkeypatch.setattr(
        "src.llm_client.requests.post",
        lambda url, json=None, timeout=None: FausseReponse("Je pense que c'est plutot bien."))
    monkeypatch.setattr(config, "LM_STUDIO_VLM_MODEL", "un-vlm")

    with pytest.raises(ValueError, match="inexploitable"):
        juge_vlm.noter("un cube", image, str(tmp_path))


def test_un_rendu_manquant_est_une_erreur_claire(tmp_path):
    with pytest.raises(FileNotFoundError, match="Rendu introuvable"):
        juge_vlm.noter("un cube", str(tmp_path / "absent.png"), str(tmp_path))


# ---------- optionnalite ----------

def test_le_juge_est_desactive_par_defaut(monkeypatch):
    """Sans modele configure, aucune generation ne doit dependre du juge."""
    monkeypatch.setattr(config, "LM_STUDIO_VLM_MODEL", "")
    assert juge_vlm.disponible() is False
    monkeypatch.setattr(config, "LM_STUDIO_VLM_MODEL", "un-vlm")
    assert juge_vlm.disponible() is True


def test_note_existante_vaut_none_sans_fichier(tmp_path):
    assert juge_vlm.note_existante(str(tmp_path)) is None


def test_le_juge_ne_lit_jamais_le_design(monkeypatch, tmp_path, image):
    """Un modele texte qui relit du JSON remplacerait src.validator, exact, par une
    opinion. Le juge ne doit voir que l'image et la demande."""
    envoyes = []
    monkeypatch.setattr(
        "src.llm_client.requests.post",
        lambda url, json=None, timeout=None: (
            envoyes.append(json), FausseReponse('{"note": 5, "raison": "x"}'))[1])
    monkeypatch.setattr(config, "LM_STUDIO_VLM_MODEL", "un-vlm")
    (tmp_path / "design.json").write_text('{"name": "piege"}', encoding="utf-8")

    juge_vlm.noter("un cube", image, str(tmp_path))

    assert "piege" not in json.dumps(envoyes[0]["messages"][0])
    assert '"op"' not in json.dumps(envoyes[0]["messages"])


# ---------- calibration ----------

def test_la_correlation_est_non_definie_si_le_juge_note_tout_pareil():
    """Le cas qu'on redoute vraiment : un juge qui met 5 partout. Il ne doit pas
    sortir un chiffre rassurant, il doit sortir None."""
    from tools.calibrer_juge import spearman

    assert spearman([0.1, 0.5, 0.9], [5, 5, 5]) is None
    assert spearman([0.5], [7]) is None


def test_la_correlation_de_rang_suit_l_ordre_pas_l_echelle():
    """Spearman et non Pearson : une note entiere 0-10 contre un IoU continu n'a
    aucune raison d'etre lineaire. Seul le classement compte."""
    from tools.calibrer_juge import spearman

    assert spearman([0.1, 0.4, 0.6, 0.9], [1, 4, 6, 9]) == 1.0
    assert spearman([0.1, 0.4, 0.6, 0.9], [2, 3, 8, 10]) == 1.0, "meme ordre, autre echelle"
    assert spearman([0.1, 0.4, 0.6, 0.9], [9, 6, 4, 1]) == -1.0


def test_la_collecte_ignore_les_dossiers_sans_rendu(tmp_path, monkeypatch):
    """Un run interrompu laisse toujours des dossiers incomplets : ils ne doivent ni
    faire echouer la calibration, ni compter comme des notes manquantes."""
    from tools import calibrer_juge

    testset = {"prim_cube": {"id": "prim_cube", "prompt": "un cube",
                             "reference": {"shape": "cube", "size": 4}}}
    complet = tmp_path / "v1prim" / "prim_cube" / "attempt_01"
    complet.mkdir(parents=True)
    (complet / "design.json").write_text("{}", encoding="utf-8")
    (complet / "render_iso.png").write_bytes(PNG_1X1)

    sans_rendu = tmp_path / "v1prim" / "prim_cube" / "attempt_02"
    sans_rendu.mkdir(parents=True)
    (sans_rendu / "design.json").write_text("{}", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    trouves = calibrer_juge.collecter("v1prim/*/attempt_*", testset)
    assert [os.path.basename(w) for _, w, _ in trouves] == ["attempt_01"]


def test_la_calibration_rapporte_les_deux_verites_terrain(tmp_path):
    """voxel_iou punit une rotation, le juge ne peut pas la voir : il regarde une image
    et le prompt du jeu de test n'impose aucune orientation. Correler contre le seul
    voxel_iou ferait porter au juge un defaut connu de la verite terrain — c'est ce qui
    se mesure sur prim_escalier (IoU 0,200, note du juge 9/10, forme exacte dans docs/guide-complet.md).
    La calibration doit donc rendre les deux, et l'oriente ne peut pas etre inferieur.
    """
    from tools.calibrer_juge import iou_du_dossier

    # Un pave 3x2x1 demande, construit tourne de 90 degres : meme forme, autre axe.
    (tmp_path / "design.json").write_text(json.dumps(
        {"name": "pave tourne", "bbox": [2, 3, 1],
         "ops": [{"op": "box", "from": [0, 0, 0], "to": [1, 2, 0], "color": "red"}]}),
        encoding="utf-8")

    iou, iou_or = iou_du_dossier(
        str(tmp_path), {"shape": "box", "params": {"nx": 3, "ny": 2, "nz": 1}})

    assert iou_or == 1.0, "la forme est la bonne, seule l'orientation differe"
    assert iou < iou_or, "voxel_iou punit la rotation, c'est justement le piege"


def test_la_collecte_ignore_les_formes_sans_verite_terrain(tmp_path, monkeypatch):
    """Sans reference analytique, pas d'IoU connu, donc rien a correler."""
    from tools import calibrer_juge

    d = tmp_path / "v1" / "bateau" / "attempt_01"
    d.mkdir(parents=True)
    (d / "design.json").write_text("{}", encoding="utf-8")
    (d / "render_iso.png").write_bytes(PNG_1X1)

    monkeypatch.chdir(tmp_path)
    assert calibrer_juge.collecter("v1/*/attempt_*", {"bateau": {"prompt": "un bateau"}}) == []


def test_resume_refuse_une_note_produite_par_un_autre_bareme(tmp_path):
    """Le piege : modifier le prompt puis relancer avec --resume melangerait deux
    baremes dans la meme correlation, sans que rien ne le signale."""
    (tmp_path / juge_vlm.NOM_FICHIER).write_text(
        json.dumps({"note": 8, "bareme": "ancien"}), encoding="utf-8")

    assert juge_vlm.note_existante(str(tmp_path)) == 8, "sans exigence, on relit"
    assert juge_vlm.note_existante(str(tmp_path), "ancien") == 8
    assert juge_vlm.note_existante(str(tmp_path), juge_vlm.EMPREINTE_BAREME) is None


def test_une_note_sans_bareme_est_rejouee(tmp_path):
    """Note anterieure a ce champ : on ignore d'ou elle vient, donc on la refait."""
    (tmp_path / juge_vlm.NOM_FICHIER).write_text(json.dumps({"note": 8}), encoding="utf-8")
    assert juge_vlm.note_existante(str(tmp_path), juge_vlm.EMPREINTE_BAREME) is None


def test_la_reprise_garde_la_raison_et_la_duree(tmp_path, monkeypatch):
    """Le defaut constate : --resume ne relisait que la note, et reecrivait le CSV avec
    raison et duree_s vides. Les justifications du run de calibration ont ete effacees
    comme ca, et il a fallu aller les rechercher dans les juge_vlm.json un par un.
    """
    from tools import calibrer_juge

    work = tmp_path / "v1prim" / "prim_cube" / "attempt_01"
    work.mkdir(parents=True)
    (work / "design.json").write_text(json.dumps(
        {"name": "cube", "bbox": [2, 2, 2],
         "ops": [{"op": "box", "from": [0, 0, 0], "to": [1, 1, 1], "color": "red"}]}),
        encoding="utf-8")
    (work / "render_iso.png").write_bytes(PNG_1X1)
    (work / juge_vlm.NOM_FICHIER).write_text(json.dumps(
        {"note": 7, "raison": "un cube reconnaissable", "duree_s": 9.4,
         "bareme": juge_vlm.EMPREINTE_BAREME}), encoding="utf-8")

    # Aucun appel au modele ne doit avoir lieu : tout est deja sur disque.
    monkeypatch.setattr(juge_vlm, "noter",
                        lambda *a, **k: pytest.fail("la reprise a rappele le modele"))
    # _bilan ecrit son CSV sous BASE_DIR : sans ca le test deposerait un fichier dans
    # eval/results/ du depot.
    monkeypatch.setattr(calibrer_juge, "BASE_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    lignes = calibrer_juge.calibrer(
        "v1prim/*/attempt_*",
        _testset_cube(tmp_path), "un-vlm", resume=True)

    assert lignes[0]["note_vlm"] == 7
    assert lignes[0]["raison"] == "un cube reconnaissable"
    assert lignes[0]["duree_s"] == 9.4


def _testset_cube(tmp_path):
    """Jeu de test minimal sur disque : calibrer() attend un chemin, pas un dict."""
    chemin = tmp_path / "testset.json"
    chemin.write_text(json.dumps(
        [{"id": "prim_cube", "prompt": "un cube",
          "reference": {"shape": "cube", "params": {"side": 2}}}]), encoding="utf-8")
    return str(chemin)
