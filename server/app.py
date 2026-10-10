"""API LLMtoL + service de l'interface. Tourne en conteneur, NE GENERE RIEN.

Une generation a besoin de Blender et de la bibliotheque LDraw, qui vivent sur l'hote :
un conteneur Linux ne peut pas executer blender.exe. L'API se limite donc a deposer des
jobs en base et a lire ce que l'agent (agent.py, cote hote) y a ecrit. La base EST la
file d'attente — pas de Celery, pas de Redis : une seule generation tourne a la fois de
toute facon, LM Studio etant mono-modele sur CPU.
"""

import os

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from server.db import Job, Session, Variante, agent_actif

# Racine des sorties, montee en LECTURE SEULE depuis l'hote (voir docker-compose).
OUTPUTS_ROOT = os.path.abspath(os.environ.get("OUTPUTS_ROOT", "/outputs"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Liste fermee : le nom de vue vient de l'URL, donc de l'exterieur. Un `vue` libre
# permettrait de composer un chemin arbitraire.
VUES = {"iso", "top", "front", "right", "final"}
FICHIER_DE_VUE = {"iso": "render_iso.png", "top": "render_top.png",
                  "front": "render_front.png", "right": "render_right.png",
                  "final": "final.png"}

TAILLES = {"petite", "moyenne", "grande"}

app = FastAPI(title="LLMtoL", docs_url="/api/docs", openapi_url="/api/openapi.json")


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
        "a_un_rendu": bool(v.render_dir),
        "a_un_pdf": v.pdf is not None,
        "error_message": v.error_message,
    }


def _job_json(job, avec_variantes=True):
    retenue = next((v for v in job.variantes if v.is_chosen), None)
    data = {
        "id": str(job.id),
        "prompt": job.prompt,
        "taille": job.taille,
        "n_variantes": job.n_variantes,
        "status": job.status,
        "etape": job.etape,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error_message": job.error_message,
        "variante_retenue": str(retenue.id) if retenue else None,
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

    try:
        n = int(payload.get("n_variantes", 4))
    except (TypeError, ValueError):
        raise HTTPException(400, "n_variantes doit être un entier.")
    if not 1 <= n <= 6:
        raise HTTPException(400, "n_variantes doit être entre 1 et 6.")

    with Session() as s:
        if agent_actif(s) is None:
            # Refuser maintenant plutot que de creer un job qui ne partira jamais :
            # l'API est incapable de generer elle-meme.
            raise HTTPException(
                503, "Aucun agent de génération actif. Lance `python agent.py` sur la "
                     "machine où sont installés Blender et LM Studio.")
        job = Job(prompt=prompt, taille=taille, n_variantes=n)
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

    # render_dir vient de la base, donc d'un agent : on le traite malgre tout comme
    # une entree non fiable et on verifie que le chemin resolu reste sous la racine.
    chemin = os.path.abspath(os.path.join(OUTPUTS_ROOT, relatif, FICHIER_DE_VUE[vue]))
    if os.path.commonpath([chemin, OUTPUTS_ROOT]) != OUTPUTS_ROOT:
        raise HTTPException(403, "Chemin hors de la racine des sorties.")
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


@app.get("/", include_in_schema=False)
def racine():
    return RedirectResponse("/ui/")


# Monte en dernier : une route montee a la racine capterait tout /api/*.
app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
