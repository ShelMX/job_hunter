from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from job_hunter.application.catchup import overdue_sources, run_catchup
from job_hunter.application.ingest import IngestVacancies
from job_hunter.bootstrap import AppContext
from job_hunter.connectors import RegisteredConnector
from job_hunter.domain.models import Vacancy
from job_hunter.infrastructure.config import Settings
from job_hunter.infrastructure.db.engine import session_scope
from job_hunter.infrastructure.db.repositories import SqlFetchRunRepository, SqlVacancyRepository

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@dataclass
class FakeConnector:
    name: str
    vacancies: list[Vacancy]
    available: bool = True

    def is_available(self) -> bool:
        return self.available

    def unavailable_reason(self) -> str:
        return "нет credentials"

    def search(self, query=None, *, limit=100):
        assert self.available, "search() не должен вызываться для недоступного источника"
        yield from self.vacancies[:limit]


def test_fr_008_overdue_boundaries() -> None:
    threshold = 3.0
    sources = ["hh", "habr"]

    # ровно порог — не просрочено
    exact = {"hh": NOW - timedelta(hours=threshold)}
    assert overdue_sources(exact, sources, now=NOW, threshold_hours=threshold) == ["habr"]

    # больше порога — просрочено
    older = {"hh": NOW - timedelta(hours=threshold + 0.5)}
    assert overdue_sources(older, sources, now=NOW, threshold_hours=threshold) == ["hh", "habr"]

    # меньше порога — не просрочено
    fresh = {"hh": NOW - timedelta(hours=threshold - 0.5), "habr": NOW - timedelta(minutes=10)}
    assert overdue_sources(fresh, sources, now=NOW, threshold_hours=threshold) == []

    # отсутствие записи = просрочено
    assert overdue_sources({}, sources, now=NOW, threshold_hours=threshold) == ["hh", "habr"]


def test_fr_008_fetch_run_repository(engine) -> None:
    with session_scope(engine) as session:
        repo = SqlFetchRunRepository(session)
        assert repo.get_all() == {}

        repo.record_success("hh", NOW)
        repo.record_success("habr", NOW - timedelta(hours=2))
        repo.record_failure("superjob", NOW, "нет credentials")

        all_runs = repo.get_all()
        assert set(all_runs) == {"hh", "habr"}
        assert all_runs["hh"] == NOW

    # новая сессия — данные переживают коммит
    with session_scope(engine) as session:
        repo = SqlFetchRunRepository(session)
        all_runs = repo.get_all()
        assert set(all_runs) == {"hh", "habr"}
        # failure НЕ стирает факт последнего успеха (иначе catch-up зациклится); FR-008
        repo.record_failure("hh", NOW + timedelta(hours=1), "timeout")
        assert repo.get_all()["hh"] == NOW
        # источник, у которого успехов не было вовсе, в last_success не появляется
        assert "superjob" not in repo.get_all()


def test_fr_008_catchup_no_duplicates(engine, make_vacancy) -> None:
    v1 = make_vacancy("1")
    v2 = make_vacancy("2", title="Другая")
    connector = FakeConnector("fake", [v1, v2])

    with session_scope(engine) as session:
        repo = SqlVacancyRepository(session)
        run_repo = SqlFetchRunRepository(session)
        first = IngestVacancies([connector], repo).execute(now=NOW, limit=100, fetch_run_repo=run_repo)[0]
        assert (first.fetched, first.created, first.updated) == (2, 2, 0)
        assert run_repo.get_all()["fake"] == NOW

    later = NOW + timedelta(days=1)
    with session_scope(engine) as session:
        repo = SqlVacancyRepository(session)
        run_repo = SqlFetchRunRepository(session)
        second = IngestVacancies([connector], repo).execute(now=later, limit=100, fetch_run_repo=run_repo)[0]
        assert (second.fetched, second.created, second.updated) == (2, 0, 2)
        assert repo.count() == 2

        record = repo.list_recent(limit=1)[0]
        assert record.times_seen == 2
        assert record.first_seen_at == NOW
        assert record.last_seen_at == later
        assert run_repo.get_all()["fake"] == later


def test_fr_008_run_catchup_uses_threshold(engine, make_vacancy, tmp_path) -> None:
    """run_catchup дёргает только просроченные источники и останавливается,
    если все свежие."""
    v1 = make_vacancy("1")
    connector = FakeConnector("fake", [v1])

    settings = Settings(
        database_url="sqlite:///:memory:",
        config_dir=tmp_path,
        scheduler={"enabled": True, "fetch_times": [], "catch_up_hours": 3.0},
    )
    ctx = AppContext(
        settings=settings,
        engine=engine,
        http=None,  # не нужен для catch-up
        connectors=[RegisteredConnector(connector=connector, name="fake", enabled=True)],
    )

    # нет записи — источник просрочен, catch-up запустит сбор
    stats = run_catchup(ctx, now=NOW)
    assert len(stats) == 1
    assert stats[0].source == "fake"
    assert stats[0].created == 1

    # сразу повторный запуск: источник уже собирался 0 ч назад — ничего не делаем
    stats2 = run_catchup(ctx, now=NOW + timedelta(minutes=5))
    assert stats2 == []
