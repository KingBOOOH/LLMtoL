"""Agent de generation, a lancer SUR L'HOTE. L'API en conteneur ne peut pas le faire.

Pourquoi cet agent existe : un conteneur Linux ne peut pas executer blender.exe, et la
bibliotheque LDraw comme LM Studio vivent sur l'hote. L'API se contente donc de deposer
des jobs en base ; c'est ici qu'ils sont traites. La base sert de file d'attente.

Benefice au-dela du contournement : le tueur de taches par pression memoire a deja tue
des runs (LM Studio retient ~18 Go). Un job tue reste en base et est repris au
redemarrage, au lieu d'etre perdu.

Deux phases, volontairement separees :
  1. produire les N variantes (plan, design, model.ldr, 4 rendus) -> status a_choisir
  2. apres le choix de l'utilisateur : nomenclature, etapes, notice de la SEULE
     variante retenue -> status terminee

Grouper la phase 2 sur une seule variante est ce qui rend le choix abordable : le rendu
des images d'etapes est de loin le poste le plus lourd, le produire pour 4 variantes
reviendrait a en jeter les trois quarts.

    python agent.py                 boucle en continu
    python agent.py --une-fois      traite au plus un job puis rend la main
"""

import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from datetime import UTC, datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select, text

import config
import pipeline
from server.db import AGENT_TIMEOUT_S, Job, Session, Variante, assurer_schema
from src.instructions import compose_all_steps, write_notice_html
from src.llm_client import LLMCallError
from src.pdf import write_pdf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUTS_DIR = os.path.join(BASE_DIR, "outputs")

NOM_AGENT = os.environ.get("LEGO_AGENT_NAME", socket.gethostname())
INTERVALLE_S = int(os.environ.get("LEGO_AGENT_POLL_S", "3"))

# Un job en_cours plus vieux que ca est considere abandonne (agent tue en cours de
# route) et repris. Large, parce qu'une generation de 4 variantes peut depasser dix
# minutes sur un modele gros ou courbe.
REPRISE_APRES_S = int(os.environ.get("LEGO_AGENT_STALE_S", "1800"))



def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def version_du_code():
    """Commit git court, pour pouvoir expliquer plus tard un resultat ancien."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=BASE_DIR,
                             capture_output=True, text=True, timeout=10,
                             check=False)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


CODE_VERSION = version_du_code()


def signaler(session):
    """Enregistre le signe de vie. Appele aussi PENDANT un job, pas seulement entre
    deux : une generation dure plusieurs minutes et l'agent ne doit pas etre declare
    mort alors qu'il travaille."""
    session.execute(text(
        "INSERT INTO agents (nom, seen_at, version) VALUES (:n, now(), :v) "
        "ON CONFLICT (nom) DO UPDATE SET seen_at = now(), version = :v"),
        {"n": NOM_AGENT, "v": CODE_VERSION})
    session.commit()


def boucle_signe_de_vie(arret, intervalle=None, session_factory=None, beat=None):
    """Signale la presence de l'agent en continu, dans un THREAD separe.

    Indispensable : le signal etait emis par la boucle principale, qui est bloquee
    pendant un rendu Blender de plusieurs minutes. L'API declarait alors l'agent mort
    alors qu'il travaillait, et l'interface affichait "aucun agent actif" en plein
    travail. Mesure du defaut : 3 min 05 sans signal pendant un rendu d'etapes, pour
    un delai cote API de 90 s.

    session_factory / beat sont injectables pour les tests : la boucle est testable
    sans base ni thread reel.
    """
    pause = intervalle or INTERVALLE_S
    fabrique = session_factory or Session
    emettre = beat or signaler
    with fabrique() as session:
        while True:
            try:
                emettre(session)
            except Exception as e:  # noqa: BLE001 - frontiere : voir ci-dessous
                # Une base momentanement injoignable ne doit pas tuer le thread :
                # l'agent continuerait a travailler en paraissant mort pour toujours.
                log(f"signe de vie impossible : {e}")
                try:
                    session.rollback()
                except Exception:  # noqa: BLE001, S110 - un rollback qui
                    pass           # echoue ne doit pas masquer l'erreur d'origine
            if arret.wait(pause):
                return


