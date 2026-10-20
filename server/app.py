"""API LLMtoL + service de l'interface. Tourne en conteneur, NE GENERE RIEN.

Une generation a besoin de Blender et de la bibliotheque LDraw, qui vivent sur l'hote :
un conteneur Linux ne peut pas executer blender.exe. L'API se limite donc a deposer des
jobs en base et a lire ce que l'agent (agent.py, cote hote) y a ecrit. La base EST la
file d'attente — pas de Celery, pas de Redis : une seule generation tourne a la fois de
toute facon, LM Studio etant mono-modele sur CPU.
"""

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from server.db import Job, Session, Variante, agent_actif, assurer_schema, racine_de
from server.regles import (
    ORDRE_DES_PHASES,
    phase_de,
    propositions_manquantes,
    refus_de_completion,
    refus_de_regeneration,
    refus_de_relance,
    variante_vignette,
    verifier_avis,
    verifier_bbox_max,
    verifier_n_variantes,
)

# Racine des sorties, montee en LECTURE SEULE depuis l'hote (voir docker-compose).
OUTPUTS_ROOT = os.path.abspath(os.environ.get("OUTPUTS_ROOT", "/outputs"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Liste fermee : le nom de vue vient de l'URL, donc de l'exterieur. Un `vue` libre
# permettrait de composer un chemin arbitraire.
VUES = {"iso", "top", "front", "right", "final"}
FICHIER_DE_VUE = {"iso": "render_iso.png", "top": "render_top.png",
                  "front": "render_front.png", "right": "render_right.png",
                  "final": "final.png"}

# Duplique volontairement src.dsl_schema.TAILLES : le conteneur api ne contient que
# server/, pas src/ (il n'a ni Blender ni la chaine de generation). La source de verite
# reste src/dsl_schema.py, qui porte les bornes reelles ; ici on ne valide que le nom.
# Si une taille y est ajoutee, l'ajouter aussi ici — un test le rappellerait, mais il
# ne peut pas tourner dans le conteneur.
TAILLES = {"petite", "moyenne", "grande"}

@asynccontextmanager
async def lifespan(_app):
    try:
        assurer_schema()
    except Exception as e:  # noqa: BLE001 - frontiere de demarrage
        # Une base momentanement absente ne doit pas empecher l'API de demarrer :
        # elle repond deja en erreur requete par requete dans ce cas. Mais on le DIT,
        # sinon une colonne manquante se manifesterait plus tard par un 500 opaque.
        print(f"[schema] mise a jour impossible : {e}", flush=True)
    yield


app = FastAPI(title="LLMtoL", docs_url="/api/docs", openapi_url="/api/openapi.json",
              lifespan=lifespan)


def _variante_json(v):
    """Projette une variante pour l'interface. N'expose jamais de chemin absolu."""
    return {
        "id": str(v.id),
        "indice": v.indice,
        "name": v.name,
        "bbox": v.bbox,
        "n_pieces": v.n_pieces,
        "n_references": v.n_references,
        "score": float(v.score) if v.score is not None else None,
        "n_components": v.n_components,
        "z_min": v.z_min,
        "n_floating": v.n_floating,
        "fill_rate": float(v.fill_rate) if v.fill_rate is not None else None,
        "repairs_used": v.repairs_used,
        "duration_s": float(v.duration_s) if v.duration_s is not None else None,
        "is_chosen": v.is_chosen,
        "is_rejected": v.is_rejected,
        "correspond": v.correspond,
        "commentaire": v.commentaire,
        "avis_at": v.avis_at.isoformat() if v.avis_at else None,
        "a_un_rendu": bool(v.render_dir),
        "a_un_pdf": v.pdf is not None,
        "error_message": v.error_message,
    }


def _job_json(job, avec_variantes=True):
    retenue = next((v for v in job.variantes if v.is_chosen), None)
    vignette = variante_vignette(job)
    data = {
        "id": str(job.id),
        "prompt": job.prompt,
        # La racine regroupe les essais successifs d'une meme demande.
        # L'interface compte les essais elle-meme : elle a deja la liste
        # complete sous la main, inutile de la lui renvoyer numerotee.
        "origine_id": str(job.origine_id) if job.origine_id else None,
        "racine_id": str(racine_de(job)),
        "taille": job.taille,
        "bbox_max": job.bbox_max,
        "n_variantes": job.n_variantes,
        "status": job.status,
        "etape": job.etape,
        "etape_fait": job.etape_fait,
        "etape_total": job.etape_total,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error_message": job.error_message,
        # Phase stable, ordre des phases : l'interface dessine sa barre d'etapes a
        # partir de ca, sans avoir a interpreter le texte francais de `etape`.
        "phase": phase_de(job.status, job.etape),
        "ordre_phases": ORDRE_DES_PHASES,
        "propositions_manquantes": propositions_manquantes(job),
        "variante_retenue": str(retenue.id) if retenue else None,
        # Vignette d'historique : la retenue, sinon la premiere proposition rendue.
        # Sans ce repli, une demande dont la premiere proposition a echoue restait
        # une case grise alors que les suivantes existaient.
        "vignette": str(vignette.id) if vignette else None,
        "n_pieces": retenue.n_pieces if retenue else None,
        "a_un_pdf": bool(retenue and retenue.pdf is not None),
    }
    if avec_variantes:
        data["variantes"] = [_variante_json(v) for v in job.variantes]
    return data


@app.get("/api/status")
def status():
    """Etat de la pile : un agent est-il la pour traiter les jobs ?

    L'interface s'en sert pour desactiver le bouton plutot que de laisser l'utilisateur
    deposer un job que personne ne prendra.
    """
    with Session() as s:
        nom = agent_actif(s)
        en_attente = s.scalar(select(Job).where(Job.status == "en_attente").limit(1))
        return {"agent": nom, "agent_actif": nom is not None,
                "jobs_en_attente": en_attente is not None}


@app.post("/api/jobs", status_code=201)
def creer_job(payload: dict = Body(...)):
    """Depose une soumission. Repond immediatement : c'est l'agent qui travaille."""
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "Décris ce que tu veux construire.")
    if len(prompt) > 2000:
        raise HTTPException(400, "Description trop longue (2000 caractères maximum).")

    taille = payload.get("taille") or None
    if taille is not None and taille not in TAILLES:
        raise HTTPException(400, f"Taille inconnue. Valeurs possibles : {sorted(TAILLES)}")

    n, erreur = verifier_n_variantes(payload.get("n_variantes"), 4)
    if erreur:
        raise HTTPException(400, erreur)

    # Borne exacte, pour les formes elancees que les tailles nommees ne savent pas
    # exprimer. Validee par la MEME fonction que le generateur, pas par une regle
    # recopiee ici.
    bbox_max, erreur = verifier_bbox_max(payload.get("bbox_max"))
    if erreur:
        raise HTTPException(400, erreur)

    with Session() as s:
        if agent_actif(s) is None:
            # Refuser maintenant plutot que de creer un job qui ne partira jamais :
            # l'API est incapable de generer elle-meme.
            raise HTTPException(
                503, "Aucun agent de génération actif. Lance `python agent.py` sur la "
                     "machine où sont installés Blender et LM Studio.")
        job = Job(prompt=prompt, taille=taille, n_variantes=n, bbox_max=bbox_max)
        s.add(job)
        s.commit()
        return _job_json(job)


@app.get("/api/jobs")
def lister_jobs(limit: int = 30, offset: int = 0):
    """Historique, le plus recent d'abord. Les echecs y figurent aussi."""
    limit = max(1, min(limit, 100))
    with Session() as s:
        jobs = s.scalars(
            select(Job).order_by(Job.created_at.desc()).limit(limit).offset(max(0, offset))
        ).all()
        return {"jobs": [_job_json(j) for j in jobs]}


@app.get("/api/jobs/{job_id}")
def lire_job(job_id: str):
    with Session() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job introuvable.")
        return _job_json(job)


@app.post("/api/jobs/{job_id}/choose")
def choisir(job_id: str, payload: dict = Body(...)):
    """Enregistre le choix de l'utilisateur et relance l'agent pour la finition.

    Les variantes non retenues sont REDUITES, pas supprimees : on garde design_json,
    les metriques et la seed (~2 ko), on efface le PDF. C'est la seule matiere pour
    comprendre plus tard ce que le modele rate. L'agent effacera leurs rendus sur
    disque, l'API n'ecrit pas dans outputs/ (montage en lecture seule).
    """
    variante_id = payload.get("variante_id")
    if not variante_id:
        raise HTTPException(400, "variante_id manquant.")

    with Session() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job introuvable.")
        if job.status != "a_choisir":
            raise HTTPException(409, f"Ce job n'attend pas de choix (status={job.status}).")

        choisie = next((v for v in job.variantes if str(v.id) == str(variante_id)), None)
        if choisie is None:
            raise HTTPException(404, "Cette variante n'appartient pas à ce job.")
        if choisie.design_json is None:
            raise HTTPException(409, "Cette variante n'a pas abouti, elle ne peut pas "
                                     "être retenue.")

        for v in job.variantes:
            v.is_chosen = v.id == choisie.id
            v.is_rejected = not v.is_chosen
            if v.is_rejected:
                v.pdf = None
        job.status = "en_finition"
        job.etape = "notice et nomenclature"
        s.commit()
        return _job_json(job)


@app.post("/api/jobs/{job_id}/relancer", status_code=201)
def relancer(job_id: str, payload: dict = Body(default={})):
    """Nouvel essai sur LE MEME prompt. Coute N generations.

    Cree un job distinct, rattache a l'essai d'origine : l'ancien n'est jamais
    ecrase, ses sorties vivent dans son propre dossier `outputs/job_<id>/`. Le
    rattachement n'est pas qu'un confort d'affichage — il rend enfin mesurable la
    VARIANCE du modele sur une demande donnee, ce que le projet n'a jamais su faire.
    """
    with Session() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job introuvable.")
        refus = refus_de_relance(job)
        if refus:
            raise HTTPException(409, refus)
        if agent_actif(s) is None:
            raise HTTPException(
                503, "Aucun agent de génération actif. Lance `python agent.py` sur la "
                     "machine où sont installés Blender et LM Studio.")

        n, erreur = verifier_n_variantes(payload.get("n_variantes"), job.n_variantes)
        if erreur:
            raise HTTPException(400, erreur)
        nouveau = Job(prompt=job.prompt, taille=job.taille, n_variantes=n,
                      bbox_max=job.bbox_max,
                      # Toujours la racine : une relance de relance reste au meme
                      # niveau, sinon regrouper demanderait de remonter un arbre.
                      origine_id=racine_de(job))
        s.add(nouveau)
        s.commit()
        return _job_json(nouveau)


@app.post("/api/jobs/{job_id}/regenerer")
def regenerer(job_id: str):
    """Refait rendus, notice et PDF depuis `design_json`. AUCUN appel LLM.

    Toute la chaine en aval du design est deterministe : remettre le job en
    `en_finition` suffit a la rejouer entierement. Utile apres un changement de
    moteur de rendu ou de mise en page de notice, ou si un fichier a ete efface.
    L'agent reconstruit ce qui manque sur disque (voir agent.chemins_manquants).
    """
    with Session() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job introuvable.")
        refus = refus_de_regeneration(job)
        if refus:
            raise HTTPException(409, refus)

        job.status = "en_finition"
        job.etape = "regeneration"
        job.error_message = None
        job.finished_at = None
        s.commit()
        return _job_json(job)


@app.post("/api/jobs/{job_id}/completer")
def completer(job_id: str):
    """Reproduit les propositions qui n'ont pas abouti, dans LE MEME job.

    Une generation ratee n'est pas rare : le validateur rejette ce qui ne tient pas
    debout. L'utilisateur se retrouvait alors avec moins de choix que demande, sans
    autre recours que de tout relancer — y compris les propositions reussies, qu'il
    aurait fallu re-rendre pour rien.

    Le job repasse en `en_attente` : l'agent ne reproduit que les indices manquants
    (agent.indices_a_produire), les propositions deja abouties sont intactes.
    """
    with Session() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job introuvable.")
        refus = refus_de_completion(job)
        if refus:
            raise HTTPException(409, refus)
        if agent_actif(s) is None:
            raise HTTPException(
                503, "Aucun agent de génération actif. Lance `python agent.py` sur la "
                     "machine où sont installés Blender et LM Studio.")

        job.status = "en_attente"
        job.etape = None
        job.claimed_at = None
        s.commit()
        return _job_json(job)


@app.delete("/api/jobs/{job_id}", status_code=204)
def supprimer_job(job_id: str):
    """Supprime un job et ses variantes (les essais rates encombrent vite)."""
    with Session() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job introuvable.")
        s.delete(job)
        s.commit()
    return Response(status_code=204)


@app.get("/api/relecture")
def relecture(limit: int = 20, offset: int = 0, sans_avis: bool = False):
    """Les générations passées, pour dire si elles correspondent à la demande.

    N'EXPOSE NI LE SCORE NI LE NOMBRE DE PIÈCES, et ce n'est pas un oubli. Ces deux
    chiffres sont les références triviales contre lesquelles une métrique sémantique
    devra se mesurer : affichés ici, ils influenceraient l'avis, et on ne mesurerait
    plus que la capacité du score à se prédire lui-même. Le score reste consultable
    sur le panneau principal, après le choix.

    Seules les variantes qui ont abouti sont listées : sans design, il n'y a rien à
    regarder. `a_un_rendu` est faux pour les variantes écartées, dont l'agent efface
    les images — leur `design_json` suffit à les reconstruire en lot avec
    tools/render_batch.py, mais l'API ne déclenche jamais Blender.
    """
    limit = max(1, min(limit, 100))
    with Session() as s:
        stmt = select(Job).order_by(Job.created_at.desc())
        if sans_avis:
            # Filtre en base et non apres pagination : filtrer la page deja
            # decoupee rendrait des pages a moitie vides et sauterait des jobs.
            stmt = stmt.where(Job.id.in_(
                select(Variante.job_id).where(Variante.design_json.is_not(None),
                                              Variante.avis_at.is_(None))))
        lignes = [
            {
                "job_id": str(j.id),
                "prompt": j.prompt,
                "taille": j.taille,
                "bbox_max": j.bbox_max,
                "status": j.status,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "variantes": [
                    {
                        "id": str(v.id),
                        "indice": v.indice,
                        "name": v.name,
                        "is_chosen": v.is_chosen,
                        "a_un_rendu": bool(v.render_dir),
                        "correspond": v.correspond,
                        "commentaire": v.commentaire,
                        "avis_at": v.avis_at.isoformat() if v.avis_at else None,
                    }
                    for v in j.variantes if v.design_json is not None
                ],
            }
            for j in s.scalars(stmt.limit(limit).offset(offset)).all()
        ]
    return [ligne for ligne in lignes if ligne["variantes"]]


@app.post("/api/variantes/{variante_id}/avis")
def enregistrer_avis(variante_id: str, payload: dict = Body(...)):
    """Enregistre l'avis de relecture. Un nouvel avis écrase le précédent.

    Idempotent à dessein : on relit, on se corrige, et c'est le dernier avis qui
    compte. `avis_at` bouge à chaque écriture, donc on sait toujours ce qui a été
    relu et quand.
    """
    avis, erreur = verifier_avis(payload)
    if erreur:
        raise HTTPException(400, erreur)
    correspond, commentaire = avis

    with Session() as s:
        v = s.get(Variante, variante_id)
        if v is None:
            raise HTTPException(404, "Variante introuvable.")
        v.correspond = correspond
        v.commentaire = commentaire
        v.avis_at = datetime.now(UTC)
        s.commit()
        return _variante_json(v)


def _fichier_de_sortie(relatif, nom_fichier):
    """Resout un fichier sous OUTPUTS_ROOT, en refusant de sortir de la racine.

    `relatif` vient de la base, donc d'un agent : on le traite malgre tout comme une
    entree non fiable. Extrait de la route de rendu, qui faisait deja ce controle,
    pour que la liste d'achat ne le reimplemente pas a moitie.
    """
    chemin = os.path.abspath(os.path.join(OUTPUTS_ROOT, relatif, nom_fichier))
    if os.path.commonpath([chemin, OUTPUTS_ROOT]) != OUTPUTS_ROOT:
        raise HTTPException(403, "Chemin hors de la racine des sorties.")
    return chemin


@app.get("/api/variantes/{variante_id}/render/{vue}")
def rendu(variante_id: str, vue: str):
    """Sert une image depuis le montage lecture seule."""
    if vue not in VUES:
        raise HTTPException(404, "Vue inconnue.")
    with Session() as s:
        v = s.get(Variante, variante_id)
        if v is None or not v.render_dir:
            raise HTTPException(404, "Aucun rendu pour cette variante.")
        relatif = v.render_dir

    chemin = _fichier_de_sortie(relatif, FICHIER_DE_VUE[vue])
    if not os.path.isfile(chemin):
        raise HTTPException(404, "Image absente du disque.")
    return FileResponse(chemin, media_type="image/png")


@app.get("/api/jobs/{job_id}/pdf")
def pdf(job_id: str):
    """Sert la notice PDF de la variante retenue."""
    with Session() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job introuvable.")
        retenue = next((v for v in job.variantes if v.is_chosen), None)
        if retenue is None or retenue.pdf is None:
            raise HTTPException(404, "Pas encore de PDF pour ce modèle.")
        nom = (retenue.name or "modele").replace('"', "")
        return Response(
            content=bytes(retenue.pdf), media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="notice_{nom}.pdf"'})


@app.get("/api/jobs/{job_id}/wanted-list")
def wanted_list(job_id: str):
    """Sert la liste d'achat BrickLink de la variante retenue.

    Produite par l'agent a cote de la nomenclature (voir pipeline.preparer_notice) :
    l'API ne la calcule pas, elle n'ecrit jamais dans outputs/ et ne contient meme
    pas src/.
    """
    with Session() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job introuvable.")
        retenue = next((v for v in job.variantes if v.is_chosen), None)
        if retenue is None or not retenue.render_dir:
            raise HTTPException(404, "Aucune variante retenue pour ce job.")
        relatif, nom = retenue.render_dir, (retenue.name or "modele")

    chemin = _fichier_de_sortie(relatif, "wanted_list.xml")
    if not os.path.isfile(chemin):
        raise HTTPException(
            404, "Pas encore de liste d'achat pour ce modèle. Elle est produite avec "
                 "la notice : relance « Refaire la notice » si le modèle est ancien.")
    return FileResponse(chemin, media_type="application/xml",
                        filename=f"wanted_list_{nom}.xml")


@app.get("/api/jobs/{job_id}/ldr")
def modele_ldr(job_id: str):
    """Sert le model.ldr de la variante retenue.

    C'est le format que le selecteur de fichier de BrickLink accepte (.bsx, .ldr,
    .lxf, .io), la ou le XML de wanted list ne passe QUE par la zone de collage de la
    meme page. Deux chemins, deux usages — et le fichier existe deja, produit a chaque
    generation : il n'y a rien a calculer ici.

    A SAVOIR : par cette voie, c'est BrickLink qui fait la correspondance entre
    numeros LDraw et numeros de boutique, pas src/catalog.py. Notre table verifiee
    (et l'avertissement sur les couleurs qui ne sont plus produites) ne s'applique
    donc qu'au XML.
    """
    with Session() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job introuvable.")
        retenue = next((v for v in job.variantes if v.is_chosen), None)
        if retenue is None or not retenue.render_dir:
            raise HTTPException(404, "Aucune variante retenue pour ce job.")
        relatif, nom = retenue.render_dir, (retenue.name or "modele")

    chemin = _fichier_de_sortie(relatif, "model.ldr")
    if not os.path.isfile(chemin):
        raise HTTPException(404, "Le fichier du modèle est absent du disque.")
    return FileResponse(chemin, media_type="text/plain", filename=f"{nom}.ldr")


@app.get("/api/jobs/{job_id}/design")
def design(job_id: str):
    """Le design DSL de la variante retenue : l'archive qui regenere tout le reste."""
    with Session() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job introuvable.")
        retenue = next((v for v in job.variantes if v.is_chosen), None)
        if retenue is None or retenue.design_json is None:
            raise HTTPException(404, "Aucun design retenu pour ce job.")
        return retenue.design_json


@app.get("/ui/relecture", include_in_schema=False)
def ui_relecture():
    """Sert la planche de relecture sur une URL sans extension.

    StaticFiles ne rend une URL sans extension que pour l'index.html d'un DOSSIER :
    c'est pourquoi /ui/ marche. Obtenir /ui/relecture par le seul montage obligerait
    donc a enterrer la page dans server/static/relecture/index.html — un dossier par
    page, et un deuxieme fichier nomme index.html dans l'arborescence. Quatre lignes
    ici coutent moins, et le fichier reste nomme d'apres ce qu'il est.

    Declaree AVANT le montage : les routes sont essayees dans l'ordre d'ajout, et le
    montage /ui capterait sinon cette URL pour repondre 404. /ui/relecture.html
    continue de fonctionner, servi par le montage.
    """
    return FileResponse(os.path.join(STATIC_DIR, "relecture.html"),
                        media_type="text/html")


@app.get("/", include_in_schema=False)
def racine():
    return RedirectResponse("/ui/")


# Monte en dernier : une route montee a la racine capterait tout /api/*.
app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
