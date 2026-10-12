"""Выборка утреннего дайджеста: свежее за N дней, без скрытых и без скрытых работодателей."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from job_hunter.domain.models import VacancyRecord, VacancyState, VacancyStatus

# статусы, которым место не в дайджесте, а в воронке/архиве
_NOT_IN_DIGEST = frozenset(
    {
        VacancyStatus.HIDDEN,
        VacancyStatus.ARCHIVED,
        VacancyStatus.INTERESTED,
        VacancyStatus.APPLIED,
        VacancyStatus.RESPONDED,
        VacancyStatus.INTERVIEW,
        VacancyStatus.OFFER,
        VacancyStatus.REJECTED,
    }
)


def select_digest(
    records: Sequence[VacancyRecord],
    states: dict[int, VacancyState],
    hidden_employers: set[str],
    *,
    days: int = 1,
    now: datetime | None = None,
) -> list[VacancyRecord]:
    """Новые в базе за последние `days` дней, статус new, работодатель не скрыт."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    hidden_norm = {name.strip().lower() for name in hidden_employers}
    out: list[VacancyRecord] = []
    for record in records:
        if record.first_seen_at < cutoff:
            continue
        state = states.get(record.id)
        if state is not None and state.status in _NOT_IN_DIGEST:
            continue
        if record.vacancy.employer.name.strip().lower() in hidden_norm:
            continue
        out.append(record)
    return out
