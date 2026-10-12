"""Web-слой (FastAPI + Jinja2, server-side). Только localhost, чтение + действия воронки.

Скоуп и сценарии — docs/UI_SCENARIOS.md. LLM по умолчанию читается из кэша;
on-demand анализ карточки — через POST /vacancies/{id}/analyze.
"""

import logging
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from job_hunter.application.analyze import Assessment, VacancyAnalyzer
from job_hunter.application.dedup import find_duplicate_clusters
from job_hunter.application.digest import select_digest
from job_hunter.application.employers import compute_vacancy_analytics, employer_stats
from job_hunter.application.llm_pipeline import _key as _key_llm
from job_hunter.application.llm_pipeline import enrich_one
from job_hunter.application.ranking import assemble_from_cache
from job_hunter.bootstrap import AppContext
from job_hunter.domain.models import (
    PIPELINE_STATUSES,
    VacancyRecord,
    VacancyState,
    VacancyStatus,
)
from job_hunter.domain.profile import CandidateProfile, ScoringConfig
from job_hunter.infrastructure.config import load_scoring_config
from job_hunter.infrastructure.db.engine import session_scope
from job_hunter.infrastructure.db.repositories import (
    SqlLlmCacheRepository,
    SqlStateRepository,
    SqlVacancyRepository,
)
from job_hunter.infrastructure.llm import build_llm
from job_hunter.infrastructure.profile import load_profile

logger = logging.getLogger(__name__)

_PAGE_SIZE = 50
_reports_dir = Path("data/reports")


def create_app(ctx: AppContext) -> FastAPI:
    web_dir = resources.files("job_hunter.presentation.web")
    with resources.as_file(web_dir / "templates") as tdp, resources.as_file(web_dir / "static") as sdp:
        templates = Jinja2Templates(directory=str(tdp))
        templates.env.globals["status_ru"] = _STATUS_RU.get
        app = FastAPI(title="Job Hunter", docs_url=None, redoc_url=None)
        app.mount("/static", StaticFiles(directory=str(sdp)), name="static")
        _register(app, ctx, templates)
    return app


_STATUS_RU = {
    VacancyStatus.NEW.value: "новая",
    VacancyStatus.INTERESTED.value: "✨ интересно",
    VacancyStatus.HIDDEN.value: "🙈 скрыта",
    VacancyStatus.APPLIED.value: "📨 отклик",
    VacancyStatus.RESPONDED.value: "💬 ответ",
    VacancyStatus.INTERVIEW.value: "🗣 интервью",
    VacancyStatus.OFFER.value: "🏆 оффер",
    VacancyStatus.REJECTED.value: "❌ отказ",
    VacancyStatus.ARCHIVED.value: "🗄 архив",
}
# ключи — строки VacancyStatus.value (mypy типизирует .value у StrEnum хитро)
_PIPE_NEXT: dict[str, VacancyStatus] = {
    "interested": VacancyStatus.APPLIED,
    "applied": VacancyStatus.RESPONDED,
    "responded": VacancyStatus.INTERVIEW,
    "interview": VacancyStatus.OFFER,
}
_PIPE_PREV: dict[str, VacancyStatus] = {v.value: VacancyStatus(k) for k, v in _PIPE_NEXT.items()}


