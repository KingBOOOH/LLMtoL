"""Regles metier de l'API, SANS dependance a FastAPI ni a la base.

Pourquoi un module a part : fastapi n'est installe que dans le conteneur, la chaine de
generation tourne sur l'hote. Un test qui voudrait importer server/app.py echouerait
donc sur l'hote, et les regles d'enchainement des etats — celles qui decident si on
peut relancer ou regenerer un essai — ne seraient jamais verifiees nulle part. Elles
vivent ici, en fonctions pures, et server/app.py se contente de traduire leur reponse
en code HTTP.
"""


# RECOPIE de src.dsl_schema.BBOX_MAX, et surtout PAS importee : l'image de l'API ne
# contient que server/ (voir Dockerfile.api) et n'a ni src/ ni numpy. Un import ici
# ferait planter le conteneur au demarrage, pas au premier appel.
#
# La duplication est donc imposee par le deploiement, pas choisie. Ce qui la rend sure
# est un test : tests/test_server.py verifie que les deux valeurs sont egales, et
# echoue des qu'elles divergent. La verification tourne sur l'hote, ou les deux modules
# sont importables.
BBOX_MAX = [48, 48, 60]

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


def verifier_bbox_max(valeur):
    """Retourne ([X, Y, Z], erreur). None en entree = aucune borne exacte demandee.

    Pourquoi cette option existe a cote des tailles nommees : leur plafond est CUBIQUE,
    et un objet elance n'y tient pas. Un phare demande en « petite » plafonne a
    16x16x20 et sort en gateau ; 10x10x48 dit etroit ET haut.

    Accepte une liste [X, Y, Z] ou la chaine « 10x10x48 » que l'interface envoie.

    Refuse au depot plutot que de laisser l'agent echouer : la personne est encore
    devant son ecran, et un job cree ici ne sera traite que plus tard, ailleurs.
    """
    if valeur is None or valeur == "":
        return None, None

    if isinstance(valeur, str):
        morceaux = valeur.lower().replace("*", "x").replace(" ", "").split("x")
        if len(morceaux) != 3:
            return None, "La taille exacte s'écrit XxYxZ, par exemple 10x10x48."
        valeur = morceaux

    if not isinstance(valeur, (list, tuple)) or len(valeur) != 3:
        return None, "La taille exacte demande trois nombres : largeur, profondeur, hauteur."
    try:
        bornes = [int(v) for v in valeur]
    except (TypeError, ValueError):
        return None, "La taille exacte demande trois nombres entiers."

    if any(v < 1 or v > m for v, m in zip(bornes, BBOX_MAX)):
        return None, (f"Taille exacte hors limites : chaque valeur doit aller de 1 à "
                      f"{BBOX_MAX[0]} (X), {BBOX_MAX[1]} (Y), {BBOX_MAX[2]} (Z).")
    return bornes, None


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


# --- Avis de relecture ------------------------------------------------------------

# Borne du commentaire libre. Pas une mesure de securite — la colonne est un `text`
# et Postgres n'en souffrirait pas — mais une borne de bon sens sur une zone de
# saisie : au-dela, ce n'est plus un avis, c'est un rapport, et sa place est dans
# docs/mesures.md.
COMMENTAIRE_MAX = 2000

# Verdicts possibles, DU MEILLEUR AU PIRE — l'ordre est signifiant et sert au
# classement, pas seulement a l'affichage.
#
# Pourquoi trois et pas deux : un modele LEGO n'est pas bon ou mauvais, il ressemble
# plus ou moins. « La silhouette est bonne mais il manque les roues » n'est ni un oui
# ni un non, et le forcer dans un binaire perd l'information la plus courante.
#
# Trois valeurs nommees, et NON une note : une echelle numerique saisie a la main
# derive d'une session a l'autre (ce que « 6 » veut dire aujourd'hui n'est pas ce
# qu'il voudra dire dans un mois), trois etiquettes non. Le projet a deja mesure que
# le bareme n'est pas le levier.
#
# Benefice de mesure, et c'est le vrai gain : un verdict GRADUE rend la correlation
# de rang calculable sur une variante seule. Un binaire ne donne que deux rangs et
# obligerait a ne comparer que des paires.
VERDICTS = ("oui", "partiel", "non")


def verifier_avis(payload):
    """Retourne ((correspond, commentaire), erreur). L'entree vient de l'exterieur.

    `correspond` vaut l'un de VERDICTS, ou None quand on ne veut laisser qu'un
    commentaire sans trancher. Rien d'autre, et surtout pas un booleen : « oui » et
    « partiel » seraient tous deux vrais pour un `bool()` naif.

    Un avis vide des DEUX cotes est refuse. Il n'apporte rien, et comme un nouvel
    avis ecrase le precedent, l'accepter effacerait un vrai avis sur un clic de
    travers.

    « partiel » EXIGE un commentaire, les deux autres non. C'est la seule contrainte
    asymetrique, et elle se justifie : « partiellement » sans precision ne dit rien
    d'exploitable — partiellement en quoi ? — alors que « oui » et « non » se
    suffisent a eux-memes. Exiger un commentaire partout couterait du volume de
    relecture, qui est la ressource rare.
    """
    if not isinstance(payload, dict):
        return None, "Avis illisible."

    correspond = payload.get("correspond")
    if correspond is not None and correspond not in VERDICTS:
        return None, ("correspond doit valoir « " + " », « ".join(VERDICTS)
                      + " », ou être absent.")

    commentaire = payload.get("commentaire")
    if commentaire is None:
        commentaire = ""
    if not isinstance(commentaire, str):
        return None, "Le commentaire doit être du texte."
    commentaire = commentaire.strip()
    if len(commentaire) > COMMENTAIRE_MAX:
        return None, (f"Commentaire trop long : {len(commentaire)} caractères pour "
                      f"{COMMENTAIRE_MAX} au maximum.")

    if correspond is None and not commentaire:
        return None, ("Un avis vide n'apporte rien : dis si ça correspond, ou écris "
                      "en quoi.")

    if correspond == "partiel" and not commentaire:
        return None, ("« Partiellement » demande une précision : qu'est-ce qui "
                      "correspond, et qu'est-ce qui ne correspond pas ?")

    return (correspond, commentaire or None), None