def prendre_un_job(session):
    """Prend atomiquement le job en attente le plus ancien, ou None.

    SKIP LOCKED + FOR UPDATE : si deux agents tournent par erreur, chacun prend un job
    different au lieu que les deux traitent le meme.
    """
    ligne = session.execute(text(
        "UPDATE jobs SET status = 'en_cours', claimed_at = now(), etape = 'demarrage' "
        "WHERE id = ("
        "  SELECT id FROM jobs WHERE status = 'en_attente' "
        "  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
        ") RETURNING id")).first()
    session.commit()
    return session.get(Job, ligne[0]) if ligne else None


def prendre_une_finition(session):
    """Prend un job dont l'utilisateur a choisi la variante (phase 2)."""
    ligne = session.execute(text(
        "UPDATE jobs SET status = 'en_cours', etape = 'notice' "
        "WHERE id = ("
        "  SELECT id FROM jobs WHERE status = 'en_finition' "
        "  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
        ") RETURNING id")).first()
    session.commit()
    return session.get(Job, ligne[0]) if ligne else None


def reprendre_les_abandonnes(session, limite=None):
    """Remet en file les jobs laisses en_cours par un agent tue.

    C'est la reponse automatique au tueur de taches par pression memoire : au lieu de
    perdre le travail, le job repart. Un job deja passe a_choisir n'est pas touche.

    Deux cas, qui ne se reprennent PAS de la meme facon :

    - une variante est deja retenue : l'interruption a eu lieu en phase 2 (notice,
      PDF). La variante retenue est intacte, le job repart en `en_finition`.
    - aucune variante retenue : l'interruption a eu lieu pendant la production des
      variantes. Celles deja produites sont incompletes (leurs rendus ont pu etre
      interrompus) et surtout leurs indices rejoueraient a l'identique : l'insertion
      violerait UNIQUE(job_id, indice) et le job echouerait au lieu de reprendre.
      On repart donc proprement de zero.

    Retourne le nombre de jobs repris.
    """
    limite = limite or (datetime.now(UTC) - timedelta(seconds=REPRISE_APRES_S))
    abandonnes = session.scalars(
        select(Job).where(Job.status == "en_cours", Job.claimed_at < limite)).all()

    for job in abandonnes:
        if any(v.is_chosen for v in job.variantes):
            job.status = "en_finition"
        else:
            for v in list(job.variantes):
                session.delete(v)
            job.status = "en_attente"
        job.claimed_at = None
        job.etape = "reprise apres interruption"
        session.add(job)

    session.commit()
    if abandonnes:
        log(f"{len(abandonnes)} job(s) abandonne(s) repris")
    return len(abandonnes)


def _etat(session, job, etape, fait=None, total=None):
    """Met a jour l'avancement lu par l'interface.

    fait / total ne sont renseignes que lorsqu'ils representent quelque chose de
    reel. Une etape sans sous-progression mesurable les remet a None : l'interface
    affiche alors une barre indeterminee plutot qu'une fausse progression.
    """
    job.etape = etape
    job.etape_fait = fait
    job.etape_total = total
    session.add(job)
    session.commit()


def indices_a_produire(variantes, n_variantes):
    """Indices des propositions qu'il reste a produire pour ce job.

    Une proposition est acquise des qu'elle a un design_json ; les autres sont a
    refaire. Rend produire_variantes REPRENABLE, ce qui sert deux fois :

    - l'utilisateur demande des propositions supplementaires pour remplacer celles qui
      n'ont pas abouti (route /completer) — refaire les reussies serait les re-rendre
      pour rien, et plusieurs minutes de Blender a la poubelle ;
    - un job remis en file conserve ce qu'il avait deja obtenu.

    Les propositions ratees gardent leur indice : il est reutilise, donc aucune
    violation de UNIQUE(job_id, indice) tant que la ligne est effacee avant.
    """
    acquises = {v.indice for v in variantes if v.design_json is not None}
    return [i for i in range(1, n_variantes + 1) if i not in acquises]


