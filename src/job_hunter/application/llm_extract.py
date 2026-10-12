"""LLM-экстрактор: вакансия (текст) → структура. Строгая JSON-схема через Pydantic."""

import json
import logging
import re

from pydantic import BaseModel, ValidationError

from job_hunter.domain.models import Vacancy
from job_hunter.domain.ports import LLMClient

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"  # входит в ключ кэша — bump при изменении промпта

_SYSTEM = """Ты — аналитик IT-вакансий. Отвечай СТРОГО одним JSON-объектом без пояснений.
Поля:
- "skills": список технологий/навыков из текста (массив строк, пустой если не найдено)
- "is_umbrella": true/false — вакансия-«зонтик» под пул разных ролей/массовый набор
- "red_flags": тревожные сигналы (переработки, дежурства, штрафы, нереалистичный стек); [] если нет
- "conditions": 1-2 предложения об условиях (формат работы, соцпакет, прозрачность вилки)
- "employer_notes": 1-2 предложения о работодателе ИЗ ТЕКСТА вакансии (не выдумывай факты)
"""

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class VacancyExtraction(BaseModel):
    skills: list[str] = []
    is_umbrella: bool = False
    red_flags: list[str] = []
    conditions: str = ""
    employer_notes: str = ""


def _clean_json(raw: str) -> str:
    raw = _THINK_RE.sub("", raw)  # у «думающих» моделей срезаем рассуждение
    match = _FENCE_RE.search(raw)
    if match:
        return match.group(1).strip()
    return raw.strip()


def parse_extraction(raw: str) -> VacancyExtraction | None:
    try:
        return VacancyExtraction.model_validate(json.loads(_clean_json(raw)))
    except (json.JSONDecodeError, ValidationError):
        logger.warning("LLM extract: невалидный JSON: %.200r", raw)
        return None


def extract_vacancy(llm: LLMClient, vacancy: Vacancy) -> VacancyExtraction | None:
    user = (
        f"ВАКАНСИЯ:\nНазвание: {vacancy.title}\nРаботодатель: {vacancy.employer.name}\n"
        f"Зарплата: {_salary(vacancy)}\nОписание:\n{vacancy.description[:3500]}"
    )
    raw = llm.complete(role="extract", system=_SYSTEM, user=user)
    return parse_extraction(raw)


def _salary(vacancy: Vacancy) -> str:
    s = vacancy.salary
    if not s:
        return "не указана"
    parts = []
    if s.min_amount:
        parts.append(f"от {s.min_amount}")
    if s.max_amount:
        parts.append(f"до {s.max_amount}")
    return " ".join(parts) + (f" {s.currency}" if s.currency else "")