def _register(app: FastAPI, ctx: AppContext, templates: Jinja2Templates) -> None:
    settings = ctx.settings

    def _session() -> AbstractContextManager[Session]:
        return session_scope(ctx.engine)

    def _all_records(
        max_n: int = 2000,
    ) -> tuple[list[VacancyRecord], dict[int, VacancyState], set[str]]:
        with _session() as session:
            repo = SqlVacancyRepository(session)
            state_repo = SqlStateRepository(session)
            return (
                list(repo.list_recent(limit=max_n)),
                state_repo.states(),
                state_repo.hidden_employers(),
            )

    def _ranked(
        records: list[VacancyRecord],
        *,
        profile: CandidateProfile | None,
        cfg: ScoringConfig,
    ) -> list[tuple[VacancyRecord, Assessment]]:
        with _session() as session:
            cache = SqlLlmCacheRepository(session)
            return assemble_from_cache(records, profile=profile, config=cfg, cache=cache)

    def _back(next_url: str | None, default: str = "/") -> RedirectResponse:
        target = next_url if next_url and next_url.startswith("/") else default
        return RedirectResponse(url=target, status_code=303)

    # --- чтение -----------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def digest(request: Request, days: int = Query(default=1, ge=1, le=30)) -> HTMLResponse:
        cfg = load_scoring_config(settings.config_dir)
        profile = load_profile(settings.config_dir)
        records, states, hidden = _all_records()
        fresh = select_digest(records, states, hidden, days=days)
        rows = _ranked(fresh, profile=profile, cfg=cfg)
        with _session() as session:
            cache = SqlLlmCacheRepository(session)
            triage_state = {r.id: cache.get(_key_llm("triage", r.content_hash)) for r, _ in rows}
        digest_rows = [
            {
                "row": _record_view(r),
                "a": a,
                "state": states.get(r.id),
                "triage": triage_state.get(r.id),  # None = ещё не проверена LLM-триажем
            }
            for r, a in rows
            if not a.rejected
        ]
        # сценарий-дайджест: по умолчанию показываем только проверенные триажем (keep)
        show_unvetted = bool(request.query_params.get("unvetted"))
        if profile is not None and not show_unvetted:
            unvetted_count = sum(1 for d in digest_rows if d["triage"] is None)
            digest_rows = [d for d in digest_rows if d["triage"] == "keep"]
        else:
            unvetted_count = 0
        with _session() as session:
            repo = SqlVacancyRepository(session)
            sources = [
                (r.name, repo.count(source=r.name))
                for r in ctx.connectors
                if r.enabled and r.connector.is_available()
            ]
        return templates.TemplateResponse(
            request,
            "digest.html",
            {
                "rows": digest_rows,
                "days": days,
                "total_today": len(fresh),
                "unvetted_count": unvetted_count,
                "total_in_base": sum(n for _, n in sources),
                "sources": sources,
                "has_profile": profile is not None,
                "reports": sorted(_reports_dir.glob("report-*.md"), reverse=True)[:5],
            },
        )

    @app.get("/vacancies", response_class=HTMLResponse)
    def vacancies(
        request: Request,
        q: str | None = Query(default=None),
        source: str | None = Query(default=None),
        all_states: int = Query(default=0),
        page: int = Query(default=1, ge=1),
    ) -> HTMLResponse:
        records, states, hidden = _all_records()
        q_low = (q or "").strip().lower()
        rows = []
        for record in records:
            v = record.vacancy
            st = states.get(record.id)
            if not all_states:
                if st and st.status in (VacancyStatus.HIDDEN, VacancyStatus.ARCHIVED):
                    continue
                if v.employer.name.strip().lower() in {h.strip().lower() for h in hidden}:
                    continue
            if source and v.source != source:
                continue
            if q_low and q_low not in f"{v.title} {v.employer.name}".lower():
                continue
            rows.append(_record_view(record, states.get(record.id)))
        total = len(rows)
        page_rows = rows[(page - 1) * _PAGE_SIZE : page * _PAGE_SIZE]
        return templates.TemplateResponse(
            request,
            "vacancies.html",
            {
                "records": page_rows,
                "total": total,
                "q": q or "",
                "source": source or "",
                "all_states": all_states,
                "page": page,
                "pages": max(1, -(-total // _PAGE_SIZE)),
                "sources": [r.name for r in ctx.connectors],
            },
        )

    @app.get("/vacancies/{record_id}", response_class=HTMLResponse)
    def vacancy_detail(request: Request, record_id: int) -> HTMLResponse:
        cfg = load_scoring_config(settings.config_dir)
        profile = load_profile(settings.config_dir)
        records, states, hidden = _all_records()
        record = next((r for r in records if r.id == record_id), None)
        if record is None:
            raise HTTPException(status_code=404)
        rows = _ranked([record], profile=profile, cfg=cfg)
        assessment: Assessment | None = rows[0][1] if rows else None
        clusters = find_duplicate_clusters(records, similarity_threshold=cfg.repost_similarity)
        analytics = compute_vacancy_analytics(record, records, clusters)
        employer = employer_stats(
            record.vacancy.employer.name, records, clusters, evergreen_days=cfg.evergreen_days
        )
        return templates.TemplateResponse(
            request,
            "vacancy_detail.html",
            {
                "record": _record_view(record, states.get(record.id)),
                "assessment": assessment,
                "analytics": analytics,
                "employer": employer,
                "is_hidden_employer": employer.name in hidden,
                "statuses": list(VacancyStatus),
                "pipeline_next": _PIPE_NEXT.get(states[record.id].status.value)
                if record.id in states
                else None,
                "status_ru": _STATUS_RU,
            },
        )

    @app.get("/pipeline", response_class=HTMLResponse)
    def pipeline(request: Request) -> HTMLResponse:
        records, states, hidden = _all_records()
        by_id = {r.id: r for r in records}
        columns: dict[str, list[dict[str, Any]]] = {s.value: [] for s in PIPELINE_STATUSES}
        for state in states.values():
            if state.status not in PIPELINE_STATUSES or state.record_id not in by_id:
                continue
            record = by_id[state.record_id]
            columns[state.status.value].append(
                {
                    "row": _record_view(record, state),
                    "next": _PIPE_NEXT.get(state.status.value),
                    "prev": _PIPE_PREV.get(state.status.value),
                }
            )
        graveyard = [
            _record_view(by_id[s.record_id], s)
            for s in states.values()
            if s.status in (VacancyStatus.REJECTED, VacancyStatus.ARCHIVED) and s.record_id in by_id
        ]
        return templates.TemplateResponse(
            request,
            "pipeline.html",
            {"columns": columns, "graveyard": graveyard, "status_ru": _STATUS_RU},
        )

    @app.get("/clusters", response_class=HTMLResponse)
    def clusters_page(request: Request) -> HTMLResponse:
        cfg = load_scoring_config(settings.config_dir)
        profile = load_profile(settings.config_dir)
        records, states, hidden = _all_records()
        by_id = {r.id: r for r in records}
        groups = find_duplicate_clusters(records, similarity_threshold=cfg.cluster_similarity)
        hidden_norm = {name.strip().lower() for name in hidden}

        with _session() as session:
            cache = SqlLlmCacheRepository(session)

            def visible(rid: int) -> bool:
                rec = by_id[rid]
                state = states.get(rid)
                if state and state.status in (VacancyStatus.HIDDEN, VacancyStatus.ARCHIVED):
                    return False
                if rec.vacancy.employer.name.strip().lower() in hidden_norm:
                    return False
                # с профилем: триаж-drop прячем, непроверенное оставляем
                return not (profile is not None and cache.get(_key_llm("triage", rec.content_hash)) == "drop")

            view: list[dict[str, Any]] = [
                {
                    "size": len(g.record_ids),
                    "similarity": g.min_similarity,
                    "canonical": _record_view(by_id[g.canonical_id]),
                    "members": [_record_view(by_id[rid]) for rid in g.record_ids if visible(rid)],
                }
                for g in groups
                if any(visible(rid) for rid in g.record_ids)
            ]
        view.sort(key=lambda g: -g["size"])
        return templates.TemplateResponse(
            request, "clusters.html", {"groups": view, "threshold": cfg.cluster_similarity}
        )

    @app.get("/employers", response_class=HTMLResponse)
    def employers_page(request: Request) -> HTMLResponse:
        _, _, hidden = _all_records()
        return templates.TemplateResponse(request, "employers.html", {"hidden": sorted(hidden)})

    @app.get("/report", response_class=HTMLResponse)
    def report(request: Request, limit: int = Query(default=50, ge=1, le=500)) -> HTMLResponse:
        cfg = load_scoring_config(settings.config_dir)
        profile = load_profile(settings.config_dir)
        records, states, hidden = _all_records()
        rows = _ranked(records, profile=profile, cfg=cfg)
        ranked: list[tuple[VacancyRecord, Assessment]] = []
        rejected_n = 0
        hidden_norm = {h.strip().lower() for h in hidden}
        for r, a in rows:
            st = states.get(r.id)
            if st and st.status in (VacancyStatus.HIDDEN, VacancyStatus.ARCHIVED):
                continue
            if r.vacancy.employer.name.strip().lower() in hidden_norm:
                continue
            if a.rejected:
                rejected_n += 1
            else:
                ranked.append((r, a))
        return templates.TemplateResponse(
            request,
            "report.html",
            {
                "ranked": [{"row": _record_view(r, states.get(r.id)), "a": a} for r, a in ranked[:limit]],
                "total_in_base": len(records),
                "rejected": rejected_n,
                "limit": limit,
                "has_profile": profile is not None,
            },
        )

    @app.get("/reports/{name}", response_class=HTMLResponse)
    def report_file(request: Request, name: str) -> HTMLResponse:
        if "/" in name or ".." in name or not name.endswith(".md"):
            raise HTTPException(status_code=400)
        path = _reports_dir / name
        if not path.exists():
            raise HTTPException(status_code=404)
        return templates.TemplateResponse(
            request,
            "report_file.html",
            {"name": name, "content": path.read_text(encoding="utf-8")},
        )

    # --- действия ---------------------------------------------------------

    @app.post("/vacancies/{record_id}/status")
    def set_status(
        record_id: int,
        status: Annotated[str, Form()],
        next: Annotated[str | None, Form()] = None,
    ) -> RedirectResponse:
        new_status = VacancyStatus(status)
        applied_at = datetime.now(UTC) if new_status is VacancyStatus.APPLIED else None
        with _session() as session:
            SqlStateRepository(session).set_status(record_id, new_status, applied_at=applied_at)
        return _back(next or f"/vacancies/{record_id}", default="/")

    @app.post("/vacancies/{record_id}/note")
    def set_note(
        record_id: int,
        note: Annotated[str | None, Form()] = None,
        next: Annotated[str | None, Form()] = None,
    ) -> RedirectResponse:
        with _session() as session:
            SqlStateRepository(session).set_note(record_id, note or None)
        return _back(next or f"/vacancies/{record_id}")

    @app.post("/vacancies/{record_id}/analyze")
    def analyze_now(
        record_id: int,
        next: Annotated[str | None, Form()] = None,
    ) -> RedirectResponse:
        cfg = load_scoring_config(settings.config_dir)
        profile = load_profile(settings.config_dir)
        with _session() as session:
            repo = SqlVacancyRepository(session)
            record = repo.get(record_id)
            if record is None:
                raise HTTPException(status_code=404)
            llm = build_llm(settings, ctx.http)
            if llm is None:
                return _back(next, default=f"/vacancies/{record_id}")  # msg: LLM недоступен
            cache = SqlLlmCacheRepository(session)
            analyzer = VacancyAnalyzer(profile, cfg)
            base = analyzer.assess_all([record])[0][1]
            try:
                enrich_one(record, base, llm=llm, cache=cache, profile=profile, config=cfg)
            finally:
                if settings.llm.resource_policy.unload_after_batch:
                    llm.unload_used()
        return _back(next, default=f"/vacancies/{record_id}")

    @app.post("/employers/hide")
    def employer_hide(
        name: Annotated[str, Form()], next: Annotated[str | None, Form()] = None
    ) -> RedirectResponse:
        with _session() as session:
            SqlStateRepository(session).hide_employer(name)
        return _back(next, default="/employers")

    @app.post("/employers/unhide")
    def employer_unhide(
        name: Annotated[str, Form()], next: Annotated[str | None, Form()] = None
    ) -> RedirectResponse:
        with _session() as session:
            SqlStateRepository(session).unhide_employer(name)
        return _back(next, default="/employers")


def _record_view(record: VacancyRecord, state: VacancyState | None = None) -> dict[str, Any]:
    """Канонический view-словарь вакансии для шаблонов."""
    vacancy = record.vacancy
    salary = vacancy.salary
    salary_str = "—"
    if salary and (salary.min_amount or salary.max_amount):
        salary_str = "–".join(
            f"{v:,}".replace(",", " ") if v else "…" for v in (salary.min_amount, salary.max_amount)
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
        "status": state.status.value if state else VacancyStatus.NEW.value,
        "note": state.note if state else None,
        "applied_at": state.applied_at.date().isoformat() if state and state.applied_at else None,
    }


__all__ = ["create_app"]