def produire_variantes(session, job):
    """Phase 1 : les propositions manquantes, puis UN seul Blender pour les rendre."""
    run_dir = os.path.join(OUTPUTS_DIR, f"job_{job.id}")
    os.makedirs(run_dir, exist_ok=True)
    dossiers = []

    a_produire = indices_a_produire(job.variantes, job.n_variantes)
    deja = job.n_variantes - len(a_produire)
    if deja:
        log(f"job {job.id} : {deja} proposition(s) deja acquise(s), "
            f"{len(a_produire)} a produire")
    # Les lignes des tentatives ratees sont effacees, leur indice etant reutilise.
    for v in [v for v in job.variantes if v.indice in a_produire]:
        session.delete(v)
    session.commit()
    # La session est construite avec expire_on_commit=False : sans ce rafraichissement,
    # job.variantes continue de referencer les lignes supprimees, et la premiere
    # ecriture sur le job leve "Instance has been deleted". Mesure en conditions
    # reelles : le job echouait des la premiere mise a jour d'avancement.
    session.refresh(job)

    for rang, i in enumerate(a_produire, start=1):
        _etat(session, job, "design", fait=deja + rang, total=job.n_variantes)
        iter_dir = os.path.join(run_dir, f"variante_{i:02d}")
        os.makedirs(iter_dir, exist_ok=True)
        log_path = os.path.join(iter_dir, "log.txt")

        variante = Variante(job_id=job.id, indice=i)
        t0 = time.time()
        try:
            result = pipeline.generer_modele(job.prompt, iter_dir, log_path,
                                             taille=job.taille)
        except (LLMCallError, RuntimeError) as e:
            # Echec attendu : reseau, ou design toujours invalide apres reparations.
            # Une variante ratee ne doit pas faire tomber les autres.
            log(f"variante {i} en echec : {e}")
            variante.error_message = str(e)[:500]
            variante.duration_s = round(time.time() - t0, 2)
            session.add(variante)
            session.commit()
            continue

        m = result["report"]["metrics"]
        variante.design_json = result["design"]
        variante.name = result["nom"]
        variante.bbox = result["design"].get("bbox")
        variante.n_pieces = m["n_pieces"]
        variante.score = result["report"]["score"]
        variante.n_components = m["n_components"]
        variante.fill_rate = m["fill_rate"]
        variante.joint_alignment_rate = m["joint_alignment_rate"]
        variante.z_min = m.get("z_min")
        variante.n_floating = sum("flottante" in w for w in result["report"]["warnings"])
        variante.llm_model = config.LM_STUDIO_MODEL
        variante.repairs_used = result["repairs_used"]
        variante.duration_s = round(time.time() - t0, 2)
        variante.code_version = CODE_VERSION
        # RELATIF a outputs/ : l'API lit ce chemin depuis un conteneur Linux.
        variante.render_dir = os.path.relpath(iter_dir, OUTPUTS_DIR).replace(os.sep, "/")
        session.add(variante)
        session.commit()
        dossiers.append(iter_dir)

    # Echec seulement si RIEN n'a abouti, tentatives precedentes comprises : sur une
    # demande de propositions supplementaires, les anciennes restent valables meme si
    # les nouvelles echouent toutes.
    acquises = session.scalars(
        select(Variante).where(Variante.job_id == job.id,
                               Variante.design_json.is_not(None))).all()
    if not acquises:
        raise RuntimeError("aucune variante n'a abouti")

    if not dossiers:
        # Rien de neuf a rendre, mais des propositions valables existent : on rend la
        # main a l'utilisateur plutot que de faire echouer son job.
        log(f"job {job.id} : aucune nouvelle proposition, {len(acquises)} conservee(s)")
        job.status = "a_choisir"
        job.etape = job.etape_fait = job.etape_total = None
        session.add(job)
        session.commit()
        return

    _etat(session, job, "rendu des variantes", fait=0, total=len(dossiers))
    pipeline.render_all(
        dossiers, run_dir,
        on_progress=lambda k, n: _etat(session, job, "rendu des variantes", k, n))

    # La nomenclature a besoin du nombre de references : calculee ici, apres coup,
    # pour ne pas rallonger la phase de design.
    for v in session.scalars(select(Variante).where(Variante.job_id == job.id)):
        if v.design_json and v.n_references is None:
            from src.bom import build_bom
            from src.compiler import compile_design
            from src.tiler import tile
            v.n_references = len(build_bom(tile(compile_design(v.design_json))))
    job.status = "a_choisir"
    job.etape = job.etape_fait = job.etape_total = None
    session.add(job)
    session.commit()
    log(f"job {job.id} : {len(dossiers)} variante(s) pretes, en attente du choix")


