"""Сборка ранжирования для веб-слоя: rules живьём + LLM-данные только из кэша (быстро)."""

import dataclasses
from collections.abc import Sequence

from job_hunter.application.analyze import Assessment, Signal, VacancyAnalyzer
from job_hunter.application.llm_extract import parse_extraction
from job_hunter.application.llm_pipeline import _key, _profile_fingerprint
from job_hunter.application.llm_score import parse_verdict
from job_hunter.domain.models import VacancyRecord
from job_hunter.domain.ports import LLMCache
from job_hunter.domain.profile import CandidateProfile, ScoringConfig


def assemble_from_cache(
    records: Sequence[VacancyRecord],
    *,
    profile: CandidateProfile | None,
    config: ScoringConfig,
    cache: LLMCache,
) -> list[tuple[VacancyRecord, Assessment]]:
    """Оценить все записи rules-модулем и подмешать закэшированные LLM-результаты.

    Отсеянные триажем исключаются из выдачи (при наличии профиля).
    """
    analyzer = VacancyAnalyzer(profile, config)
    fingerprint = _profile_fingerprint(profile) if profile else ""
    out: list[tuple[VacancyRecord, Assessment]] = []
    for record, assessment in analyzer.assess_all(records):
        # триаж: cache.get → "keep"/"drop"
        if profile is not None:
            verdict = cache.get(_key("triage", record.content_hash))
            if verdict == "drop":
                continue

        signals = list(assessment.signals)
        cached_extract = cache.get(_key("extract", record.content_hash))
        extraction = parse_extraction(cached_extract) if cached_extract else None
        if extraction:
            if extraction.conditions:
                signals.append(Signal("info", 0.0, f"условия: {extraction.conditions}"))
            if extraction.employer_notes:
                signals.append(Signal("info", 0.0, f"работодатель: {extraction.employer_notes}"))
            if extraction.is_umbrella:
                signals.append(Signal(
                    "umbrella", config.penalties.get("umbrella", -8.0),
                    "вакансия-«зонтик» (LLM)",
                ))
        if profile is not None and (
            verdict_raw := cache.get(_key("score", record.content_hash, fingerprint))
        ):
            if verdict := parse_verdict(verdict_raw):
                weight = config.weights.get("llm", 15.0)
                points = round((verdict.fit / 5 * 2 - 1) * weight, 1)
                signals.append(Signal("llm_fit", points, f"LLM {verdict.fit}/5 — {verdict.verdict}"))
                if verdict.risks:
                    signals.append(Signal("info", 0.0, f"риск: {verdict.risks}"))

        total = max(0.0, min(100.0, sum(s.points for s in signals)))
        out.append((record, dataclasses.replace(assessment, total=round(total, 1), signals=tuple(signals))))
    out.sort(key=lambda pair: pair[1].total, reverse=True)
    return out
