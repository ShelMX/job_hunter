"""LLM-триаж вакансий decision-моделью (tev1): подходит профилю или нет.

Fail-open: при сбое/нечитаемом ответе вакансия СОХРАНЯЕТСЯ (шум лучше потерянной позиции).
"""

import logging
from dataclasses import dataclass

from job_hunter.application.analyze import Assessment
from job_hunter.application.llm_extract import _salary
from job_hunter.application.llm_pipeline import _key as _cache_key
from job_hunter.domain.models import VacancyRecord
from job_hunter.domain.ports import LLMCache, LLMClient, LLMUnavailableError
from job_hunter.domain.profile import CandidateProfile

logger = logging.getLogger(__name__)

_OPTIONS = ("подходит профилю по роли и содержанию", "не подходит")

_TASK = """Профиль кандидата:
- роли: {roles}
- навыки: {skills}
- формат работы: {remote}; локации: {locations}
- минимальная зарплата: {salary}

Вакансия:
- название: {title}
- работодатель: {employer}
- зарплата: {vac_salary}; формат: {remote_mode}; грейд: {seniority}
- текст: {description}

Реши, стоит ли показывать эту вакансию кандидату. Грубая прикидка по роли и содержанию.
Смежные роли из профессиональной области кандидата считай ПОДХОДЯЩИМИ (например, для
руководителя аналитики подходят и роли ведущего/старшего аналитика). Отсекай только
вакансии из явно других профессий (продажи, разработка, бухгалтерия и т.п.) или заведомо
несовместимые по уровню/формату.
"""


@dataclass(frozen=True, slots=True)
class TriageResult:
    keep: bool
    reason: str


def _task(profile: CandidateProfile, record: VacancyRecord) -> str:
    vacancy = record.vacancy
    return _TASK.format(
        roles=", ".join(profile.desired_roles) or "—",
        skills=", ".join(profile.skills) or "—",
        remote=", ".join(profile.remote_modes) or "—",
        locations=", ".join(profile.locations) or "—",
        salary=profile.salary_min or "—",
        title=vacancy.title,
        employer=vacancy.employer.name,
        vac_salary=_salary(vacancy),
        remote_mode=vacancy.remote.value,
        seniority=vacancy.seniority or "—",
        description=(vacancy.description or "")[:900] or "(нет описания)",
    )


def triage_batch(
    pairs: list[tuple[VacancyRecord, Assessment]],
    *,
    llm: LLMClient,
    cache: LLMCache,
    profile: CandidateProfile | None,
) -> tuple[list[tuple[VacancyRecord, Assessment]], int]:
    """Отсеивает заведомо непрофильные вакансии. Возвращает (выжившие, сколько отсеяно)."""
    if profile is None:
        return pairs, 0

    survivors: list[tuple[VacancyRecord, Assessment]] = []
    dropped = 0
    for pair in pairs:
        record = pair[0]
        key = _cache_key("triage", record.content_hash)
        cached = cache.get(key)
        if cached is not None:
            keep = cached == "keep"
        else:
            try:
                index = llm.decide(role="triage", task=_task(profile, record), options=_OPTIONS)
            except LLMUnavailableError:
                # модель недоступна (RAM/наличие) — дальше триаж бессмыслен: всё остальное fail-open
                logger.warning(
                    "Триаж: LLM недоступен — остаток (%d шт.) идёт без триажа",
                    len(pairs) - len(survivors) - dropped,
                )
                survivors.append(pair)
                idx = pairs.index(pair)
                survivors.extend(pairs[idx + 1 :])
                return survivors, dropped
            except Exception:
                logger.warning("Триаж #%d: сбой модели — оставляем вакансию (fail-open)", record.id)
                index = None
            keep = (index == 0) if index is not None else True  # fail-open
            if index is not None:
                cache.set(key, role="triage", model=llm.provider_name(), output="keep" if keep else "drop")
        if keep:
            survivors.append(pair)
        else:
            dropped += 1
            logger.info(
                "Триаж: отсеяна #%d «%s» (%s)", record.id, record.vacancy.title, record.vacancy.employer.name
            )
    return survivors, dropped
