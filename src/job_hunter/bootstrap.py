"""Composition root: сборка контекста приложения из конфигов и адаптеров."""

from dataclasses import dataclass, field
from functools import lru_cache

from sqlalchemy import Engine

from job_hunter.connectors import RegisteredConnector, build_connectors
from job_hunter.infrastructure.config import Settings, load_connector_configs
from job_hunter.infrastructure.db.engine import make_engine
from job_hunter.infrastructure.http import HttpClient


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@dataclass(slots=True)
class AppContext:
    settings: Settings
    engine: Engine
    http: HttpClient
    connectors: list[RegisteredConnector] = field(default_factory=list)

    def close(self) -> None:
        self.http.close()
        self.engine.dispose()


def build_context(settings: Settings | None = None) -> AppContext:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    http = HttpClient(
        user_agent=settings.user_agent,
        timeout=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
    )
    configs = load_connector_configs(settings.config_dir)
    connectors = build_connectors(settings=settings, configs=configs, http=http)
    return AppContext(settings=settings, engine=engine, http=http, connectors=connectors)
