"""SqlAlchemy-реализация VacancyRepository (data-mapper: domain <-> row)."""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime

from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from job_hunter.domain.models import (
    Employer,
    EmploymentType,
    RemoteMode,
    Salary,
    Vacancy,
    VacancyRecord,
)
from job_hunter.infrastructure.db.orm import LLMCacheRow, VacancyRow


class SqlVacancyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, vacancy: Vacancy, *, seen_at: datetime) -> bool:
        row = self._session.execute(
            select(VacancyRow).where(
                VacancyRow.source == vacancy.source,
                VacancyRow.external_id == vacancy.external_id,
            )
        ).scalar_one_or_none()

        if row is None:
            self._session.add(self._new_row(vacancy, seen_at))
            self._session.flush()
            return True

        self._fill_row(row, vacancy)
        row.last_seen_at = seen_at
        row.times_seen += 1
        return False

    def get(self, record_id: int) -> VacancyRecord | None:
        row = self._session.get(VacancyRow, record_id)
        return self._to_record(row) if row else None

    @staticmethod
    def _with_filters(
        stmt: Select[Any], *, source: str | None, query: str | None
    ) -> Select[Any]:
        if source:
            stmt = stmt.where(VacancyRow.source == source)
        if query:
            like = f"%{query}%"
            stmt = stmt.where(
                VacancyRow.title.ilike(like) | VacancyRow.employer_name.ilike(like)
            )
        return stmt

    def list_recent(
        self,
        *,
        source: str | None = None,
        limit: int = 50,
        offset: int = 0,
        query: str | None = None,
    ) -> Sequence[VacancyRecord]:
        stmt = (
            self._with_filters(select(VacancyRow), source=source, query=query)
            .order_by(VacancyRow.published_at.desc().nulls_last(), VacancyRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [self._to_record(row) for row in self._session.execute(stmt).scalars()]

    def count(self, *, source: str | None = None, query: str | None = None) -> int:
        stmt = self._with_filters(
            select(func.count()).select_from(VacancyRow), source=source, query=query
        )
        return int(self._session.execute(stmt).scalar_one())

    # --- mapping ---------------------------------------------------------

    @staticmethod
    def _content_hash(vacancy: Vacancy) -> str:
        salary = vacancy.salary
        payload = "|".join(
            [
                vacancy.title,
                vacancy.employer.name,
                vacancy.description,
                str(salary.min_amount if salary else ""),
                str(salary.max_amount if salary else ""),
                (salary.currency if salary else "") or "",
            ]
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _new_row(self, vacancy: Vacancy, seen_at: datetime) -> VacancyRow:
        row = VacancyRow(
            source=vacancy.source,
            external_id=vacancy.external_id,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            times_seen=1,
            content_hash="",
            description="",
            skills_json="[]",
            locations_json="[]",
            remote=RemoteMode.UNKNOWN.value,
            employment=EmploymentType.UNKNOWN.value,
        )
        self._fill_row(row, vacancy)
        return row

    def _fill_row(self, row: VacancyRow, vacancy: Vacancy) -> None:
        salary = vacancy.salary
        row.url = vacancy.url
        row.title = vacancy.title
        row.employer_name = vacancy.employer.name
        row.employer_url = vacancy.employer.url
        row.description = vacancy.description
        row.salary_min = salary.min_amount if salary else None
        row.salary_max = salary.max_amount if salary else None
        row.salary_currency = salary.currency if salary else None
        row.skills_json = json.dumps(list(vacancy.skills), ensure_ascii=False)
        row.locations_json = json.dumps(list(vacancy.locations), ensure_ascii=False)
        row.remote = vacancy.remote.value
        row.employment = vacancy.employment.value
        row.seniority = vacancy.seniority
        row.published_at = vacancy.published_at
        row.content_hash = self._content_hash(vacancy)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        """SQLite не хранит tz — трактуем наивные даты как UTC (Postgres вернёт aware как есть)."""
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    @staticmethod
    def _to_record(row: VacancyRow) -> VacancyRecord:
        salary = None
        if row.salary_min is not None or row.salary_max is not None:
            salary = Salary(
                min_amount=row.salary_min, max_amount=row.salary_max, currency=row.salary_currency
            )
        vacancy = Vacancy(
            source=row.source,
            external_id=row.external_id,
            url=row.url,
            title=row.title,
            employer=Employer(name=row.employer_name, url=row.employer_url),
            description=row.description,
            salary=salary,
            skills=tuple(json.loads(row.skills_json)),
            locations=tuple(json.loads(row.locations_json)),
            remote=RemoteMode(row.remote),
            employment=EmploymentType(row.employment),
            seniority=row.seniority,
            published_at=SqlVacancyRepository._aware(row.published_at) if row.published_at else None,
        )
        return VacancyRecord(
            id=row.id,
            vacancy=vacancy,
            first_seen_at=SqlVacancyRepository._aware(row.first_seen_at),
            last_seen_at=SqlVacancyRepository._aware(row.last_seen_at),
            times_seen=row.times_seen,
            content_hash=row.content_hash,
        )


class SqlLlmCacheRepository:
    """Кэш LLM-ответов (порт LLMCache): неизменяемые входы → записанный вывод навсегда валиден."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, key: str) -> str | None:
        row = self._session.get(LLMCacheRow, key)
        return row.output if row else None

    def set(self, key: str, *, role: str, model: str, output: str) -> None:
        stmt = (
            sqlite_insert(LLMCacheRow)
            .values(cache_key=key, role=role, model=model, output=output, created_at=datetime.now(UTC))
            .on_conflict_do_nothing()
        )
        self._session.execute(stmt)
