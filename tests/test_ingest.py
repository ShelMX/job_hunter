from dataclasses import dataclass
from datetime import UTC, datetime

from job_hunter.application.ingest import IngestVacancies
from job_hunter.domain.models import Vacancy
from job_hunter.infrastructure.db.engine import session_scope
from job_hunter.infrastructure.db.repositories import SqlVacancyRepository

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


def test_ingest_creates_then_updates(engine, make_vacancy) -> None:
    connector = FakeConnector("fake", [make_vacancy("1"), make_vacancy("2", title="Другая")])

    with session_scope(engine) as session:
        stats = IngestVacancies([connector], SqlVacancyRepository(session)).execute(now=NOW)[0]
    assert (stats.fetched, stats.created, stats.updated) == (2, 2, 0)

    later = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    with session_scope(engine) as session:
        repo = SqlVacancyRepository(session)
        stats = IngestVacancies([connector], repo).execute(now=later)[0]
        assert (stats.fetched, stats.created, stats.updated) == (2, 0, 2)
        assert repo.count() == 2

        record = repo.list_recent(limit=1)[0]
        assert record.times_seen == 2
        assert record.first_seen_at == NOW
        assert record.last_seen_at == later


def test_ingest_skips_unavailable_sources(engine, make_vacancy) -> None:
    connector = FakeConnector("fake", [make_vacancy("1")], available=False)
    with session_scope(engine) as session:
        repo = SqlVacancyRepository(session)
        stats = IngestVacancies([connector], repo).execute(now=NOW)[0]
        assert stats.skipped_reason == "нет credentials"
        assert stats.fetched == 0
        assert repo.count() == 0
