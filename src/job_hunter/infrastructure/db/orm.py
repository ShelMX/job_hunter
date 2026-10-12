"""ORM-модели (SQLAlchemy 2.0, typed)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class VacancyRow(Base):
    __tablename__ = "vacancies"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_vacancy_source_ext"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(256))
    employer_name: Mapped[str] = mapped_column(String(256))
    employer_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    skills_json: Mapped[str] = mapped_column(Text)
    locations_json: Mapped[str] = mapped_column(Text)
    remote: Mapped[str] = mapped_column(String(16))
    employment: Mapped[str] = mapped_column(String(16))
    seniority: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    times_seen: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64))


class LLMCacheRow(Base):
    """Кэш LLM-ответов: ключ = sha256(роль + версия промпта + вход)."""

    __tablename__ = "llm_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    output: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VacancyStateRow(Base):
    """Воронка кандидата: статус/заметка/дата отклика для вакансии."""

    __tablename__ = "vacancy_states"

    record_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="new")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HiddenEmployerRow(Base):
    """Работодатели, скрытые кандидатом из всех выдач."""

    __tablename__ = "hidden_employers"

    employer_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FetchRunRow(Base):
    """Время последнего успешного сбора по источнику (для catch-up и индикации возраста)."""

    __tablename__ = "fetch_runs"

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
