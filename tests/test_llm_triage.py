from dataclasses import dataclass, field

from job_hunter.application.analyze import Assessment
from job_hunter.application.llm_triage import triage_batch
from job_hunter.domain.profile import CandidateProfile
from job_hunter.infrastructure.db.engine import session_scope
from job_hunter.infrastructure.db.repositories import SqlLlmCacheRepository
from job_hunter.infrastructure.llm.ollama import parse_letter

PROFILE = CandidateProfile(
    desired_roles=("Руководитель аналитики",), skills=("Data Analysis",), salary_min=250_000
)


def _assess(record_id: int) -> Assessment:
    return Assessment(record_id=record_id, total=50.0, rejected=False, reject_reasons=(), signals=())


@dataclass
class ScriptedLLM:
    answers: list[int | None]
    calls: list[str] = field(default_factory=list)

    def provider_name(self) -> str:
        return "fake"

    def is_available(self) -> bool:
        return True

    def complete(self, *, role: str, system: str, user: str) -> str:
        self.calls.append(role)
        return "{}"

    def decide(self, *, role: str, task: str, options) -> int | None:  # noqa: ANN001
        self.calls.append(role)
        return self.answers[len(self.calls) - 1]

    def unload_used(self) -> None:
        pass


def test_letter_parsing() -> None:
    assert parse_letter("A", 2) == 0
    assert parse_letter("Ответ: B", 2) == 1
    assert parse_letter("b", 2) == 1
    assert parse_letter("C", 2) is None  # вне диапазона
    assert parse_letter("ничего внятного", 2) is None


def test_triage_drops_mismatched_and_caches(engine, make_vacancy, make_record) -> None:
    pairs = [
        (make_record(1, make_vacancy("a", title="Руководитель отдела аналитики")), _assess(1)),
        (make_record(2, make_vacancy("b", title="Руководитель отдела продаж")), _assess(2)),
    ]
    llm = ScriptedLLM(answers=[0, 1])  # первую оставить, вторую отсеять

    with session_scope(engine) as session:
        survivors, dropped = triage_batch(
            pairs, llm=llm, cache=SqlLlmCacheRepository(session), profile=PROFILE
        )
    assert [r.id for r, _ in survivors] == [1]
    assert dropped == 1
    assert llm.calls == ["triage", "triage"]

    # повтор — из кэша, LLM не дёргается
    with session_scope(engine) as session:
        survivors2, dropped2 = triage_batch(
            pairs, llm=llm, cache=SqlLlmCacheRepository(session), profile=PROFILE
        )
    assert [r.id for r, _ in survivors2] == [1] and dropped2 == 1
    assert llm.calls == ["triage", "triage"]  # не выросло


def test_triage_fail_open_on_unreadable_answer(engine, make_vacancy, make_record) -> None:
    pairs = [(make_record(1, make_vacancy("a")), _assess(1))]
    llm = ScriptedLLM(answers=[None])  # ответ не распарсился → сохранить вакансию
    with session_scope(engine) as session:
        survivors, dropped = triage_batch(
            pairs, llm=llm, cache=SqlLlmCacheRepository(session), profile=PROFILE
        )
    assert len(survivors) == 1 and dropped == 0
