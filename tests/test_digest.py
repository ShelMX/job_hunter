from datetime import UTC, datetime

from job_hunter.application.digest import select_digest
from job_hunter.domain.models import VacancyState, VacancyStatus

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def _states(mapping: dict[int, VacancyStatus]) -> dict[int, VacancyState]:
    return {rid: VacancyState(record_id=rid, status=st) for rid, st in mapping.items()}


def test_digest_fresh_new_not_hidden(make_vacancy, make_record) -> None:
    fresh = make_record(1, make_vacancy("a", title="Свежая"), days_old=0)
    old = make_record(2, make_vacancy("b", title="Старенькая"), days_old=10)
    hidden = make_record(3, make_vacancy("c", title="Скрытая"), days_old=0)
    employed = make_record(4, make_vacancy("d", title="Откликнутся"), days_old=0)
    bad_employer = make_record(
        5, make_vacancy("e", title="Свежая но плохая", employer_name="ПлохаяКомпания"), days_old=0
    )

    out = select_digest(
        [fresh, old, hidden, employed, bad_employer],
        _states({3: VacancyStatus.HIDDEN, 4: VacancyStatus.APPLIED}),
        {"ПлохаяКомпания"},
        days=1,
        now=NOW,
    )
    assert [r.vacancy.title for r in out] == ["Свежая"]


def test_digest_days_window(make_vacancy, make_record) -> None:
    older = make_record(1, make_vacancy("a", title="Трёхдневка"), days_old=3)
    assert select_digest([older], {}, set(), days=1, now=NOW) == []
    assert len(select_digest([older], {}, set(), days=5, now=NOW)) == 1
