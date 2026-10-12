"""Веб-слой: TestClient поверх in-memory базы с сид-записями."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from job_hunter.bootstrap import AppContext
from job_hunter.infrastructure.config import Settings
from job_hunter.infrastructure.db.engine import session_scope
from job_hunter.infrastructure.db.orm import Base
from job_hunter.infrastructure.db.repositories import SqlVacancyRepository
from job_hunter.infrastructure.http import HttpClient
from job_hunter.presentation.web.app import create_app

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture()
def web(tmp_path: Path, make_vacancy):
    # Файловая SQLite: TestClient гоняет ASGI в другом треде — in-memory БД там пустая
    engine = create_engine(f"sqlite:///{tmp_path}/web-test.db")
    Base.metadata.create_all(engine)
    settings = Settings(database_url=f"sqlite:///{tmp_path}/web-test.db", config_dir=tmp_path)
    http = HttpClient(user_agent="test", timeout=5, max_retries=1)
    ctx = AppContext(settings=settings, engine=engine, http=http, connectors=[])
    with session_scope(engine) as session:
        repo = SqlVacancyRepository(session)
        repo.upsert(make_vacancy("a", title="Аналитик данных"), seen_at=NOW)
        repo.upsert(make_vacancy("b", title="Девопс", employer_name="Плохая Компания"), seen_at=NOW)
    yield TestClient(create_app(ctx)), engine
    http.close()
    engine.dispose()


def _id(engine, title: str) -> int:
    with session_scope(engine) as session:
        return next(r.id for r in SqlVacancyRepository(session).list_recent() if r.vacancy.title == title)


def test_digest_lists_fresh(web) -> None:
    client, _ = web
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Аналитик данных" in resp.text and "Девопс" in resp.text


def test_hide_status_excludes_from_digest(web) -> None:
    client, engine = web
    rid = _id(engine, "Девопс")
    resp = client.post(
        f"/vacancies/{rid}/status", data={"status": "hidden", "next": "/"}, follow_redirects=True
    )
    assert resp.status_code == 200
    resp = client.get("/")
    assert "Девопс" not in resp.text and "Аналитик данных" in resp.text


def test_applied_appears_in_pipeline(web) -> None:
    client, engine = web
    rid = _id(engine, "Аналитик данных")
    client.post(f"/vacancies/{rid}/status", data={"status": "interested"}, follow_redirects=True)
    client.post(f"/vacancies/{rid}/status", data={"status": "applied"}, follow_redirects=True)
    resp = client.get("/pipeline")
    assert resp.status_code == 200 and "Аналитик данных" in resp.text
    # в дайджесте откликнутых больше нет
    assert "Аналитик данных" not in client.get("/").text


def test_employer_hide_and_unhide(web) -> None:
    client, _ = web
    client.post("/employers/hide", data={"name": "Плохая Компания"}, follow_redirects=True)
    assert "Плохая Компания" in client.get("/employers").text
    assert "Девопс" not in client.get("/vacancies").text
    client.post("/employers/unhide", data={"name": "Плохая Компания"}, follow_redirects=True)
    assert "Девопс" in client.get("/vacancies").text


def test_note_saved_and_shown_in_detail(web) -> None:
    client, engine = web
    rid = _id(engine, "Аналитик данных")
    client.post(
        f"/vacancies/{rid}/note",
        data={"note": "созвон в четверг 15:00"},
        follow_redirects=True,
    )
    resp = client.get(f"/vacancies/{rid}")
    assert resp.status_code == 200 and "созвон в четверг 15:00" in resp.text


def test_detail_404(web) -> None:
    client, _ = web
    assert client.get("/vacancies/9999").status_code == 404


def test_digest_shows_only_triage_kept_when_profile(web, tmp_path, make_vacancy) -> None:
    """Регрессия: ключ кэша триажа должен совпадать с читаемым в дайджесте (one-way)."""
    # профиль есть → дайджест в режиме «только проверенные»
    from job_hunter.domain.profile import CandidateProfile
    from job_hunter.infrastructure.db.repositories import SqlLlmCacheRepository
    from job_hunter.infrastructure.profile import save_profile

    save_profile(tmp_path, CandidateProfile(desired_roles=("аналитик",)))

    client, engine = web
    # пишем триаж-кэши: аналитик keep, девопс drop
    with session_scope(engine) as session:
        repo = SqlVacancyRepository(session)
        cache = SqlLlmCacheRepository(session)
        for r in repo.list_recent():
            verdict = "keep" if "Аналитик" in r.vacancy.title else "drop"
            from job_hunter.application.llm_pipeline import _key

            cache.set(_key("triage", r.content_hash), role="triage", model="fake", output=verdict)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "Аналитик данных" in resp.text  # keep виден
    assert "Девопс" not in resp.text  # drop скрыт


def test_xss_escaped(web, make_vacancy) -> None:
    client, engine = web  # engine — именно тот, что использует приложение (файловая БД теста)
    with session_scope(engine) as session:
        SqlVacancyRepository(session).upsert(
            make_vacancy("xss", title="<script>alert(1)</script>"), seen_at=NOW
        )
    text = client.get("/vacancies").text
    assert "<script>alert(1)</script>" not in text  # автоэскейпинг Jinja
    assert "&lt;script&gt;" in text
