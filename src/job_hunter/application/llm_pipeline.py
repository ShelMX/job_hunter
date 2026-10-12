"""Оркестрация LLM-обогащения короткого списка: экстракция + вердикт (с кэшем)."""

import dataclasses
import hashlib
import json
import logging
from collections.abc import Sequence

from job_hunter.application.analyze import Assessment, Signal
from job_hunter.application.llm_extract import PROMPT_VERSION, extract_vacancy
from job_hunter.application.llm_score import score_vacancy
from job_hunter.domain.models import VacancyRecord
from job_hunter.domain.ports import LLMCache, LLMClient
from job_hunter.domain.profile import CandidateProfile, ScoringConfig
from job_hunter.infrastructure.llm.ollama import LLMUnavailableError

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


def enrich_with_llm(
    assessed: Sequence[tuple[VacancyRecord, Assessment]],
    *,
    llm: LLMClient,
    cache: LLMCache,
    profile: CandidateProfile | None,
    config: ScoringConfig,
) -> list[tuple[VacancyRecord, Assessment]]:
    """Обогатить сигналы LLM-слоя. При падении LLM — вернуть частичный результат (graceful)."""
    result: list[tuple[VacancyRecord, Assessment]] = []
    fingerprint = _profile_fingerprint(profile) if profile else ""

    for record, assessment in assessed:
        signals: list[Signal] = list(assessment.signals)
        try:
            # --- экстракция (кэш по хэшу контента) ---
            key = _key("extract", record.content_hash)
            extraction = None
            if cached := cache.get(key):
                from job_hunter.application.llm_extract import parse_extraction

                extraction = parse_extraction(cached)
            if extraction is None:
                extraction = extract_vacancy(llm, record.vacancy)
                if extraction is not None:
                    cache.set(
                        key, role="extract", model=llm.provider_name(), output=extraction.model_dump_json()
                    )
            if extraction is not None:
                if extraction.conditions:
                    signals.append(Signal("info", 0.0, f"условия: {extraction.conditions}"))
                if extraction.employer_notes:
                    signals.append(Signal("info", 0.0, f"работодатель: {extraction.employer_notes}"))
                if extraction.red_flags:
                    flags = "; ".join(extraction.red_flags)
                    signals.append(Signal("info", 0.0, f"LLM заметил: {flags}"))
                if extraction.is_umbrella:
                    signals.append(
                        Signal(
                            "umbrella",
                            config.penalties.get("umbrella", -8.0),
                            "вакансия-«зонтик» под пул ролей (по оценке LLM)",
                        )
                    )

            # --- вердикт (только при профиле) ---
            if profile is not None:
                key = _key("score", record.content_hash, fingerprint)
                verdict = None
                if cached := cache.get(key):
                    from job_hunter.application.llm_score import parse_verdict

                    verdict = parse_verdict(cached)
                if verdict is None:
                    verdict = score_vacancy(llm, profile, record.vacancy, rules_total=assessment.total)
                    if verdict is not None:
                        cache.set(
                            key, role="score", model=llm.provider_name(), output=verdict.model_dump_json()
                        )
                if verdict is not None:
                    weight = config.weights.get("llm", 15.0)
                    points = round((verdict.fit / 5 * 2 - 1) * weight, 1)  # [-w .. +w]
                    signals.append(Signal("llm_fit", points, f"LLM {verdict.fit}/5 — {verdict.verdict}"))
                    if verdict.risks:
                        signals.append(Signal("info", 0.0, f"риск: {verdict.risks}"))
        except LLMUnavailableError:
            logger.warning("LLM стал недоступен — дальше rules; частичные сигналы сохранены")
            if tuple(signals) != assessment.signals:  # частичное обогащение (extract успел)
                total = max(0.0, min(100.0, sum(s.points for s in signals)))
                assessment = dataclasses.replace(assessment, total=round(total, 1), signals=tuple(signals))
            result.append((record, assessment))
            result.extend(assessed[len(result) :])
            return result
        except Exception:
            logger.exception("LLM-обогащение #%d упало — вакансия остаётся с rules-оценкой", record.id)

        total = max(0.0, min(100.0, sum(s.points for s in signals)))
        result.append(
            (record, dataclasses.replace(assessment, total=round(total, 1), signals=tuple(signals)))
        )
    return result
