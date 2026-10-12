"""Regles metier de l'API, SANS dependance a FastAPI ni a la base.

Pourquoi un module a part : fastapi n'est installe que dans le conteneur, la chaine de
generation tourne sur l'hote. Un test qui voudrait importer server/app.py echouerait
donc sur l'hote, et les regles d'enchainement des etats — celles qui decident si on
peut relancer ou regenerer un essai — ne seraient jamais verifiees nulle part. Elles
vivent ici, en fonctions pures, et server/app.py se contente de traduire leur reponse
en code HTTP.
"""

# Un job dans un de ces etats a encore quelque chose devant lui : ni relance ni
# regeneration ne doivent s'y superposer, sinon deux traitements se disputeraient le
# meme dossier de sortie.
EN_VOL = {"en_attente", "en_cours", "a_choisir", "en_finition"}

N_VARIANTES_MIN, N_VARIANTES_MAX = 1, 6


def refus_de_relance(job):
    """Pourquoi ce job ne peut pas etre relance, ou None s'il peut.

    Un ECHEC est relancable — c'est meme le cas d'usage principal, puisque la demande
    est alors restee sans resultat. Seul un essai encore en vol ne l'est pas.
    """
    if job.status in EN_VOL:
        return ("Cet essai n'est pas terminé. Attends qu'il finisse avant d'en "
                "relancer un autre.")
    return None


def refus_de_regeneration(job):
    """Pourquoi ce job ne peut pas etre regenere, ou None s'il peut.

    La regeneration repart du seul `design_json` de la variante retenue : elle exige
    donc un choix deja fait, et ne coute aucun appel au modele.
    """
    if job.status in EN_VOL:
        return "Ce modèle est déjà en travaux."
    retenue = next((v for v in job.variantes if v.is_chosen), None)
    if retenue is None:
        return "Aucune variante retenue : il n'y a rien à refaire."
    if retenue.design_json is None:
        return ("La variante retenue n'a pas de design enregistré, on ne peut pas la "
                "reconstruire.")
    return None


def verifier_n_variantes(valeur, defaut):
    """Retourne (n, erreur). `valeur` vient de l'exterieur, donc rien n'est suppose.

    Partage entre la soumission et la relance : deux chemins, une seule borne.
    """
    if valeur is None:
        return defaut, None
    try:
        n = int(valeur)
    except (TypeError, ValueError):
        return None, "n_variantes doit être un entier."
    if not N_VARIANTES_MIN <= n <= N_VARIANTES_MAX:
        return None, (f"n_variantes doit être entre {N_VARIANTES_MIN} et "
                      f"{N_VARIANTES_MAX}.")
    return n, None
