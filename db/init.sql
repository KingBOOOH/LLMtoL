-- Schema LLMtoL. Joue une seule fois, au premier demarrage du volume Postgres.
-- Pour le rejouer apres modification : docker compose down -v (efface les donnees).

-- Une soumission : un prompt, N variantes a produire, puis un choix de l'utilisateur.
CREATE TABLE jobs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt        text        NOT NULL,
    taille        text,
    n_variantes   int         NOT NULL DEFAULT 4,

    -- en_attente  : depose par l'API, aucun agent ne l'a encore pris
    -- en_cours    : l'agent produit les variantes
    -- a_choisir   : les variantes sont pretes, on attend le choix de l'utilisateur
    -- en_finition : choix fait, l'agent produit etapes + nomenclature + notice + PDF
    -- terminee    : tout est pret
    -- echec       : voir error_message
    status        text        NOT NULL DEFAULT 'en_attente'
                  CHECK (status IN ('en_attente','en_cours','a_choisir',
                                    'en_finition','terminee','echec')),

    -- Texte libre affiche tel quel par l'interface ("design 2/4", "rendu"). Une
    -- generation dure de 20 s a plusieurs minutes : sans ca l'utilisateur ne voit
    -- qu'un sablier.
    etape         text,

    created_at    timestamptz NOT NULL DEFAULT now(),
    claimed_at    timestamptz,
    finished_at   timestamptz,
    error_message text
);

CREATE INDEX jobs_recent_idx ON jobs (created_at DESC);
CREATE INDEX jobs_status_idx ON jobs (status);

-- Une variante produite pour un job.
CREATE TABLE variantes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id      uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    indice      int  NOT NULL,           -- 1..n, ordre d'affichage stable

    -- L'ARCHIVE REELLE. ~1 a 2 ko, et toute la chaine en aval est deterministe :
    -- avec ce seul champ on regenere le .ldr, les rendus, la nomenclature, la notice
    -- et le PDF, sans aucun appel LLM. C'est la seule chose qu'on ne peut pas
    -- reconstruire, donc la seule qu'on doit absolument garder.
    design_json jsonb,

    name        text,
    bbox        int[],
    n_pieces    int,
    n_references int,

    -- Metriques deja calculees par src/validator.py et jetees jusqu'ici. Les garder
    -- rend l'historique triable par qualite et donne une tendance dans le temps,
    -- pour zero calcul supplementaire.
    score                numeric(5,1),
    n_components         int,
    fill_rate            numeric(6,3),
    joint_alignment_rate numeric(6,3),
    z_min                int,
    n_floating           int,

    -- Reproductibilite : sans ces champs, impossible d'expliquer pourquoi un ancien
    -- resultat differe d'un nouveau. Le schema DSL et les prompts bougent souvent.
    llm_model    text,
    seed         bigint,
    repairs_used int,
    duration_s   numeric(10,2),
    code_version text,                   -- commit git court

    -- RELATIF a la racine outputs/ : l'agent est sur Windows, l'API dans un conteneur
    -- Linux. Un chemin absolu n'aurait de sens que d'un seul cote.
    render_dir  text,

    is_chosen   boolean NOT NULL DEFAULT false,
    -- Variante non retenue : on efface le PDF et les rendus, on GARDE design_json,
    -- les metriques et la seed (~2 ko). C'est la seule matiere pour comprendre plus
    -- tard ce que le modele rate.
    is_rejected boolean NOT NULL DEFAULT false,

    pdf         bytea,                   -- uniquement pour la variante choisie
    error_message text,
    created_at  timestamptz NOT NULL DEFAULT now(),

    UNIQUE (job_id, indice)
);

CREATE INDEX variantes_job_idx ON variantes (job_id);

-- Signe de vie de l'agent. L'API ne peut pas generer elle-meme : si aucun agent ne
-- s'est signale recemment, elle doit refuser un nouveau job plutot que de le laisser
-- attendre indefiniment.
CREATE TABLE agents (
    nom     text PRIMARY KEY,
    seen_at timestamptz NOT NULL DEFAULT now(),
    version text
);
