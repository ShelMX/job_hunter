"""Оркестрация LLM-обогащения: экстракция + вердикт с кэшем. Батчи и одиночные вызовы."""

import dataclasses
import hashlib
import json
import logging
from collections.abc import Sequence

from job_hunter.application.analyze import Assessment, Signal
from job_hunter.application.llm_extract import (
    PROMPT_VERSION,
    VacancyExtraction,
    extract_vacancy,
    parse_extraction,
)
from job_hunter.application.llm_score import LLMVerdict, parse_verdict, score_vacancy
from job_hunter.domain.models import Vacancy, VacancyRecord
from job_hunter.domain.ports import LLMCache, LLMClient, LLMUnavailableError
from job_hunter.domain.profile import CandidateProfile, ScoringConfig

logger = logging.getLogger(__name__)


def _key(role: str, content_hash: str, extra: str = "") -> str:
    return hashlib.sha256(f"{role}|{PROMPT_VERSION}|{content_hash}|{extra}".encode()).hexdigest()


def _profile_fingerprint(profile: CandidateProfile) -> str:
    payload = json.dumps(
        [profile.desired_roles, profile.skills, profile.salary_min, profile.deal_breakers],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _extract_cached_or_compute(
    vacancy: Vacancy, content_hash: str, *, llm: LLMClient, cache: LLMCache
) -> VacancyExtraction | None:
    """Экстракция: сначала кэш, при промахе — вызов модели и запись в кэш."""
    key = _key("extract", content_hash)
    extraction = None
    if cached := cache.get(key):
        extraction = parse_extraction(cached)
    if extraction is None:
        extraction = extract_vacancy(llm, vacancy)
        if extraction is not None:
            cache.set(key, role="extract", model=llm.provider_name(), output=extraction.model_dump_json())
    return extraction


def _score_cached_or_compute(
    vacancy: Vacancy,
    content_hash: str,
    rules_total: float,
    profile: CandidateProfile,
    *,
    llm: LLMClient,
    cache: LLMCache,
) -> LLMVerdict | None:
    key = _key("score", content_hash, _profile_fingerprint(profile))
    verdict = None
    if cached := cache.get(key):
        verdict = parse_verdict(cached)
    if verdict is None:
        verdict = score_vacancy(llm, profile, vacancy, rules_total=rules_total)
        if verdict is not None:
            cache.set(key, role="score", model=llm.provider_name(), output=verdict.model_dump_json())
    return verdict


def llm_signals(
    record: VacancyRecord,
    assessment: Assessment,
    *,
    llm: LLMClient,
    cache: LLMCache,
    profile: CandidateProfile | None,
    config: ScoringConfig,
) -> list[Signal]:
    """Полный набор LLM-сигналов для одной вакансии (кэш → вычисление)."""
    signals: list[Signal] = list(assessment.signals)
    extraction = _extract_cached_or_compute(record.vacancy, record.content_hash, llm=llm, cache=cache)
    if extraction is not None:
        if extraction.conditions:
            signals.append(Signal("info", 0.0, f"условия: {extraction.conditions}"))
        if extraction.employer_notes:
            signals.append(Signal("info", 0.0, f"работодатель: {extraction.employer_notes}"))
        if extraction.red_flags:
            signals.append(Signal("info", 0.0, f"LLM заметил: {'; '.join(extraction.red_flags)}"))
        if extraction.is_umbrella:
            signals.append(
                Signal(
                    "umbrella",
                    config.penalties.get("umbrella", -8.0),
                    "вакансия-«зонтик» под пул ролей (по оценке LLM)",
                )
            )

    if profile is not None:
        verdict = _score_cached_or_compute(
            record.vacancy, record.content_hash, assessment.total, profile, llm=llm, cache=cache
        )
        if verdict is not None:
            weight = config.weights.get("llm", 15.0)
            points = round((verdict.fit / 5 * 2 - 1) * weight, 1)  # [-w .. +w]
            signals.append(Signal("llm_fit", points, f"LLM {verdict.fit}/5 — {verdict.verdict}"))
            if verdict.risks:
                signals.append(Signal("info", 0.0, f"риск: {verdict.risks}"))
    return signals


def enrich_one(
    record: VacancyRecord,
    assessment: Assessment,
    *,
    llm: LLMClient,
    cache: LLMCache,
    profile: CandidateProfile | None,
    config: ScoringConfig,
) -> Assessment:
    """On-demand LLM-анализ одной вакансии (карточка «проанализировать»)."""
    signals = llm_signals(record, assessment, llm=llm, cache=cache, profile=profile, config=config)
    total = max(0.0, min(100.0, sum(s.points for s in signals)))
    return dataclasses.replace(assessment, total=round(total, 1), signals=tuple(signals))


def enrich_with_llm(
    assessed: Sequence[tuple[VacancyRecord, Assessment]],
    *,
    llm: LLMClient,
    cache: LLMCache,
    profile: CandidateProfile | None,
    config: ScoringConfig,
) -> list[tuple[VacancyRecord, Assessment]]:
    """Батч-обогащение. При падении LLM — частичный результат (graceful)."""
    result: list[tuple[VacancyRecord, Assessment]] = []
    for record, assessment in assessed:
        try:
            result.append(
                (record, enrich_one(record, assessment, llm=llm, cache=cache, profile=profile, config=config))
            )
        except LLMUnavailableError:
            logger.warning("LLM стал недоступен — дальше rules; частичные сигналы сохранены")
            result.append((record, assessment))
            result.extend(assessed[len(result) :])
            return result
        except Exception:
            logger.exception("LLM-обогащение #%d упало — вакансия остаётся с rules-оценкой", record.id)
            result.append((record, assessment))
    return result
