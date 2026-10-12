"""CLI (Typer + rich)."""

import logging
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from job_hunter.application.analyze import Assessment, VacancyAnalyzer
from job_hunter.application.dedup import find_duplicate_clusters
from job_hunter.application.ingest import FetchStats, IngestVacancies
from job_hunter.application.llm_pipeline import enrich_with_llm
from job_hunter.application.llm_triage import triage_batch
from job_hunter.application.profile_import import import_profile, merge_profiles, read_resume_text
from job_hunter.application.report import render_markdown
from job_hunter.bootstrap import AppContext, build_context
from job_hunter.domain.models import Salary, VacancyRecord
from job_hunter.domain.profile import CandidateProfile
from job_hunter.infrastructure.config import load_scoring_config
from job_hunter.infrastructure.db.engine import session_scope
from job_hunter.infrastructure.db.migrate import upgrade_head
from job_hunter.infrastructure.db.repositories import SqlLlmCacheRepository, SqlVacancyRepository
from job_hunter.infrastructure.llm import build_llm
from job_hunter.infrastructure.llm.ollama import OllamaLLM
from job_hunter.infrastructure.logging import setup_logging
from job_hunter.infrastructure.profile import load_profile, profile_path, save_profile

logger = logging.getLogger(__name__)
console = Console(width=150)  # фиксированная ширина — в пайпах rich ужимается до 80

app = typer.Typer(no_args_is_help=True, help="Job Hunter — локальный ассистент поиска работы")
sources_app = typer.Typer(no_args_is_help=True, help="Источники вакансий")
vacancies_app = typer.Typer(no_args_is_help=True, help="Вакансии в локальной базе")
profile_app = typer.Typer(no_args_is_help=True, help="Профиль кандидата")
app.add_typer(sources_app, name="sources")
app.add_typer(vacancies_app, name="vacancies")
app.add_typer(profile_app, name="profile")

VerboseOption = Annotated[bool, typer.Option("--verbose", "-v", help="Подробные логи")]


def _prepare(verbose: bool) -> AppContext:
    setup_logging(verbose=verbose)
    ctx = build_context()
    upgrade_head(ctx.settings.database_url)  # миграции — идемпотентны
    return ctx


def _fmt_salary(salary: Salary | None) -> str:
    if salary is None:
        return "—"
    left = f"{salary.min_amount:,}".replace(",", " ") if salary.min_amount else "…"
    right = f"{salary.max_amount:,}".replace(",", " ") if salary.max_amount else "…"
    currency = f" {salary.currency}" if salary.currency else ""
    return f"{left}–{right}{currency}"


def _fmt_date(value: datetime | None) -> str:
    return value.date().isoformat() if value else "—"


@app.command("init")
def init_db(verbose: VerboseOption = False) -> None:
    """Создать БД и применить миграции."""
    ctx = _prepare(verbose)
    with suppress(Exception):
        ctx.close()
    console.print(f"[green]OK[/green] БД готова: {ctx.settings.database_url}")


@sources_app.command("list")
def sources_list(verbose: VerboseOption = False) -> None:
    """Показать источники: включён ли, доступен ли, что не так."""
    setup_logging(verbose=verbose)
    ctx = build_context()
    table = Table(title="Источники вакансий")
    for column in ("Источник", "Включён", "Доступен", "Комментарий"):
        table.add_column(column)
    for registered in ctx.connectors:
        available = registered.connector.is_available()
        reason = "" if available else registered.connector.unavailable_reason()
        table.add_row(
            registered.name,
            "✅" if registered.enabled else "⛔",
            "✅" if available else "⛔",
            reason,
        )
    console.print(table)
    ctx.close()


def _fetch_queries(ctx: AppContext, query: str | None) -> list[str | None]:
    """Без явного -q берём пресеты из профиля; нет и их — один широкий запрос."""
    if query:
        return [query]
    profile = load_profile(ctx.settings.config_dir)
    if profile and profile.search_queries:
        return list(profile.search_queries)
    return [None]


