# ruff: noqa: E501
from dataclasses import dataclass, field

from job_hunter.application.analyze import VacancyAnalyzer
from job_hunter.application.llm_pipeline import enrich_with_llm
from job_hunter.domain.models import Employer, RemoteMode, Salary, Vacancy
from job_hunter.domain.profile import CandidateProfile, ScoringConfig
from job_hunter.infrastructure.db.engine import session_scope
from job_hunter.infrastructure.db.repositories import SqlLlmCacheRepository

EXTRACT_JSON = (
    '{"skills": ["python"], "is_umbrella": false, "red_flags": [],'
    ' "conditions": "remote, соцпакет", "employer_notes": "продуктовая команда"}'
)
SCORE_JSON = '{"fit": 5, "verdict": "полное совпадение по стеку", "risks": "уточнить онколл"}'

PROFILE = CandidateProfile(skills=("python", "fastapi"), remote_modes=("remote",), salary_min=200_000)


@dataclass
class FakeLLM:
    calls: list[str] = field(default_factory=list)

    def provider_name(self) -> str:
        return "fake"

    def is_available(self) -> bool:
        return True

    def complete(self, *, role: str, system: str, user: str) -> str:
        self.calls.append(role)
        return EXTRACT_JSON if role == "extract" else SCORE_JSON

    def unload_used(self) -> None:
        pass


def _vacancy() -> Vacancy:
    return Vacancy(
        source="fake",
        external_id="1",
        url="https://example.com/1",
        title="Senior Python-разработчик",
        employer=Employer(name="Рога"),
        description="FastAPI, remote",
        salary=Salary(min_amount=300_000, currency="RUR"),
        skills=("python", "fastapi"),
        remote=RemoteMode.REMOTE,
    )


def test_enrich_adds_llm_signals_and_uses_cache(engine, make_record) -> None:
    record = make_record(1, _vacancy())
    base = VacancyAnalyzer(PROFILE, ScoringConfig()).assess_all([record])[0]
    llm = FakeLLM()

    with session_scope(engine) as session:
        enr = enrich_with_llm(
            [base],
            llm=llm,
            cache=SqlLlmCacheRepository(session),
            profile=PROFILE,
            config=ScoringConfig(),
        )
    assert llm.calls == ["extract", "score"]

    rec, assessment = enr[0]
    names = [s.name for s in assessment.signals]
    assert "llm_fit" in names and "info" in names
    fit = next(s for s in assessment.signals if s.name == "llm_fit")
    assert fit.points == 15.0  # fit 5/5 × вес 15
    assert "полное совпадение" in fit.reason
    assert assessment.total > base[1].total  # LLM поднял хорошую вакансию

    # второй прогон — из кэша, LLM не вызывается
    with session_scope(engine) as session:
        enrich_with_llm(
            [base],
            llm=llm,
            cache=SqlLlmCacheRepository(session),
            profile=PROFILE,
            config=ScoringConfig(),
        )
    assert llm.calls == ["extract", "score"]  # не выросло


def test_enrich_umbrella_penalty(engine, make_record) -> None:
    record = make_record(1, _vacancy())
    base = VacancyAnalyzer(PROFILE, ScoringConfig()).assess_all([record])[0]

    @dataclass
    class UmbrellaLLM(FakeLLM):
        def complete(self, *, role: str, system: str, user: str) -> str:
            self.calls.append(role)
            if role == "extract":
                return '{"skills": [], "is_umbrella": true, "red_flags": ["массовый набор"], "conditions": "", "employer_notes": ""}'
            return '{"fit": 2, "verdict": "зонтик", "risks": ""}'

    with session_scope(engine) as session:
        enr = enrich_with_llm(
            [base],
            llm=UmbrellaLLM(),
            cache=SqlLlmCacheRepository(session),
            profile=PROFILE,
            config=ScoringConfig(),
        )
    _, assessment = enr[0]
    umbrella = next(s for s in assessment.signals if s.name == "umbrella")
    assert umbrella.points < 0 and "зонтик" in umbrella.reason
    fit = next(s for s in assessment.signals if s.name == "llm_fit")
    assert fit.points == -3.0  # (2/5*2-1) * 15 = -0.2 * 15
