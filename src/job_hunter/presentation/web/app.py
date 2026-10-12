"""Web-слой (FastAPI + Jinja2, server-side). Только localhost, только чтение.

Данные: SQLite через репозитории; скоринг — rules живьём + LLM из кэша (UI не гоняет GPU).
"""

import logging
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from job_hunter.application.analyze import Assessment
from job_hunter.application.ranking import assemble_from_cache
from job_hunter.bootstrap import AppContext
from job_hunter.domain.models import VacancyRecord
from job_hunter.infrastructure.config import Settings, load_scoring_config
from job_hunter.infrastructure.db.engine import session_scope
from job_hunter.infrastructure.db.repositories import SqlLlmCacheRepository, SqlVacancyRepository
from job_hunter.infrastructure.profile import load_profile

logger = logging.getLogger(__name__)

_PAGE_SIZE = 50
_reports_dir = Path("data/reports")


def create_app(ctx: AppContext) -> FastAPI:
    templates_dir = resources.files("job_hunter.presentation.web") / "templates"
    static_dir = resources.files("job_hunter.presentation.web") / "static"
    with resources.as_file(templates_dir) as tdp, resources.as_file(static_dir) as sdp:
        templates = Jinja2Templates(directory=str(tdp))
        templates.env.globals["score_class"] = _score_class
        app = FastAPI(title="Job Hunter", docs_url=None, redoc_url=None)
        app.mount("/static", StaticFiles(directory=str(sdp)), name="static")

        _register(app, ctx, templates)
    return app


def _score_class(total: float) -> str:
    if total >= 60:
        return "score good"
    if total >= 40:
        return "score mid"
    return "score low"


def _register(app: FastAPI, ctx: AppContext, templates: Jinja2Templates) -> None:
    settings: Settings = ctx.settings

    def _repo():
        return session_scope(ctx.engine)  # каждый запрос — своя сессия (контекстный менеджер)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        with _repo() as session:
            repo = SqlVacancyRepository(session)
            rows: dict[str, int] = {}
            for registered in ctx.connectors:
                rows[registered.name] = repo.count(source=registered.name)
        sources = [
            {
                "name": r.name,
                "enabled": r.enabled,
                "available": r.connector.is_available(),
                "count": rows.get(r.name, 0),
                "reason": "" if r.connector.is_available() else r.connector.unavailable_reason(),
            }
            for r in ctx.connectors
        ]
        reports = sorted(_reports_dir.glob("report-*.md"), reverse=True)[:5]
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "total": sum(rows.values()),
                "sources": sources,
                "has_profile": load_profile(settings.config_dir) is not None,
                "reports": reports,
            },
        )

    @app.get("/vacancies", response_class=HTMLResponse)
    def vacancies(
        request: Request,
        q: str | None = Query(default=None),
        source: str | None = Query(default=None),
        page: int = Query(default=1, ge=1),
    ):
        offset = (page - 1) * _PAGE_SIZE
        with _repo() as session:
            repo = SqlVacancyRepository(session)
            records = repo.list_recent(
                source=source, query=q, limit=_PAGE_SIZE, offset=offset
            )
            total = repo.count(source=source, query=q)
        return templates.TemplateResponse(
            "vacancies.html",
            {
                "request": request,
                "records": [_record_view(r) for r in records],
                "total": total,
                "q": q or "",
                "source": source or "",
                "page": page,
                "pages": max(1, -(-total // _PAGE_SIZE)),
                "sources": [r.name for r in ctx.connectors],
            },
        )

    @app.get("/vacancies/{record_id}", response_class=HTMLResponse)
    def vacancy_detail(request: Request, record_id: int):
        with _repo() as session:
            repo = SqlVacancyRepository(session)
            record = repo.get(record_id)
            if record is None:
                raise HTTPException(status_code=404)
            cfg = load_scoring_config(settings.config_dir)
            cache = SqlLlmCacheRepository(session)
            rows = assemble_from_cache(
                [record], profile=load_profile(settings.config_dir), config=cfg, cache=cache
            )
        assessment = rows[0][1] if rows else None
        return templates.TemplateResponse(
            "vacancy_detail.html",
            {"request": request, "record": _record_view(record), "assessment": assessment},
        )

    @app.get("/report", response_class=HTMLResponse)
    def report(request: Request, limit: int = Query(default=50, ge=1, le=500)):
        cfg = load_scoring_config(settings.config_dir)
        profile = load_profile(settings.config_dir)
        with _repo() as session:
            repo = SqlVacancyRepository(session)
            cache = SqlLlmCacheRepository(session)
            records = repo.list_recent(limit=1000)
            rows = assemble_from_cache(records, profile=profile, config=cfg, cache=cache)
            total_in_base = repo.count()
        ranked = [(r, a) for r, a in rows if not a.rejected][:limit]
        rejected_n = sum(1 for _, a in rows if a.rejected)
        return templates.TemplateResponse(
            "report.html",
            {
                "request": request,
                "ranked": [{"row": _record_view(r), "a": a} for r, a in ranked],
                "total_in_base": total_in_base,
                "rejected": rejected_n,
                "limit": limit,
                "has_profile": profile is not None,
            },
        )

    @app.get("/reports/{name}", response_class=HTMLResponse)
    def report_file(request: Request, name: str):
        if "/" in name or ".." in name or not name.endswith(".md"):
            raise HTTPException(status_code=400)
        path = _reports_dir / name
        if not path.exists():
            raise HTTPException(status_code=404)
        return templates.TemplateResponse(
            "report_file.html",
            {"request": request, "name": name, "content": path.read_text(encoding="utf-8")},
        )


def _record_view(record: VacancyRecord) -> dict[str, Any]:
    """Канонический view-словарь вакансии для шаблонов (единое место форматирования)."""
    vacancy = record.vacancy
    salary = vacancy.salary
    salary_str = "—"
    if salary and (salary.min_amount or salary.max_amount):
        salary_str = "–".join(
            f"{v:,}".replace(",", " ") if v else "…"
            for v in (salary.min_amount, salary.max_amount)
        ) + (f" {salary.currency}" if salary.currency else "")
    return {
        "id": record.id,
        "title": vacancy.title,
        "employer": vacancy.employer.name,
        "source": vacancy.source,
        "url": vacancy.url,
        "salary": salary_str,
        "seniority": vacancy.seniority or "—",
        "remote": vacancy.remote.value,
        "locations": ", ".join(vacancy.locations) or "—",
        "skills": ", ".join(vacancy.skills) or "—",
        "description": vacancy.description,
        "published": vacancy.published_at.date().isoformat() if vacancy.published_at else "—",
        "times_seen": record.times_seen,
        "first_seen": record.first_seen_at.date().isoformat(),
    }


# Assessment импортирован для типов сигналов в шаблонах через домен, реэкспорт не нужен
__all__ = ["create_app"]
