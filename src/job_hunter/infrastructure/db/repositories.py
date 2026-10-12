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
    VacancyState,
    VacancyStatus,
)
from job_hunter.infrastructure.db.orm import (
    FetchRunRow,
    HiddenEmployerRow,
    LLMCacheRow,
    VacancyRow,
    VacancyStateRow,
)


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
    def _with_filters(stmt: Select[Any], *, source: str | None, query: str | None) -> Select[Any]:
        if source:
            stmt = stmt.where(VacancyRow.source == source)
        if query:
            like = f"%{query}%"
            stmt = stmt.where(VacancyRow.title.ilike(like) | VacancyRow.employer_name.ilike(like))
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
        stmt = self._with_filters(select(func.count()).select_from(VacancyRow), source=source, query=query)
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


class SqlStateRepository:
    """Реализация StateRepository поверх SQLAlchemy (vacancy_states + hidden_employers)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def set_status(
        self,
        record_id: int,
        status: VacancyStatus,
        *,
        applied_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        moment = now or datetime.now(UTC)
        row = self._session.get(VacancyStateRow, record_id)
        if row is None:
            row = VacancyStateRow(record_id=record_id, status=status.value, updated_at=moment)
            self._session.add(row)
        else:
            row.status = status.value
            row.updated_at = moment
        if applied_at is not None:
            row.applied_at = applied_at
        self._session.flush()

    def set_note(self, record_id: int, note: str | None, *, now: datetime | None = None) -> None:
        row = self._session.get(VacancyStateRow, record_id)
        moment = now or datetime.now(UTC)
        if row is None:
            row = VacancyStateRow(record_id=record_id, status=VacancyStatus.NEW.value, updated_at=moment)
            self._session.add(row)
        row.note = note
        row.updated_at = moment
        self._session.flush()

    def states(self) -> dict[int, VacancyState]:
        out: dict[int, VacancyState] = {}
        for row in self._session.execute(select(VacancyStateRow)).scalars():
            out[row.record_id] = VacancyState(
                record_id=row.record_id,
                status=VacancyStatus(row.status),
                note=row.note,
                applied_at=_aware(row.applied_at),
                updated_at=_aware(row.updated_at),
            )
        return out

    def hide_employer(self, name: str, *, now: datetime | None = None) -> None:
        stmt = (
            sqlite_insert(HiddenEmployerRow)
            .values(employer_name=name, created_at=now or datetime.now(UTC))
            .on_conflict_do_nothing()
        )
        self._session.execute(stmt)

    def unhide_employer(self, name: str) -> None:
        row = self._session.get(HiddenEmployerRow, name)
        if row is not None:
            self._session.delete(row)

    def hidden_employers(self) -> set[str]:
        return {row.employer_name for row in self._session.execute(select(HiddenEmployerRow)).scalars()}


class SqlFetchRunRepository:
    """Реализация FetchRunRepository поверх SQLAlchemy."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_all(self) -> dict[str, datetime]:
        out: dict[str, datetime] = {}
        for row in self._session.execute(select(FetchRunRow)).scalars():
            last = row.last_success_at
            if last is not None:
                out[row.source] = last.replace(tzinfo=UTC) if last.tzinfo is None else last
        return out

    def record_success(self, source: str, when: datetime) -> None:
        self._upsert(source, last_success_at=when, last_status="success", updated_at=when)

    def record_failure(self, source: str, when: datetime, reason: str) -> None:
        """Провал обновляет статус, но НЕ стирает факт последнего успеха (иначе catch-up зациклится)."""
        row = self._session.get(FetchRunRow, source)
        if row is None:
            row = FetchRunRow(
                source=source,
                last_success_at=None,
                last_status=_status(reason),
                updated_at=when,
            )
            self._session.add(row)
        else:
            row.last_status = _status(reason)
            row.updated_at = when

    def _upsert(
        self,
        source: str,
        *,
        last_success_at: datetime | None,
        last_status: str,
        updated_at: datetime,
    ) -> None:
        row = self._session.get(FetchRunRow, source)
        if row is None:
            row = FetchRunRow(
                source=source,
                last_success_at=last_success_at,
                last_status=last_status,
                updated_at=updated_at,
            )
            self._session.add(row)
        else:
            row.last_success_at = last_success_at
            row.last_status = last_status
            row.updated_at = updated_at
        self._session.flush()


def _status(reason: str) -> str:
    """Сжимаем причину до 16 символов, чтобы уместиться в колонку last_status."""
    normalized = reason.strip().lower()
    if "credentials" in normalized or "ключ" in normalized or "токен" in normalized:
        return "no_credentials"
    if "ошибка" in normalized or "error" in normalized or "упал" in normalized:
        return "error"
    if "timeout" in normalized or "таймаут" in normalized:
        return "timeout"
    return reason[:16] if len(reason) <= 16 else "skipped"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
