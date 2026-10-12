"""Rules-анализ и скоринг вакансий против профиля. Каждый балл объясним (Signal.reason)."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from job_hunter.application.dedup import find_duplicate_clusters, normalize_text
from job_hunter.domain.models import RemoteMode, VacancyRecord
from job_hunter.domain.profile import CandidateProfile, ScoringConfig

_FRESH_FULL_DAYS = 3
_FRESH_ZERO_DAYS = 14


@dataclass(frozen=True, slots=True)
class Signal:
    name: str
    points: float
    reason: str


@dataclass(frozen=True, slots=True)
class Assessment:
    record_id: int
    total: float  # 0..100
    rejected: bool
    reject_reasons: tuple[str, ...]
    signals: tuple[Signal, ...]


class VacancyAnalyzer:
    def __init__(self, profile: CandidateProfile | None, config: ScoringConfig) -> None:
        self._profile = profile
        self._cfg = config

    def assess_all(
        self, records: Sequence[VacancyRecord], *, now: datetime | None = None
    ) -> list[tuple[VacancyRecord, Assessment]]:
        now = now or datetime.now(UTC)
        clusters = find_duplicate_clusters(records, similarity_threshold=self._cfg.repost_similarity)
        cluster_size: dict[int, int] = {}
        for cluster in clusters:
            for rid in cluster.record_ids:
                cluster_size[rid] = len(cluster.record_ids)

        return [(record, self._assess(record, cluster_size, now)) for record in records]

    # --- внутреннее -------------------------------------------------------

    def _assess(self, record: VacancyRecord, cluster_size: dict[int, int], now: datetime) -> Assessment:
        vacancy = record.vacancy
        profile = self._profile
        text = normalize_text(" ".join([vacancy.title, vacancy.description, *vacancy.skills]))

        reject_reasons: list[str] = []
        if profile:
            for breaker in profile.deal_breakers:
                if normalize_text(breaker) in text:
                    reject_reasons.append(f"deal-breaker: «{breaker}»")

        signals: list[Signal] = []
        if profile:
            signals.extend(self._signal_skills(vacancy_skills=vacancy.skills, text=text))
            signals.extend(self._signal_salary(record))
            signals.extend(self._signal_remote(record))
            signals.extend(self._signal_seniority(record))
            signals.extend(self._signal_location(record))
        signals.extend(self._signal_freshness(record, now))
        signals.extend(self._signal_repost(record, cluster_size))
        signals.extend(self._signal_red_flags(text))

        total = max(0.0, min(100.0, sum(s.points for s in signals)))
        return Assessment(
            record_id=record.id,
            total=round(total, 1),
            rejected=bool(reject_reasons),
            reject_reasons=tuple(reject_reasons),
            signals=tuple(signals),
        )

    def _signal_skills(self, *, vacancy_skills: tuple[str, ...], text: str) -> list[Signal]:
        profile = self._profile
        if profile is None or not profile.skills:
            return []
        canonical = profile.canonical_skills()
        vacancy_skill_names = {s.lower() for s in vacancy_skills}
        matched: list[str] = []
        for form, canon in canonical.items():
            hit = form in vacancy_skill_names or re.search(rf"(?<!\w){re.escape(form)}(?!\w)", text)
            if hit and canon not in matched:
                matched.append(canon)
        coverage = len(matched) / len(profile.skills)
        points = round(self._cfg.weights["skills"] * coverage, 1)
        reason = f"совпадение навыков {len(matched)}/{len(profile.skills)}"
        if matched:
            reason += f": {', '.join(matched)}"
        return [Signal("skills", points, reason)]

    def _signal_salary(self, record: VacancyRecord) -> list[Signal]:
        profile = self._profile
        if profile is None or profile.salary_min is None:
            return []
        salary = record.vacancy.salary
        if salary is None or (salary.min_amount is None and salary.max_amount is None):
            return [Signal("salary", self._cfg.penalties["no_salary"], "вилка не указана")]
        offered = salary.max_amount or salary.min_amount or 0
        weight = self._cfg.weights["salary"]
        if offered >= profile.salary_min:
            return [
                Signal(
                    "salary",
                    weight,
                    f"вилка от {offered:,} покрывает ожидание {profile.salary_min:,}".replace(",", " "),
                )
            ]
        if offered >= profile.salary_min * 0.8:
            return [
                Signal(
                    "salary",
                    round(weight * 0.5, 1),
                    f"вилка слегка ниже ожидания ({offered:,})".replace(",", " "),
                )
            ]
        return [
            Signal(
                "salary", 0.0, f"вилка ниже ожидания ({offered:,} < {profile.salary_min:,})".replace(",", " ")
            )
        ]

    def _signal_freshness(self, record: VacancyRecord, now: datetime) -> list[Signal]:
        base = record.vacancy.published_at or record.first_seen_at
        age_days = max(0, (now - base).days)
        weight = self._cfg.weights["freshness"]
        if age_days <= _FRESH_FULL_DAYS:
            points = weight
        elif age_days >= _FRESH_ZERO_DAYS:
            points = 0.0
        else:
            points = round(
                weight * (1 - (age_days - _FRESH_FULL_DAYS) / (_FRESH_ZERO_DAYS - _FRESH_FULL_DAYS)), 1
            )
        signals = [Signal("freshness", points, f"опубликована {age_days} дн. назад")]
        if age_days > self._cfg.evergreen_days:
            signals.append(
                Signal(
                    "evergreen",
                    self._cfg.penalties["evergreen"],
                    f"«вечная» вакансия: висит {age_days} дн. (порог {self._cfg.evergreen_days})",
                )
            )
        return signals

    def _signal_repost(self, record: VacancyRecord, cluster_size: dict[int, int]) -> list[Signal]:
        size = cluster_size.get(record.id, 1)
        if size < 2:
            return []
        return [
            Signal(
                "repost",
                self._cfg.penalties["repost"],
                f"та же позиция опубликована пулом: {size} объявлений у работодателя",
            )
        ]

    def _signal_remote(self, record: VacancyRecord) -> list[Signal]:
        profile = self._profile
        if profile is None or not profile.remote_modes:
            return []
        remote = record.vacancy.remote
        weight = self._cfg.weights["remote"]
        if remote.value in profile.remote_modes or remote is RemoteMode.UNKNOWN:
            reason = "формат подходит" if remote.value in profile.remote_modes else "формат не указан"
            return [
                Signal(
                    "remote", weight if remote.value in profile.remote_modes else round(weight / 2, 1), reason
                )
            ]
        return [
            Signal("remote", 0.0, f"формат {remote.value} не из списка ({', '.join(profile.remote_modes)})")
        ]

    def _signal_seniority(self, record: VacancyRecord) -> list[Signal]:
        profile = self._profile
        if profile is None or not profile.seniorities:
            return []
        seniority = record.vacancy.seniority
        weight = self._cfg.weights["seniority"]
        if seniority is None:
            return [Signal("seniority", round(weight / 2, 1), "грейд не указан")]
        wanted = {s.lower() for s in profile.seniorities}
        if seniority.lower() in wanted:
            return [Signal("seniority", weight, f"грейд {seniority} — совпадение")]
        return [
            Signal("seniority", 0.0, f"грейд {seniority} не из списка ({', '.join(profile.seniorities)})")
        ]

    def _signal_location(self, record: VacancyRecord) -> list[Signal]:
        profile = self._profile
        if profile is None or not profile.locations:
            return []
        weight = self._cfg.weights["location"]
        vacancy_locs = " ".join(record.vacancy.locations).lower()
        for loc in profile.locations:
            if loc.lower() in vacancy_locs:
                return [Signal("location", weight, f"локация {loc} совпала")]
        if record.vacancy.remote is RemoteMode.REMOTE:
            return [Signal("location", weight, "удалёнка — локация не критична")]
        return [
            Signal(
                "location",
                0.0,
                f"локации «{vacancy_locs or '—'}» не из списка ({', '.join(profile.locations)})",
            )
        ]

    def _signal_red_flags(self, text: str) -> list[Signal]:
        return [
            Signal("red_flag", self._cfg.penalties["red_flag"], f"красный флаг: «{flag}»")
            for flag in self._cfg.red_flags
            if normalize_text(flag) in text
        ]