def chemins_manquants(iter_dir):
    """Ce qui doit etre reconstruit sur disque avant la phase 2.

    Une REGENERATION repart du seul `design_json` : le dossier de sortie peut avoir
    ete efface entre-temps (nettoyage, changement de machine, variante rejetee puis
    retenue). Tout en descend de facon deterministe, encore faut-il savoir ce qui
    manque plutot que de tout refaire a chaque fois — le rendu des quatre vues coute
    plusieurs minutes.
    """
    return {
        "ldr": not os.path.isfile(os.path.join(iter_dir, "model.ldr")),
        # render_iso.png sert de couverture au PDF : son absence suffit a declarer
        # les rendus du modele a refaire.
        "rendus": not os.path.isfile(os.path.join(iter_dir, "render_iso.png")),
    }


def finir(session, job):
    """Phase 2 : nomenclature, etapes et notice de la variante retenue uniquement.

    Sert aussi de chemin de REGENERATION : l'API remet un job termine en
    `en_finition` et tout est rejoue depuis `design_json`, sans aucun appel LLM.
    """
    retenue = session.scalar(
        select(Variante).where(Variante.job_id == job.id, Variante.is_chosen.is_(True)))
    if retenue is None:
        raise RuntimeError("aucune variante retenue")

    if not retenue.render_dir:
        # Variante dont les rendus avaient ete effaces : on lui redonne un dossier
        # plutot que de refuser la regeneration.
        retenue.render_dir = f"job_{job.id}/variante_{retenue.indice:02d}"
        session.add(retenue)
        session.commit()
    iter_dir = os.path.join(OUTPUTS_DIR, retenue.render_dir)
    os.makedirs(iter_dir, exist_ok=True)
    log_path = os.path.join(iter_dir, "log.txt")
    run_dir = os.path.dirname(iter_dir)

    from src.compiler import compile_design
    from src.tiler import tile
    from src.validator import validate
    voxels = compile_design(retenue.design_json)
    placements = tile(voxels)
    # Revalidation plutot que stockage du rapport : la chaine est deterministe, donc
    # recalculer coute quelques millisecondes et evite une colonne de donnee derivee.
    rapport = validate(voxels, placements, retenue.design_json)

    manquant = chemins_manquants(iter_dir)
    if manquant["ldr"]:
        from src.ldraw_writer import write_ldr
        write_ldr(placements, os.path.join(iter_dir, "model.ldr"),
                  retenue.name or "modele")
        log(f"job {job.id} : model.ldr reconstruit depuis design_json")
    if manquant["rendus"]:
        # Lot a part : les quatre vues du modele veulent le cadrage par defaut, les
        # etapes un cadrage plus serre et la seule vue isometrique.
        _etat(session, job, "rendu du modele", fait=0, total=1)
        pipeline.render_all(
            [iter_dir], run_dir, manifest_name="_manifest_modele.txt",
            on_progress=lambda k, n: _etat(session, job, "rendu du modele", k, n))

    _etat(session, job, "nomenclature et etapes")
    etape_dirs, piece_models, etapes = pipeline.preparer_notice(
        placements, iter_dir, retenue.name or "modele", log_path)

    images_pieces = {}
    if etape_dirs:
        a_rendre = list(etape_dirs) + list(piece_models.values())
        _etat(session, job, "rendu des etapes", fait=0, total=len(a_rendre))
        env = os.environ.copy()
        env["LEGO_RENDER_MARGIN"] = os.environ.get("LEGO_NOTICE_MARGIN", "1.7")
        env["LEGO_RENDER_VIEWS"] = "iso"
        pipeline.render_all(
            a_rendre, run_dir, assemble_dirs=[], env_extra=env,
            manifest_name="_manifest_notice.txt",
            on_progress=lambda k, n: _etat(session, job, "rendu des etapes", k, n))
        compose_all_steps(etapes, etape_dirs, piece_models)

        from src.bom import write_bom
        images_pieces = {cle: os.path.join(d, "render_iso.png")
                         for cle, d in piece_models.items()}
        write_bom(placements, iter_dir, retenue.name or "modele",
                  piece_images=images_pieces)

    write_notice_html(etapes, etape_dirs or [""] * len(etapes),
                      os.path.join(iter_dir, "notice.html"), retenue.name or "modele",
                      rapport=rapport)

    # PDF de la SEULE variante retenue : le produire pour les 4 reviendrait a en jeter
    # les trois quarts. Un echec ici ne met pas le job en echec — le modele est bon,
    # c'est le PDF qui manque, et l'interface le dit.
    _etat(session, job, "notice PDF")
    try:
        chemin_pdf = write_pdf(
            etapes, etape_dirs or [""] * len(etapes), placements, iter_dir,
            retenue.name or "modele",
            rendu_final=os.path.join(iter_dir, "render_iso.png"),
            piece_images=images_pieces, prompt=job.prompt,
            identifiant=str(job.id), rapport=rapport)
        with open(chemin_pdf, "rb") as f:
            retenue.pdf = f.read()
        session.add(retenue)
        session.commit()
        log(f"job {job.id} : PDF de {len(retenue.pdf)} octets enregistre")
    except (RuntimeError, OSError) as e:
        log(f"job {job.id} : PDF non produit ({e})")

    # Les variantes rejetees gardent design_json, metriques et seed (~2 ko) mais plus
    # leurs rendus : c'est le seul materiau pour comprendre plus tard ce que le modele
    # rate, sans garder des images inutiles.
    for v in session.scalars(select(Variante).where(Variante.job_id == job.id,
                                                    Variante.is_rejected.is_(True))):
        if v.render_dir:
            import shutil
            shutil.rmtree(os.path.join(OUTPUTS_DIR, v.render_dir), ignore_errors=True)
            v.render_dir = None

    job.status = "terminee"
    job.etape = job.etape_fait = job.etape_total = None
    job.finished_at = datetime.now(UTC)
    session.add(job)
    session.commit()
    log(f"job {job.id} : termine")


