from datetime import UTC, datetime

from job_hunter.domain.models import VacancyStatus
from job_hunter.infrastructure.db.engine import session_scope
from job_hunter.infrastructure.db.repositories import SqlStateRepository, SqlVacancyRepository

NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def test_status_lifecycle(engine, make_vacancy) -> None:
    with session_scope(engine) as session:
        repo = SqlVacancyRepository(session)
        repo.upsert(make_vacancy("1"), seen_at=NOW)
        rid = repo.list_recent(limit=1)[0].id

        states = SqlStateRepository(session)
        states.set_status(rid, VacancyStatus.INTERESTED, now=NOW)
        states.set_status(rid, VacancyStatus.APPLIED, applied_at=NOW, now=NOW)
        states.set_note(rid, "созвон в четверг", now=NOW)

    with session_scope(engine) as session:
        state = SqlStateRepository(session).states()[rid]
        assert state.status is VacancyStatus.APPLIED
        assert state.applied_at == NOW
        assert state.note == "созвон в четверг"


def test_employer_hide_unhide(engine) -> None:
    with session_scope(engine) as session:
        repo = SqlStateRepository(session)
        repo.hide_employer("Плохая Компания", now=NOW)
        assert repo.hidden_employers() == {"Плохая Компания"}
        repo.unhide_employer("Плохая Компания")
        assert repo.hidden_employers() == set()


def test_unknown_record_has_new_default(engine) -> None:
    with session_scope(engine) as session:
        assert SqlStateRepository(session).states() == {}
