from datetime import UTC, datetime, timedelta

from job_hunter.application.analyze import VacancyAnalyzer
from job_hunter.domain.models import Employer, RemoteMode, Salary, Vacancy
from job_hunter.domain.profile import CandidateProfile, ScoringConfig

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

PROFILE = CandidateProfile(
    desired_roles=("backend",),
    skills=("python", "fastapi"),
    seniorities=("senior",),
    locations=("москва",),
    remote_modes=("remote",),
    salary_min=250_000,
    deal_breakers=("php",),
)


def _vacancy(external_id: str, **kwargs) -> Vacancy:
    base = {
        "source": "fake",
        "external_id": external_id,
        "url": f"https://example.com/{external_id}",
        "title": "Senior Python-разработчик",
        "employer": Employer(name="ООО Рога"),
        "salary": Salary(min_amount=300_000, max_amount=350_000, currency="RUR"),
        "skills": ("python", "fastapi", "django"),
        "locations": ("Москва",),
        "remote": RemoteMode.REMOTE,
        "seniority": "Senior",
        "published_at": NOW - timedelta(days=1),
    }
    base.update(kwargs)
    return Vacancy(**base)  # type: ignore[arg-type]


def _record(make_record, record_id: int, vacancy: Vacancy, **kwargs):
    return make_record(record_id, vacancy, **kwargs)


def test_ideal_vacancy_scores_near_max(make_record) -> None:
    record = _record(make_record, 1, _vacancy("1"))
    analyzer = VacancyAnalyzer(PROFILE, ScoringConfig())
    [(rec, assessment)] = analyzer.assess_all([record], now=NOW)

    assert not assessment.rejected
    assert assessment.total >= 95  # все сигналы положительные
    names = {s.name for s in assessment.signals}
    assert {"skills", "salary", "remote", "seniority", "location", "freshness"} <= names


def test_deal_breaker_rejects(make_record) -> None:
    vacancy = _vacancy("1", title="Senior PHP разработчик", skills=("php",))
    [(rec, assessment)] = VacancyAnalyzer(PROFILE, ScoringConfig()).assess_all(
        [_record(make_record, 1, vacancy)], now=NOW
    )
    assert assessment.rejected
    assert any("php" in reason for reason in assessment.reject_reasons)


def test_evergreen_and_no_salary_penalties(make_record) -> None:
    old = NOW - timedelta(days=45)
    vacancy = _vacancy("1", salary=None, published_at=old)
    record = _record(make_record, 1, vacancy, days_old=45)
    [(rec, assessment)] = VacancyAnalyzer(PROFILE, ScoringConfig()).assess_all([record], now=NOW)

    evergreen = next(s for s in assessment.signals if s.name == "evergreen")
    salary = next(s for s in assessment.signals if s.name == "salary")
    assert evergreen.points < 0 and "висит" in evergreen.reason
    assert salary.points < 0 and "не указана" in salary.reason


def test_repost_cluster_penalty(make_record) -> None:
    v1 = _vacancy("1")
    v2 = _vacancy("2", title="Senior Python разработчик", source="other")
    records = [_record(make_record, 1, v1), _record(make_record, 2, v2)]
    assessed = VacancyAnalyzer(PROFILE, ScoringConfig()).assess_all(records, now=NOW)

    for rec, assessment in assessed:
        repost = next((s for s in assessment.signals if s.name == "repost"), None)
        assert repost is not None, f"ожидался repost-штраф у #{rec.id}"
        assert repost.points < 0 and "пулом" in repost.reason


def test_red_flag_penalty(make_record) -> None:
    vacancy = _vacancy("1", description="У нас молодая динамичная команда; стрессоустойчивость обязательна")
    [(rec, assessment)] = VacancyAnalyzer(PROFILE, ScoringConfig()).assess_all(
        [_record(make_record, 1, vacancy)], now=NOW
    )
    flags = [s for s in assessment.signals if s.name == "red_flag"]
    assert len(flags) == 2  # «молодая динамичная команда» + «стрессоустойчивость»
    assert all(s.points < 0 for s in flags)


def test_partial_skills_coverage(make_record) -> None:
    vacancy = _vacancy("1", skills=("python", "sql"))  # fastapi нет
    [(rec, assessment)] = VacancyAnalyzer(PROFILE, ScoringConfig()).assess_all(
        [_record(make_record, 1, vacancy)], now=NOW
    )
    skills = next(s for s in assessment.signals if s.name == "skills")
    assert skills.points == 20.0  # половина покрытия от веса 40


def test_without_profile_only_generic_signals(make_record) -> None:
    record = _record(make_record, 1, _vacancy("1"))
    [(rec, assessment)] = VacancyAnalyzer(None, ScoringConfig()).assess_all([record], now=NOW)
    names = {s.name for s in assessment.signals}
    assert names == {"freshness"}
