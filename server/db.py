"""Modeles et session SQLAlchemy, PARTAGES entre l'API (conteneur) et l'agent (hote).

Un seul endroit decrit le schema, pour que les deux cotes ne derivent jamais. Le
schema SQL de reference reste db/init.sql (joue par Postgres au premier demarrage) :
ce module le reflete, il ne le cree pas.

Les deux cotes n'atteignent pas la base par la meme adresse — "db:5432" dans le reseau
Docker, "127.0.0.1:5442" depuis l'hote — d'ou l'URL lue dans l'environnement.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import (ARRAY, BigInteger, Boolean, DateTime, ForeignKey, Integer,
                        LargeBinary, Numeric, String, Text, UniqueConstraint,
                        create_engine, func, select)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://llmtol:change_moi_en_local@127.0.0.1:5442/llmtol")

# Delai au-dela duquel un agent est considere comme parti. Plus court que ca, un agent
# occupe par une generation de plusieurs minutes serait declare mort a tort : il se
# signale donc pendant qu'il travaille, pas seulement entre deux jobs.
AGENT_TIMEOUT_S = int(os.environ.get("AGENT_TIMEOUT_S", "90"))

# pool_pre_ping : l'agent tourne des heures et Postgres peut couper une connexion
# inactive entre deux jobs. Sans ca, le premier job apres une pause echouerait.
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


class Job(Base):
    """Une soumission : un prompt, N variantes a produire, puis un choix."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    taille: Mapped[str | None] = mapped_column(Text)
    n_variantes: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="en_attente")
    etape: Mapped[str | None] = mapped_column(Text)
    # Avancement chiffre de l'etape courante, nul quand il n'est pas mesurable.
    etape_fait: Mapped[int | None] = mapped_column(Integer)
    etape_total: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    variantes: Mapped[list["Variante"]] = relationship(
        back_populates="job", cascade="all, delete-orphan",
        order_by="Variante.indice", lazy="selectin")


class Variante(Base):
    """Une variante produite pour un job. `design_json` est la vraie archive."""

    __tablename__ = "variantes"
    __table_args__ = (UniqueConstraint("job_id", "indice"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    indice: Mapped[int] = mapped_column(Integer, nullable=False)

    design_json: Mapped[dict | None] = mapped_column(JSONB)
    name: Mapped[str | None] = mapped_column(Text)
    bbox: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    n_pieces: Mapped[int | None] = mapped_column(Integer)
    n_references: Mapped[int | None] = mapped_column(Integer)

    score: Mapped[float | None] = mapped_column(Numeric(5, 1))
    n_components: Mapped[int | None] = mapped_column(Integer)
    fill_rate: Mapped[float | None] = mapped_column(Numeric(6, 3))
    joint_alignment_rate: Mapped[float | None] = mapped_column(Numeric(6, 3))
    z_min: Mapped[int | None] = mapped_column(Integer)
    n_floating: Mapped[int | None] = mapped_column(Integer)

    llm_model: Mapped[str | None] = mapped_column(Text)
    seed: Mapped[int | None] = mapped_column(BigInteger)
    repairs_used: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Numeric(10, 2))
    code_version: Mapped[str | None] = mapped_column(Text)

    render_dir: Mapped[str | None] = mapped_column(Text)
    is_chosen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_rejected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pdf: Mapped[bytes | None] = mapped_column(LargeBinary)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())

    job: Mapped[Job] = relationship(back_populates="variantes")


class Agent(Base):
    """Signe de vie d'un agent de generation tournant sur un hote."""

    __tablename__ = "agents"

    nom: Mapped[str] = mapped_column(String(200), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                              server_default=func.now())
    version: Mapped[str | None] = mapped_column(Text)


def agent_actif(session):
    """Retourne le nom d'un agent vu recemment, ou None.

    Sert a refuser une soumission plutot qu'a la laisser attendre indefiniment : sans
    agent, l'API est incapable de generer quoi que ce soit.
    """
    limite = datetime.now(timezone.utc) - timedelta(seconds=AGENT_TIMEOUT_S)
    return session.scalar(select(Agent.nom).where(Agent.seen_at >= limite).limit(1))
