"""Профиль кандидата и конфигурация скоринга — доменные dataclass'ы.

Файловое хранение и валидация — в infrastructure (YAML ↔ эти типы).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CandidateProfile:
    """Что ищет кандидат. Пустые коллекции выключают соответствующие сигналы."""

    desired_roles: tuple[str, ...] = ()  # «backend-разработчик», «data engineer»
    skills: tuple[str, ...] = ()  # канонические названия навыков
    skill_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)  # канон → синонимы
    seniorities: tuple[str, ...] = ()  # Senior / Middle / Lead …
    locations: tuple[str, ...] = ()  # города; пусто = неважно
    remote_modes: tuple[str, ...] = ()  # remote / hybrid / onsite
    salary_min: int | None = None  # минимально приемлемая вилка «от»
    deal_breakers: tuple[str, ...] = ()  # фразы-отбраковщики
    search_queries: tuple[str, ...] = ()  # пресеты запросов для fetch

    def canonical_skills(self) -> dict[str, str]:
        """Любая форма навыка (нижний регистр) → каноническое имя."""
        mapping: dict[str, str] = {}
        for skill in self.skills:
            mapping[skill.lower()] = skill
            for alias in self.skill_aliases.get(skill, ()):
                mapping[alias.lower()] = skill
        return mapping


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """Веса/штрафы/пороги rules-скоринга (config/scoring.yaml)."""

    weights: dict[str, float] = field(
        default_factory=lambda: {
            "skills": 40.0,
            "salary": 20.0,
            "freshness": 10.0,
            "remote": 10.0,
            "seniority": 10.0,
            "location": 10.0,
            "llm": 15.0,  # LLM-вердикт: [-15..+15] поверх rules-пульта
        }
    )
    penalties: dict[str, float] = field(
        default_factory=lambda: {
            "repost": -12.0,  # та же позиция публикуется пулом/перепостится
            "evergreen": -15.0,  # «вечная» вакансия (висит дольше evergreen_days)
            "no_salary": -5.0,  # вилка не указана
            "red_flag": -8.0,  # за каждую фразу из red_flags
            "umbrella": -8.0,  # вакансия-«зонтик» под пул ролей (находит LLM)
        }
    )
    evergreen_days: int = 30
    dedup_similarity: float = 96.0
    repost_similarity: float = 90.0
    red_flags: tuple[str, ...] = (
        "молодая динамичная команда",
        "молодой коллектив",
        "стрессоустойчивость",
        "многозадачность",
        "работа в выходные",
        "овертаймы",
        "переработки",
    )
