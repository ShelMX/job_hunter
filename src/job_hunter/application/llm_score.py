"""LLM-скоринг: профиль × вакансия → fit 0..5 + объяснение. JSON-схема через Pydantic."""

import json
import logging

from pydantic import BaseModel, Field, ValidationError

from job_hunter.application.llm_extract import _clean_json, _salary
from job_hunter.domain.models import Vacancy
from job_hunter.domain.ports import LLMClient
from job_hunter.domain.profile import CandidateProfile

logger = logging.getLogger(__name__)

_SYSTEM = """Ты — карьерный ассистент, оценивающий соответствие вакансии кандидату.
Отвечай СТРОГО одним JSON-объектом:
- "fit": целое 0..5 (0 — совсем мимо, 3 — приемлемо, 5 — идеально)
- "verdict": 1-2 предложения — почему подходит/нет (по-русски, честно)
- "risks": 1 предложение — главный риск/что уточнить (пустая строка если очевидного нет)
Оценивай сдержанно: не выдумывай совпадений, штрафуй пробелы между профилем и требованиями.
"""


class LLMVerdict(BaseModel):
    fit: int = Field(ge=0, le=5)
    verdict: str = ""
    risks: str = ""


def parse_verdict(raw: str) -> LLMVerdict | None:
    try:
        return LLMVerdict.model_validate(json.loads(_clean_json(raw)))
    except (json.JSONDecodeError, ValidationError):
        logger.warning("LLM score: невалидный JSON: %.200r", raw)
        return None


def score_vacancy(
    llm: LLMClient, profile: CandidateProfile, vacancy: Vacancy, *, rules_total: float
) -> LLMVerdict | None:
    user = (
        "ПРОФИЛЬ КАНДИДАТА:\n"
        f"Роли: {', '.join(profile.desired_roles) or '—'}\n"
        f"Навыки: {', '.join(profile.skills) or '—'}\n"
        f"Грейды: {', '.join(profile.seniorities) or '—'}\n"
        f"Формат: {', '.join(profile.remote_modes) or '—'}; локации: {', '.join(profile.locations) or '—'}\n"
        f"Мин. зарплата: {profile.salary_min or '—'}\n"
        f"Deal-breakers: {', '.join(profile.deal_breakers) or '—'}\n\n"
        f"ВАКАНСИЯ (rules-балл: {rules_total}):\n"
        f"Название: {vacancy.title}\nРаботодатель: {vacancy.employer.name}\n"
        f"Зарплата: {_salary(vacancy)}\nГрейд: {vacancy.seniority or '—'}\n"
        f"Навыки из карточки: {', '.join(vacancy.skills) or '—'}\n"
        f"Описание:\n{vacancy.description[:3000]}"
    )
    raw = llm.complete(role="score", system=_SYSTEM, user=user)
    return parse_verdict(raw)
