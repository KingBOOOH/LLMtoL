"""Detection du piege LM Studio (reglages du modele reinjectes en fallback).

Tout est teste sur detect_default_override, qui est une fonction pure : aucun appel
reseau, donc le test tourne meme LM Studio eteint. C'est voulu — un test qui aurait
besoin du serveur ne serait jamais lance au bon moment.
"""


from tools.check_env import detect_default_override


def test_prose_normale_ne_declenche_rien():
    content = ("LEGO bricks are small interlocking plastic blocks. They connect with "
               "studs on top and matching sockets underneath, which is what gives the "
               "system its clutch power.")
    assert detect_default_override(content) is None


def test_prose_mentionnant_le_mot_cube_ne_declenche_rien():
    """Faux positif a eviter : une prose sur les briques peut parler de cubes.

    La detection cherche la CLE JSON "action", pas le mot action, precisement pour
    ne pas bloquer un run sur une phrase anodine.
    """
    content = "A brick is roughly cube shaped, and putting one into action is simple."
    assert detect_default_override(content) is None


def test_schema_v0_detecte():
    content = '[{"action": "create", "object": "cube", "position": [0, 0, 0]}]'
    motif = detect_default_override(content)
    assert motif is not None
    # Le JSON valide est reconnu avant la signature v0 : les deux sont des motifs
    # acceptables, on verifie seulement qu'un motif est remonte.
    assert "JSON" in motif or "action/cube/rectangle" in motif


def test_json_quelconque_sur_une_demande_de_prose_detecte():
    assert detect_default_override('{"paragraph": "bricks are nice"}') is not None


def test_reponse_vide_detectee():
    assert detect_default_override("") is not None
    assert detect_default_override(None) is not None
    assert detect_default_override("   \n  ") is not None


def test_texte_commencant_par_une_accolade_sans_etre_du_json():
    """Une prose qui demarre par une accolade n'est pas du JSON : ne pas bloquer."""
    content = "{ this is not json at all, just a stray brace followed by prose about bricks."
    assert detect_default_override(content) is None


def test_signature_v0_sans_guillemets_ne_declenche_pas():
    """Sans guillemets ce ne sont pas des cles JSON, donc pas la signature du piege."""
    content = "The action of stacking a cube on a rectangle is the basis of every build."
    assert detect_default_override(content) is None