def traiter_un(session):
    """Traite au plus un job. Retourne True si quelque chose a ete fait."""
    job = prendre_une_finition(session) or prendre_un_job(session)
    if job is None:
        return False

    phase = "finition" if any(v.is_chosen for v in job.variantes) else "variantes"
    log(f"job {job.id} pris ({phase}) : {job.prompt[:60]}")
    try:
        if phase == "finition":
            finir(session, job)
        else:
            produire_variantes(session, job)
    except Exception as e:  # noqa: BLE001 - un job qui echoue est enregistre
        log(f"job {job.id} ECHEC : {e}")  # comme tel, il n'arrete pas l'agent
        traceback.print_exc()
        job.status = "echec"
        job.etape = job.etape_fait = job.etape_total = None
        job.error_message = str(e)[:500]
        job.finished_at = datetime.now(UTC)
        session.add(job)
        session.commit()
    return True


# Recul entre deux pre-controles. Le pre-controle envoie une VRAIE requete de completion
# — il faut voir ce que le modele repond pour detecter le Structured Output parasite — et
# le JIT loading de LM Studio charge donc le modele de generation, 18,6 Go, pour y
# repondre. Un SystemExit ici etait vu par Docker comme un plantage (`restart:
# unless-stopped`) : le conteneur redemarrait, redemandait le modele, et rechargeait
# 18,6 Go toutes les quelques secondes, jusqu'a tuer par pression memoire tout autre
# travail en cours. Un refus DELIBERE de demarrer n'est pas un plantage. On attend, avec
# un recul croissant qui laisse au chargement le temps d'aboutir au lieu de l'annuler.
ATTENTE_LM_MIN_S = 10
ATTENTE_LM_MAX_S = 300


