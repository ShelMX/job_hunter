"""Конфигурация: pydantic-settings (env + YAML) и загрузка connectors.yaml."""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr, TypeAdapter
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from job_hunter.domain.profile import ScoringConfig

DEFAULT_CONFIG_DIR = Path(os.environ.get("JOB_HUNTER_CONFIG_DIR", "config"))


class LLMResourcePolicy(BaseModel):
    """Ресурсная дисциплина локальных LLM (PLAN §7.1 — требование владельца)."""

    concurrency: int = 1  # строго последовательные вызовы
    max_loaded_models: int = 1  # одна модель в памяти единовременно
    batch_size: int = 20
    keep_alive: str = "60s"  # резиденция между задачами батча
    unload_after_batch: bool = True  # после батча — явная выгрузка (keep_alive=0)
    min_free_ram_gb: float = 8.0  # иначе — деградация к меньшей модели из тира
    lmstudio_unload_via_cli: bool = True  # `lms unload` после батча (fallback-провайдер)


class LLMSettings(BaseModel):
    """Настройки локальных LLM: primary Ollama (нативное API), fallback LM Studio."""

    enabled: bool = False
    primary_base_url: str = "http://localhost:11434"  # Ollama native
    fallback_base_url: str = "http://localhost:1234/v1"  # LM Studio OpenAI-compat
    # тиры ролей: от предпочтительной к запасной (деградация по свободной RAM)
    models: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "extract": ["qwen3:4b", "gemma2:2b"],
            "score": ["gemma3:12b", "qwen3:4b", "gemma2:2b"],
        }
    )
    resource_policy: LLMResourcePolicy = Field(default_factory=LLMResourcePolicy)


class SchedulerSettings(BaseModel):
    """Фоновой планировщик внутри web-процесса (APScheduler)."""

    enabled: bool = True
    fetch_times: list[str] = Field(default_factory=lambda: ["09:00", "18:00"])  # локальное время
    triage_after_fetch: bool = True
    triage_pool_limit: int = 40
    catch_up_hours: float = 3.0  # FR-008: порог догоняющего сбора


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOB_HUNTER_",
        env_file=".env",  # секреты храним здесь (см. .env.example), файл в .gitignore
        extra="ignore",
    )

    database_url: str = "sqlite:///data/job_hunter.db"
    http_timeout_seconds: float = 20.0
    http_max_retries: int = 3
    user_agent: str = "job-hunter/0.1 (local job-search assistant)"
    config_dir: Path = DEFAULT_CONFIG_DIR
    llm: LLMSettings = Field(default_factory=LLMSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)

    # hh.ru OAuth (client credentials) — из env или .env; SecretStr: значение
    # невозможно случайно напечатать в лог/repr/str, извлекается только по месту использования
    hh_client_id: SecretStr | None = None
    hh_client_secret: SecretStr | None = None
    # SuperJob: Secret key приложения с https://api.superjob.ru (идёт заголовком X-Api-App-Id)
    sj_secret_key: SecretStr | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_source = YamlConfigSettingsSource(settings_cls, yaml_file=DEFAULT_CONFIG_DIR / "settings.yaml")
        return (init_settings, env_settings, yaml_source, dotenv_settings, file_secret_settings)


class ConnectorConfig(BaseModel):
    enabled: bool = True
    # type — универсальные коннекторы: имя произвольное, реализацию выбирает type ("rss" и т.п.)
    type: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


_connector_configs_adapter = TypeAdapter(dict[str, ConnectorConfig])


def load_connector_configs(config_dir: Path) -> dict[str, ConnectorConfig]:
    path = config_dir / "connectors.yaml"
    if not path.exists():
        return {}
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _connector_configs_adapter.validate_python(raw)


def load_scoring_config(config_dir: Path) -> ScoringConfig:
    """config/scoring.yaml → доменный ScoringConfig; отсутствует — дефолты."""
    path = config_dir / "scoring.yaml"
    if not path.exists():
        return ScoringConfig()
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ожидается mapping")
    defaults = ScoringConfig()
    return ScoringConfig(
        weights={**defaults.weights, **(raw.get("weights") or {})},
        penalties={**defaults.penalties, **(raw.get("penalties") or {})},
        evergreen_days=int(raw.get("evergreen_days", defaults.evergreen_days)),
        dedup_similarity=float(raw.get("dedup_similarity", defaults.dedup_similarity)),
        repost_similarity=float(raw.get("repost_similarity", defaults.repost_similarity)),
        cluster_similarity=float(raw.get("cluster_similarity", defaults.cluster_similarity)),
        red_flags=tuple(raw.get("red_flags") or list(defaults.red_flags)),
    )
