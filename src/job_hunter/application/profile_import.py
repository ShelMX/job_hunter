"""Импорт резюме (md/txt/pdf/docx) → CandidateProfile через LLM-экстрактор."""

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ValidationError

from job_hunter.application.llm_extract import _clean_json
from job_hunter.domain.ports import LLMClient
from job_hunter.domain.profile import CandidateProfile

logger = logging.getLogger(__name__)

_SYSTEM = """Ты — ассистент, извлекающий профиль кандидата из резюме. Отвечай СТРОГО одним JSON-объектом:
{
  "desired_roles": ["..."],      // желаемые должности из резюме
  "skills": ["..."],             // ключевые навыки/технологии (короткие канонические имена)
  "seniorities": ["..."],        // грейды: Junior/Middle/Senior/Lead (по опыту)
  "locations": ["..."],          // города проживания/поиска
  "remote_modes": ["..."],       // из: remote, hybrid, onsite (по предпочтениям в резюме)
  "salary_min": 0,               // минимально приемлемая зарплата (из резюме; null если не указано)
  "search_queries": ["..."]      // 3-5 коротких поисковых запросов для джоб-бордов
}
Ничего не выдумывай: чего нет в резюме — не добавляй (массив пустой / null).
"""


class ProfileExtraction(BaseModel):
    desired_roles: list[str] = []
    skills: list[str] = []
    seniorities: list[str] = []
    locations: list[str] = []
    remote_modes: list[str] = []
    salary_min: int | None = None
    search_queries: list[str] = []


def read_resume_text(path: Path) -> str:
    """Чтение текста резюме по расширению файла."""
    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    if suffix == ".docx":
        import docx

        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    raise ValueError(f"Не умею читать {suffix!r}; поддерживаются: .md, .txt, .pdf, .docx")


def parse_profile(raw: str) -> ProfileExtraction | None:
    try:
        return ProfileExtraction.model_validate(json.loads(_clean_json(raw)))
    except (json.JSONDecodeError, ValidationError):
        logger.warning("LLM profile import: невалидный JSON: %.200r", raw)
        return None


def import_profile(llm: LLMClient, resume_text: str) -> ProfileExtraction | None:
    raw = llm.complete(role="extract", system=_SYSTEM, user=resume_text[:12000])
    return parse_profile(raw)


def merge_profiles(existing: CandidateProfile | None, extracted: ProfileExtraction) -> CandidateProfile:
    """Извлечённые перекрывают непустые поля; ручные алиасы/deal-breakers сохраняются."""

    def pick(new: list[str], old: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(new) if new else old

    return CandidateProfile(
        desired_roles=pick(extracted.desired_roles, existing.desired_roles if existing else ()),
        skills=pick(extracted.skills, existing.skills if existing else ()),
        skill_aliases=existing.skill_aliases if existing else {},
        seniorities=pick(extracted.seniorities, existing.seniorities if existing else ()),
        locations=pick(extracted.locations, existing.locations if existing else ()),
        remote_modes=pick(extracted.remote_modes, existing.remote_modes if existing else ()),
        salary_min=(
            extracted.salary_min
            if extracted.salary_min is not None
            else (existing.salary_min if existing else None)
        ),
        deal_breakers=existing.deal_breakers if existing else (),
        search_queries=pick(extracted.search_queries, existing.search_queries if existing else ()),
    )