def attendre_lm_studio(preflight, une_fois=False):
    """Bloque jusqu'a ce que LM Studio soit utilisable. Leve SystemExit si --une-fois.

    Ne prend AUCUN job tant que le controle echoue : sous le piege du Structured Output,
    le planificateur repond du JSON de cubes a n'importe quelle demande, et chaque job
    traite serait un dechet. Attendre et ne rien prendre est le bon comportement d'un
    agent ; mourir et renaitre n'en est pas un.
    """
    attente = ATTENTE_LM_MIN_S
    while True:
        ok, message = preflight()
        log(f"pre-controle LM Studio : {message}")
        if ok:
            return
        if une_fois:
            raise SystemExit("\nAgent non demarre. Corrige le point ci-dessus, ou "
                             "--skip-env-check pour forcer.")
        log(f"  aucun job ne sera pris. Nouvel essai dans {attente} s "
            "(--skip-env-check pour forcer).")
        time.sleep(attente)
        attente = min(attente * 2, ATTENTE_LM_MAX_S)


def main():
    """Boucle de traitement. Verifie LM Studio avant d'accepter le premier job."""
    une_fois = "--une-fois" in sys.argv

    log(f"agent '{NOM_AGENT}' (code {CODE_VERSION}) — base {os.environ.get('DATABASE_URL', 'defaut')}")

    if "--skip-env-check" not in sys.argv:
        from tools.check_env import preflight_llm
        attendre_lm_studio(preflight_llm, une_fois)

    # L'agent peut demarrer avant l'API : il met le schema a jour lui aussi plutot
    # que d'echouer sur une colonne absente.
    assurer_schema()

    demarrage = datetime.now(UTC)
    arret = threading.Event()
    # Thread dedie : le signe de vie doit continuer pendant un rendu Blender, qui
    # bloque la boucle principale plusieurs minutes.
    threading.Thread(target=boucle_signe_de_vie, args=(arret,), daemon=True).start()

    with Session() as session:
        # Au demarrage, tout job encore "en_cours" a ete reclame par une incarnation
        # precedente de l'agent, puisque celle-ci vient de naitre. Inutile d'attendre
        # REPRISE_APRES_S : sans ca, un agent relance laissait son ancien job bloque
        # une demi-heure, et l'interface affichait deux generations en cours.
        reprendre_les_abandonnes(session, limite=demarrage)
        log(f"en attente de jobs (signe de vie toutes les {INTERVALLE_S}s dans un "
            f"thread dedie, delai cote API {AGENT_TIMEOUT_S}s)")
        while True:
            try:
                occupe = traiter_un(session)
            except KeyboardInterrupt:
                log("arret demande")
                arret.set()
                return
            except Exception as e:  # noqa: BLE001 - voir ci-dessous
                # La boucle ne doit jamais mourir sur une base momentanement absente.
                log(f"erreur de boucle : {e}")
                session.rollback()
                occupe = False
            if une_fois:
                arret.set()
                return
            if not occupe:
                time.sleep(INTERVALLE_S)


if __name__ == "__main__":
    main()
