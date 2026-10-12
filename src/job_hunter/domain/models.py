"""Доменные сущности. Чистые dataclass'ы без зависимостей от инфраструктуры."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RemoteMode(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    UNKNOWN = "unknown"


class VacancyStatus(StrEnum):
    """Статус вакансии в воронке кандидата (docs/UI_SCENARIOS.md)."""

    NEW = "new"
    INTERESTED = "interested"
    HIDDEN = "hidden"
    APPLIED = "applied"  # отклик отправлен вручную (дата фиксируется)
    RESPONDED = "responded"  # пришёл ответ работодателя
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    ARCHIVED = "archived"


# Порядок колонок канбана (новые/скрытые/архивные — вне доски)
PIPELINE_STATUSES: tuple[VacancyStatus, ...] = (
    VacancyStatus.INTERESTED,
    VacancyStatus.APPLIED,
    VacancyStatus.RESPONDED,
    VacancyStatus.INTERVIEW,
    VacancyStatus.OFFER,
)


@dataclass(frozen=True, slots=True)
class Salary:
    min_amount: int | None = None
    max_amount: int | None = None
    currency: str | None = None
    gross: bool | None = None


@dataclass(frozen=True, slots=True)
class Employer:
    name: str
    external_id: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class Vacancy:
    """Нормализованная вакансия — единый формат для всех источников."""

    source: str
    external_id: str
    url: str
    title: str
    employer: Employer
    description: str = ""
    salary: Salary | None = None
    skills: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    remote: RemoteMode = RemoteMode.UNKNOWN
    employment: EmploymentType = EmploymentType.UNKNOWN
    seniority: str | None = None
    published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class VacancyRecord:
    """Вакансия из хранилища вместе с метаданными наблюдений."""

    id: int
    vacancy: Vacancy
    first_seen_at: datetime
    last_seen_at: datetime
    times_seen: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class VacancyState:
    """Состояние вакансии в воронке кандидата (по умолчанию — NEW)."""

    record_id: int
    status: VacancyStatus = VacancyStatus.NEW
    note: str | None = None
    applied_at: datetime | None = None  # когда отметили «откликнулся»
    updated_at: datetime | None = None
