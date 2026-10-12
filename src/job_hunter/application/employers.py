"""Аналитика по вакансии и работодателю (для карточки в UI и отчётов)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from job_hunter.application.dedup import DuplicateCluster
from job_hunter.domain.models import VacancyRecord


@dataclass(frozen=True, slots=True)
class VacancyAnalytics:
    """Продольная аналитика конкретной вакансии по нашей истории наблюдений."""

    age_days: int  # от published_at (fall back first_seen_at)
    seen_span_days: int  # сколько дней её видим мы
    times_seen: int
    posting_lag_days: int | None  # first_seen - published: >0 намекает на перепост
    employer_open_vacancies: int  # сколько его вакансий ещё в наблюдении
    cluster_size: int  # размер группы такой же/смежной роли (перепосты/пулы)


@dataclass(frozen=True, slots=True)
class EmployerStats:
    name: str
    vacancies_total: int
    duplicate_groups: int  # сколько кластеров дублей/перепостов публикует
    evergreen_share: float  # доля вакансий старше evergreen_days


def compute_vacancy_analytics(
    record: VacancyRecord,
    all_records: Sequence[VacancyRecord],
    clusters: Sequence[DuplicateCluster],
    *,
    now: datetime | None = None,
) -> VacancyAnalytics:
    now = now or datetime.now(UTC)
    vacancy = record.vacancy
    base = vacancy.published_at or record.first_seen_at
    age_days = max(0, (now - base).days)
    seen_span = max(0, (record.last_seen_at - record.first_seen_at).days)
    lag = max(0, (record.first_seen_at - vacancy.published_at).days) if vacancy.published_at else None
    employer_norm = vacancy.employer.name.strip().lower()
    open_same = sum(1 for r in all_records if r.vacancy.employer.name.strip().lower() == employer_norm)
    cluster_size = next(
        (len(c.record_ids) for c in clusters if record.id in c.record_ids),
        1,
    )
    return VacancyAnalytics(
        age_days=age_days,
        seen_span_days=seen_span,
        times_seen=record.times_seen,
        posting_lag_days=lag,
        employer_open_vacancies=open_same,
        cluster_size=cluster_size,
    )


def employer_stats(
    employer_name: str,
    all_records: Sequence[VacancyRecord],
    clusters: Sequence[DuplicateCluster],
    *,
    evergreen_days: int,
    now: datetime | None = None,
) -> EmployerStats:
    now = now or datetime.now(UTC)
    norm = employer_name.strip().lower()
    own = [r for r in all_records if r.vacancy.employer.name.strip().lower() == norm]
    own_ids = {r.id for r in own}
    groups = sum(1 for c in clusters if any(rid in own_ids for rid in c.record_ids) and len(c.record_ids) > 1)
    evergreen = sum(
        1 for r in own if (now - (r.vacancy.published_at or r.first_seen_at)).days > evergreen_days
    )
    return EmployerStats(
        name=employer_name,
        vacancies_total=len(own),
        duplicate_groups=groups,
        evergreen_share=(evergreen / len(own)) if own else 0.0,
    )
