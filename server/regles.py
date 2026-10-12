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


# --- Avancement lisible -----------------------------------------------------------
#
# L'agent ecrit son avancement en texte libre dans jobs.etape. C'etait suffisant pour
# une ligne d'etat, pas pour une barre d'etapes : l'interface ne pouvait pas savoir OU
# l'on en est dans la suite des phases, seulement quoi afficher. D'ou cette traduction
# en identifiants stables, en UN SEUL endroit, cote serveur.
#
# Les cles sont les chaines ecrites par agent.py et server/app.py. Un test verifie
# qu'aucune d'elles n'est oubliee : ajouter une etape sans la declarer ici ferait
# retomber la barre dans l'inconnu, sans erreur visible.
PHASES_PAR_ETAPE = {
    "demarrage": "design",
    "design": "design",
    "rendu des variantes": "rendu_propositions",
    "rendu du modele": "rendu_propositions",
    "notice": "notice",
    "notice et nomenclature": "notice",
    "nomenclature et etapes": "notice",
    "regeneration": "notice",
    "rendu des etapes": "rendu_notice",
    "notice PDF": "pdf",
    "reprise apres interruption": "file",
}

# Ordre d'affichage. `propositions` est un marqueur : l'interface le remplace par une
# etape par proposition demandee, parce que c'est la partie longue et que voir
# "proposition 2 sur 4" repond a la seule question qu'on se pose en attendant.
ORDRE_DES_PHASES = [
    "file", "design", "rendu_propositions", "choix", "notice", "rendu_notice", "pdf",
]

PHASE_PAR_STATUT = {
    "en_attente": "file",
    "a_choisir": "choix",
    "terminee": "fini",
    "echec": "echec",
}


def phase_de(status, etape=None):
    """Identifiant stable de la phase en cours, pour l'interface.

    Le STATUT prime sur l'etape : un job en attente n'a pas encore commence, meme s'il
    porte l'etape d'une tentative precedente, et un job termine ne doit pas afficher
    la derniere etape traversee comme si elle etait en cours.
    """
    if status in PHASE_PAR_STATUT:
        return PHASE_PAR_STATUT[status]
    return PHASES_PAR_ETAPE.get(etape or "", "design")


def variante_vignette(job):
    """Variante a montrer en vignette d'historique, ou None.

    La variante retenue d'abord ; sinon la PREMIERE qui a un rendu. Sans ce repli,
    une demande dont la premiere proposition a echoue restait une case grise dans
    l'historique, alors que trois autres propositions existaient et etaient rendues.
    """
    retenue = next((v for v in job.variantes if v.is_chosen), None)
    if retenue is not None and retenue.render_dir:
        return retenue
    return next((v for v in job.variantes if v.render_dir), None)


def propositions_manquantes(job):
    """Combien de propositions demandees n'ont pas abouti.

    Une generation qui echoue n'est pas rare — le validateur rejette ce qui ne tient
    pas debout — et l'utilisateur se retrouvait avec moins de choix que demande, sans
    recours autre que tout relancer.
    """
    abouties = sum(1 for v in job.variantes if v.design_json is not None)
    return max(0, job.n_variantes - abouties)


def refus_de_completion(job):
    """Pourquoi ce job ne peut pas recevoir de propositions supplementaires, ou None.

    Volontairement limite a `a_choisir` : avant, les propositions sont encore en
    cours de production ; apres, le choix est fait et en ajouter n'aurait plus de sens.
    """
    if job.status != "a_choisir":
        return ("Les propositions supplémentaires ne s'ajoutent qu'au moment du choix.")
    if propositions_manquantes(job) == 0:
        return "Toutes les propositions demandées ont abouti."
    return None
