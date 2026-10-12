"""Фоновый планировщик (APScheduler, MIT): сбор по пресетам профиля + триаж свежих.

Живёт внутри web-процесса (`job-hunter web`). Ресурс-политика LLM §7.1 соблюдается:
триаж батчем, выгрузка модели после.
"""

import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from job_hunter.application.analyze import Assessment
from job_hunter.application.catchup import run_catchup
from job_hunter.application.ingest import IngestVacancies
from job_hunter.application.llm_pipeline import _key
from job_hunter.application.llm_triage import triage_batch
from job_hunter.bootstrap import AppContext
from job_hunter.domain.profile import CandidateProfile
from job_hunter.infrastructure.db.engine import session_scope
from job_hunter.infrastructure.db.repositories import (
    SqlFetchRunRepository,
    SqlLlmCacheRepository,
    SqlVacancyRepository,
)
from job_hunter.infrastructure.llm import build_llm
from job_hunter.infrastructure.profile import load_profile

logger = logging.getLogger(__name__)


def start_scheduler(ctx: AppContext) -> BackgroundScheduler | None:
    cfg = ctx.settings.scheduler
    if not cfg.enabled:
        logger.info("Планировщик выключен (settings.scheduler.enabled=false)")
        return None
    scheduler = BackgroundScheduler()
    for hh_mm in cfg.fetch_times:
        hour, minute = (int(p) for p in hh_mm.split(":"))
        scheduler.add_job(
            _fetch_and_triage,
            CronTrigger(hour=hour, minute=minute),
            args=[ctx],
            id=f"fetch-{hh_mm}",
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True,
        )
    # FR-008: одноразовый catch-up при старте — не блокирует поднятие UI
    scheduler.add_job(
        run_catchup,
        DateTrigger(),
        args=[ctx],
        id="catchup-startup",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Планировщик запущен: сборы в %s (локальное время); catch-up порог %.1f ч",
        ", ".join(cfg.fetch_times),
        cfg.catch_up_hours,
    )
    return scheduler


def _fetch_and_triage(ctx: AppContext) -> None:
    cfg = ctx.settings.scheduler
    connectors = [r.connector for r in ctx.connectors if r.enabled]
    if not connectors:
        return
    profile = load_profile(ctx.settings.config_dir)
    queries: list[str | None]
    queries = list(profile.search_queries) if profile and profile.search_queries else [None]

    with session_scope(ctx.engine) as session:
        use_case = IngestVacancies(connectors, SqlVacancyRepository(session))
        run_repo = SqlFetchRunRepository(session)
        for query in queries:
            for stat in use_case.execute(query=query, limit=100, fetch_run_repo=run_repo):
                logger.info(
                    "Планировщик fetch [%s/%s]: получено %d (новых %d)",
                    stat.source,
                    query or "широкий",
                    stat.fetched,
                    stat.created,
                )

    if cfg.triage_after_fetch and profile is not None:
        _triage_fresh(ctx, profile)


def _triage_fresh(ctx: AppContext, profile: CandidateProfile) -> None:
    """Триаж вакансий, впервые увиденных за последние сутки и не имеющих кэша решения."""
    cutoff = datetime.now(UTC) - timedelta(days=1)
    with session_scope(ctx.engine) as session:
        records = SqlVacancyRepository(session).list_recent(limit=300)
        cache = SqlLlmCacheRepository(session)
        fresh = [
            r
            for r in records
            if r.first_seen_at >= cutoff and cache.get(_key("triage", r.content_hash)) is None
        ]
        fresh = fresh[: ctx.settings.scheduler.triage_pool_limit]
        if not fresh:
            logger.info("Планировщик triage: свежих без решения нет")
            return
        llm = build_llm(ctx.settings, ctx.http)
        if llm is None:
            logger.warning("Планировщик triage: LLM недоступна — пропуск")
            return
        pairs = [(r, Assessment(r.id, 50.0, False, (), ())) for r in fresh]
        try:
            _, dropped = triage_batch(pairs, llm=llm, cache=cache, profile=profile)
            logger.info("Планировщик triage: %d свежих, отсеяно %d", len(fresh), dropped)
        finally:
            if ctx.settings.llm.resource_policy.unload_after_batch:
                llm.unload_used()
