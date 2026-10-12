"""Catch-up сбор: догоняем пропущенные запуски расписания.

FR-008: при старте (web/fetch) проверяем время последнего успешного сбора по
каждому источнику и, если прошло больше порога, запускаем догоняющий сбор.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from job_hunter.application.ingest import FetchStats, IngestVacancies
from job_hunter.bootstrap import AppContext
from job_hunter.domain.ports import SourceConnector
from job_hunter.infrastructure.db.engine import session_scope
from job_hunter.infrastructure.db.repositories import (
    SqlFetchRunRepository,
    SqlVacancyRepository,
)
from job_hunter.infrastructure.profile import load_profile

logger = logging.getLogger(__name__)


def overdue_sources(
    last_success: Mapping[str, datetime],
    sources: Sequence[str],
    *,
    now: datetime,
    threshold_hours: float,
) -> list[str]:
    """Чистая функция: источники, у которых последний успех старше порога.

    Отсутствие записи считается просрочкой (источник никогда не собирался).
    Ровно порог — ещё не просрочка (<= threshold).
    """
    threshold = timedelta(hours=threshold_hours)
    out: list[str] = []
    for source in sources:
        last = last_success.get(source)
        if last is None or (now - last) > threshold:
            out.append(source)
    return out


def run_catchup(ctx: AppContext, *, now: datetime | None = None) -> list[FetchStats]:
    """Выполнить догоняющий сбор для просроченных источников.

    Собирает только по пресетам профиля, с тем же лимитом, что и планировщик.
    Всегда возвращает список статистик (может быть пустым).
    """
    moment = now or datetime.now(UTC)
    threshold = ctx.settings.scheduler.catch_up_hours
    enabled = [r for r in ctx.connectors if r.enabled]
    if not enabled:
        logger.info("Catch-up: нет включённых источников")
        return []

    with session_scope(ctx.engine) as session:
        run_repo = SqlFetchRunRepository(session)
        last_success = run_repo.get_all()

    source_names = [r.name for r in enabled]
    overdue = overdue_sources(last_success, source_names, now=moment, threshold_hours=threshold)
    if not overdue:
        logger.info("Catch-up: все источники собирались менее %.1f ч назад", threshold)
        return []

    logger.info("Catch-up: просрочены источники %s (порог %.1f ч)", overdue, threshold)

    profile = load_profile(ctx.settings.config_dir)
    queries: list[str | None]
    queries = list(profile.search_queries) if profile and profile.search_queries else [None]

    overdue_connectors: list[SourceConnector] = [r.connector for r in enabled if r.name in overdue]
    all_stats: list[FetchStats] = []
    limit = 100  # как в планировщике
    with session_scope(ctx.engine) as session:
        use_case = IngestVacancies(overdue_connectors, SqlVacancyRepository(session))
        run_repo = SqlFetchRunRepository(session)
        for query in queries:
            for stat in use_case.execute(query=query, limit=limit, now=moment, fetch_run_repo=run_repo):
                all_stats.append(stat)
                logger.info(
                    "Catch-up [%s/%s]: получено %d (новых %d)",
                    stat.source,
                    query or "широкий",
                    stat.fetched,
                    stat.created,
                )

    logger.info(
        "Catch-up завершён: %d источников, %d новых вакансий", len(overdue), sum(s.created for s in all_stats)
    )
    return all_stats