@app.command("fetch")
def fetch(
    query: Annotated[str | None, typer.Option("--query", "-q", help="Поисковая строка")] = None,
    source: Annotated[str | None, typer.Option("--source", "-s", help="Только один источник")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Максимум вакансий на источник")] = 100,
    verbose: VerboseOption = False,
) -> None:
    """Собрать вакансии из включённых источников в локальную базу."""
    ctx = _prepare(verbose)
    connectors = [r.connector for r in ctx.connectors if r.enabled and (source is None or r.name == source)]
    if not connectors:
        console.print("[red]Нет подходящих источников[/red] — смотрите `job-hunter sources list`")
        raise typer.Exit(code=1)

    all_stats: list[tuple[str, FetchStats]] = []
    with session_scope(ctx.engine) as session:
        use_case = IngestVacancies(connectors, SqlVacancyRepository(session))
        for one_query in _fetch_queries(ctx, query):
            label = one_query or "(широкий)"
            logger.info("Запрос: %s", label)
            for stat in use_case.execute(source_name=source, query=one_query, limit=limit):
                all_stats.append((label, stat))

    table = Table(title="Результат сбора")
    for column in ("Запрос", "Источник", "Получено", "Новых", "Обновлено", "Примечание"):
        table.add_column(column)
    for label, stat in all_stats:
        table.add_row(
            label,
            stat.source,
            str(stat.fetched),
            str(stat.created),
            str(stat.updated),
            stat.skipped_reason or "",
        )
    console.print(table)
    ctx.close()


@vacancies_app.command("list")
def vacancies_list(
    source: Annotated[str | None, typer.Option("--source", "-s")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 20,
    offset: Annotated[int, typer.Option("--offset")] = 0,
    verbose: VerboseOption = False,
) -> None:
    """Список вакансий из базы (свежие сверху)."""
    ctx = _prepare(verbose)
    with session_scope(ctx.engine) as session:
        repo = SqlVacancyRepository(session)
        total = repo.count(source=source)
        records = repo.list_recent(source=source, limit=limit, offset=offset)

    table = Table(title=f"Вакансии (всего в базе: {total})")
    widths = {
        "ID": 5,
        "Дата": 11,
        "Название": 34,
        "Компания": 18,
        "Зарплата": 18,
        "Грейд": 8,
        "Формат": 8,
        "Источник": 12,
        "x": 3,
    }
    for column, width in widths.items():
        table.add_column(column, overflow="fold", max_width=width, no_wrap=(column in {"ID", "Дата", "x"}))
    for rec in records:
        vacancy = rec.vacancy
        table.add_row(
            str(rec.id),
            _fmt_date(vacancy.published_at),
            vacancy.title,
            vacancy.employer.name,
            _fmt_salary(vacancy.salary),
            vacancy.seniority or "—",
            vacancy.remote.value,
            vacancy.source,
            str(rec.times_seen),
        )
    console.print(table)
    ctx.close()


@vacancies_app.command("show")
def vacancies_show(record_id: int, verbose: VerboseOption = False) -> None:
    """Карточка вакансии по ID из `vacancies list`."""
    ctx = _prepare(verbose)
    with session_scope(ctx.engine) as session:
        record: VacancyRecord | None = SqlVacancyRepository(session).get(record_id)
    if record is None:
        console.print(f"[red]Запись {record_id} не найдена[/red]")
        raise typer.Exit(code=1)

    vacancy = record.vacancy
    skills = ", ".join(vacancy.skills) if vacancy.skills else "—"
    locations = ", ".join(vacancy.locations) if vacancy.locations else "—"
    body = (
        f"[bold]{vacancy.title}[/bold]\n"
        f"Компания: {vacancy.employer.name}\n"
        f"Источник: {vacancy.source} · {_fmt_date(vacancy.published_at)} · "
        f"видели {record.times_seen} раз (с {_fmt_date(record.first_seen_at)})\n"
        f"Зарплата: {_fmt_salary(vacancy.salary)} · Грейд: {vacancy.seniority or '—'} · "
        f"Формат: {vacancy.remote.value} · Занятость: {vacancy.employment.value}\n"
        f"Локации: {locations}\n"
        f"Навыки: {skills}\n"
        f"Ссылка: {vacancy.url}\n\n"
        f"{vacancy.description or '(описание появится на этапе M1 — обогащение деталями)'}"
    )
    console.print(Panel(body, title=f"#{record.id}", expand=False))
    ctx.close()


def _ask_list(prompt: str, default: str = "") -> tuple[str, ...]:
    raw = typer.prompt(f"{prompt} (через запятую)", default=default, show_default=False)
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@profile_app.command("init")
def profile_init(verbose: VerboseOption = False) -> None:
    """Интерактивный мастер создания/обновления профиля (config/profile.yaml)."""
    setup_logging(verbose=verbose)
    ctx = build_context()
    console.print("[bold]Профиль кандидата[/bold] — пустой ответ = оставить как есть/пропустить")
    roles = _ask_list("Желаемые роли")
    skills = _ask_list("Навыки (канонические: python, fastapi, postgresql)")
    seniorities = _ask_list("Грейды", "Senior, Middle")
    locations = _ask_list("Города (пусто = неважно)")
    remote_modes = _ask_list("Формат работы (remote / hybrid / onsite)", "remote")
    salary_raw = typer.prompt(
        "Минимально приемлемая зарплата (число, пусто = неважно)", default="", show_default=False
    )
    deal_breakers = _ask_list("Deal-breakers: фразы, убивающие вакансию")
    queries = _ask_list("Поисковые пресеты (используются в `fetch` без -q)")

    profile = CandidateProfile(
        desired_roles=roles,
        skills=skills,
        seniorities=seniorities,
        locations=locations,
        remote_modes=remote_modes,
        salary_min=int(salary_raw) if salary_raw.strip() else None,
        deal_breakers=deal_breakers,
        search_queries=queries,
    )
    path = save_profile(ctx.settings.config_dir, profile)
    console.print(f"[green]OK[/green] профиль сохранён: {path}")
    ctx.close()


@profile_app.command("show")
def profile_show(verbose: VerboseOption = False) -> None:
    """Показать текущий профиль."""
    setup_logging(verbose=verbose)
    ctx = build_context()
    profile = load_profile(ctx.settings.config_dir)
    if profile is None:
        console.print(
            "[yellow]Профиль не создан[/yellow] — `job-hunter profile init`; "
            f"файл: {profile_path(ctx.settings.config_dir)}"
        )
        raise typer.Exit(code=1)
    console.print(Panel(yaml_safe(profile), title=str(profile_path(ctx.settings.config_dir)), expand=False))
    ctx.close()


def yaml_safe(profile: CandidateProfile) -> str:
    import dataclasses

    import yaml

    return yaml.safe_dump(
        dict(dataclasses.asdict(profile).items()),
        allow_unicode=True,
        sort_keys=False,
    )


@app.command("report")
def report(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Сколько позиций в отчёте")] = 30,
    min_score: Annotated[float, typer.Option("--min-score", help="Отсечь ниже порога")] = 0.0,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Куда писать md-файл")] = None,
    llm_flag: Annotated[bool, typer.Option("--llm/--no-llm", help="LLM-обогащение короткого списка")] = True,
    llm_top: Annotated[
        int, typer.Option("--llm-top", help="Сколько топовых обогатить LLM (0 = batch_size)")
    ] = 0,
    verbose: VerboseOption = False,
) -> None:
    """Ранжированный отчёт по базе под ваш профиль (rules + опц. LLM-вердикты)."""
    ctx = _prepare(verbose)
    profile = load_profile(ctx.settings.config_dir)
    if profile is None:
        console.print(
            "[yellow]Профиль не найден[/yellow] — скоринг по общим сигналам. "
            "Создайте: `job-hunter profile init`"
        )
    cfg = load_scoring_config(ctx.settings.config_dir)

    with session_scope(ctx.engine) as session:
        repo = SqlVacancyRepository(session)
        total_in_base = repo.count()
        records = repo.list_recent(limit=5000)

    assessed = VacancyAnalyzer(profile, cfg).assess_all(records)
    rejected = [(r, a) for r, a in assessed if a.rejected]
    kept = [(r, a) for r, a in assessed if not a.rejected and a.total >= min_score]
    kept.sort(key=lambda pair: pair[1].total, reverse=True)

    triage_dropped = 0
    unvetted = 0
    if llm_flag and ctx.settings.llm.enabled:
        kept, triage_dropped, unvetted = _enrich_top_with_llm(ctx, kept, profile, cfg, llm_top, limit)

    top = kept[:limit]

    md = render_markdown(
        top,
        profile,
        total_in_base=total_in_base,
        rejected_count=len(rejected),
        triage_dropped=triage_dropped,
        unvetted_tail=unvetted,
    )
    out = output or Path("data/reports") / f"report-{datetime.now(UTC):%Y%m%d-%H%M%S}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")

    table = Table(title=f"Топ вакансий (всего в базе {total_in_base}, отбраковано {len(rejected)})")
    for column, width in {
        "#": 3,
        "Балл": 6,
        "LLM": 6,
        "Вакансия": 42,
        "Компания": 20,
        "Зарплата": 18,
        "Возраст": 8,
        "Источник": 12,
    }.items():
        table.add_column(column, overflow="fold", max_width=width)
    for idx, (record, assessment) in enumerate(top[:15], start=1):
        v = record.vacancy
        base = v.published_at or record.first_seen_at
        age = max(0, (datetime.now(UTC) - base).days)
        llm_sig = next((s for s in assessment.signals if s.name == "llm_fit"), None)
        table.add_row(
            str(idx),
            str(assessment.total),
            f"{llm_sig.points:+.0f}" if llm_sig else "—",
            v.title,
            v.employer.name,
            _fmt_salary(v.salary),
            f"{age} дн",
            v.source,
        )
    console.print(table)
    console.print(f"[green]OK[/green] отчёт: {out}")
    ctx.close()


def _enrich_top_with_llm(
    ctx: AppContext,
    kept: list[tuple[VacancyRecord, Assessment]],
    profile: CandidateProfile | None,
    cfg: object,
    llm_top: int,
    limit: int,
) -> tuple[list[tuple[VacancyRecord, Assessment]], int, int]:
    """LLM-проход: триаж пула (≥2×limit) → обогащение выживших из топа.

    Возвращает (список для отчёта, отсеяно триажем, сколько осталось без триажа).
    В отчёт попадают только триажированные: обогащённые топ-N + выжившие из пула.
    """
    from job_hunter.domain.profile import ScoringConfig

    assert isinstance(cfg, ScoringConfig)  # для mypy: сужаем object → ScoringConfig
    llm = build_llm(ctx.settings, ctx.http)
    if llm is None:
        console.print("[yellow]LLM недоступен[/yellow] — отчёт на чистых rules")
        return kept, 0, 0
    n_enrich = llm_top or ctx.settings.llm.resource_policy.batch_size
    n_triage = max(n_enrich, limit * 2)
    pool, rest = kept[:n_triage], kept[n_triage:]
    dropped = 0
    if isinstance(llm, OllamaLLM):
        logger.info("VRAM до батча: %s", llm.loaded_models_gb())
    try:
        with session_scope(ctx.engine) as session:
            cache = SqlLlmCacheRepository(session)
            survivors, dropped = triage_batch(pool, llm=llm, cache=cache, profile=profile)
            logger.info(
                "LLM: пул %d → выживших %d (триаж отсёк %d), обогащаем топ-%d, провайдер %s",
                len(pool),
                len(survivors),
                dropped,
                min(n_enrich, len(survivors)),
                llm.provider_name(),
            )
            # смена фазы: decision-модель выгружаем перед chat-фазой (§7.1: max_loaded_models=1)
            llm.unload_used()
            enriched = enrich_with_llm(
                survivors[:n_enrich], llm=llm, cache=cache, profile=profile, config=cfg
            )
    finally:
        if ctx.settings.llm.resource_policy.unload_after_batch:
            llm.unload_used()
        if isinstance(llm, OllamaLLM):
            logger.info("VRAM после батча: %s", llm.loaded_models_gb())
    enriched.sort(key=lambda pair: pair[1].total, reverse=True)
    vetted_not_enriched = survivors[n_enrich:]
    return enriched + vetted_not_enriched, dropped, len(rest)


@app.command("llm")
def llm_status(verbose: VerboseOption = False) -> None:
    """Статус LLM-слоя: провайдер, свободная RAM, модели по ролям, кто в VRAM."""
    setup_logging(verbose=verbose)
    import psutil

    ctx = build_context()
    table = Table(title="LLM-слой")
    table.add_column("Параметр")
    table.add_column("Значение")
    table.add_row("enabled", "✅" if ctx.settings.llm.enabled else "⛔")
    table.add_row("свободно RAM", f"{psutil.virtual_memory().available / 2**30:.1f} ГБ")
    client = build_llm(ctx.settings, ctx.http)
    if client is None:
        table.add_row("провайдер", "недоступен — rules-режим")
    else:
        table.add_row("провайдер", client.provider_name())
        if isinstance(client, OllamaLLM):
            for role in ("extract", "score"):
                table.add_row(f"модель {role}", client.pick_model(role) or "— (нет по RAM/наличию)")
            loaded = client.loaded_models_gb()
            table.add_row(
                "в VRAM сейчас",
                ", ".join(f"{name} ({gb} ГБ)" for name, gb in loaded.items()) or "пусто",
            )
    console.print(table)
    ctx.close()


@profile_app.command("import")
def profile_import_cmd(
    path: Annotated[Path, typer.Argument(help="Файл резюме (.md/.txt/.pdf/.docx)")],
    verbose: VerboseOption = False,
) -> None:
    """Импорт резюме через локальную LLM → обновление config/profile.yaml."""
    ctx = _prepare(verbose)
    try:
        text = read_resume_text(path)
    except (OSError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    llm = build_llm(ctx.settings, ctx.http)
    if llm is None:
        console.print("[red]LLM недоступен[/red] — для импорта нужна Ollama или LM Studio")
        raise typer.Exit(code=1)
    try:
        extracted = import_profile(llm, text)
    finally:
        if ctx.settings.llm.resource_policy.unload_after_batch:
            llm.unload_used()
    if extracted is None:
        console.print("[red]LLM вернула невалидный ответ[/red] — попробуйте ещё раз")
        raise typer.Exit(code=1)
    merged = merge_profiles(load_profile(ctx.settings.config_dir), extracted)
    console.print(Panel(yaml_safe(merged), title="Профиль после импорта (предпросмотр)", expand=False))
    if typer.confirm(f"Сохранить в {profile_path(ctx.settings.config_dir)}?"):
        save_profile(ctx.settings.config_dir, merged)
        console.print("[green]OK[/green] профиль обновлён")
    ctx.close()


@app.command("clusters")
def clusters(verbose: VerboseOption = False) -> None:
    """Группы дублей/перепостов одной и той же позиции."""
    ctx = _prepare(verbose)
    cfg = load_scoring_config(ctx.settings.config_dir)
    with session_scope(ctx.engine) as session:
        records = SqlVacancyRepository(session).list_recent(limit=5000)
    groups = find_duplicate_clusters(records, similarity_threshold=cfg.dedup_similarity)
    if not groups:
        console.print("Дублей не найдено.")
        ctx.close()
        return
    by_id = {r.id: r for r in records}
    for group in groups:
        console.print(
            f"[bold]Кластер (канон #{group.canonical_id}, сходство ≥ {group.min_similarity:.0f})[/bold]"
        )
        for rid in group.record_ids:
            rec = by_id[rid]
            console.print(
                f"  #{rid} · {rec.vacancy.title} · {rec.vacancy.employer.name} · "
                f"{rec.vacancy.source} · {_fmt_date(rec.vacancy.published_at)}"
            )
    ctx.close()


def main() -> None:
    app()
