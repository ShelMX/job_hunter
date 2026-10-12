"""Реестр источников вакансий.

Встроенные коннекторы живут в этом пакете. Внешние плагины подключаются через
entry point группы ``job_hunter.connectors``: в pyproject.toml плагина

    [project.entry-points."job_hunter.connectors"]
    my_source = "my_pkg.plugin:create_connector"

где ``create_connector(http: HttpClient, params: dict) -> SourceConnector``.
Добавление источника = установка пакета/модуля + запись в config/connectors.yaml,
без пересборки ядра.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, cast

from job_hunter.domain.ports import SourceConnector
from job_hunter.infrastructure.config import ConnectorConfig, Settings
from job_hunter.infrastructure.http import HttpClient

logger = logging.getLogger(__name__)

_EXTERNAL_GROUP = "job_hunter.connectors"


@dataclass(frozen=True, slots=True)
class RegisteredConnector:
    connector: SourceConnector
    name: str
    enabled: bool


def build_connectors(
    *, settings: Settings, configs: dict[str, ConnectorConfig], http: HttpClient
) -> list[RegisteredConnector]:
    """Собрать коннекторы по конфигу; неизвестное имя пробуем найти среди внешних плагинов."""
    from job_hunter.connectors import habr_career, hh, remoteok, rss, superjob, trudvsem

    builtins: dict[str, Callable[..., SourceConnector]] = {
        habr_career.HabrCareerConnector.name: habr_career.HabrCareerConnector,
        remoteok.RemoteOkConnector.name: remoteok.RemoteOkConnector,
        hh.HHConnector.name: hh.HHConnector,
        trudvsem.TrudvsemConnector.name: trudvsem.TrudvsemConnector,
        superjob.SuperJobConnector.name: superjob.SuperJobConnector,
    }
    # универсальные типы: ключ конфига — произвольное имя инстанса, реализация — по type
    typed_factories: dict[str, Callable[..., SourceConnector]] = {
        "rss": rss.RssConnector,
    }

    registered: list[RegisteredConnector] = []
    for name, config in configs.items():
        connector: SourceConnector | None = None
        if config.type and config.type in typed_factories:
            connector = typed_factories[config.type](http, name=name, **config.params)
        elif name in builtins:
            connector = _build_builtin(builtins[name], name, config, settings, http)
        else:
            connector = _load_external(name, http, config.params)
            if connector is None:
                logger.warning("Неизвестный источник %r в connectors.yaml — пропущен", name)
                continue
        registered.append(RegisteredConnector(connector=connector, name=name, enabled=config.enabled))
    return registered


def _build_builtin(
    factory: Callable[..., SourceConnector],
    name: str,
    config: ConnectorConfig,
    settings: Settings,
    http: HttpClient,
) -> SourceConnector:
    params = config.params
    if name == "hh":
        return factory(
            http,
            # расшифровка секретов — единственная точка в системе, здесь, на границе
            client_id=settings.hh_client_id.get_secret_value() if settings.hh_client_id else None,
            client_secret=(
                settings.hh_client_secret.get_secret_value() if settings.hh_client_secret else None
            ),
            base_url=str(params.get("base_url", "https://api.hh.ru")),
            per_page=int(params.get("per_page", 50)),
            user_agent=settings.user_agent,
        )
    if name == "habr_career":
        return factory(http, max_pages=int(params.get("max_pages", 5)))
    if name == "remoteok":
        return factory(
            http,
            base_url=str(params.get("base_url", "https://remoteok.com/api")),
            timeout_seconds=float(params.get("timeout_seconds", 90.0)),
        )
    if name == "trudvsem":
        return factory(http, base_url=str(params.get("base_url", "https://opendata.trudvsem.ru/api/v1")))
    if name == "superjob":
        return factory(
            http,
            secret_key=settings.sj_secret_key.get_secret_value() if settings.sj_secret_key else None,
            base_url=str(params.get("base_url", "https://api.superjob.ru/2.0")),
            max_pages=int(params.get("max_pages", 5)),
        )
    return factory(http)


def _load_external(name: str, http: HttpClient, params: dict[str, Any]) -> SourceConnector | None:
    for ep in entry_points(group=_EXTERNAL_GROUP):
        if ep.name != name:
            continue
        try:
            factory = ep.load()
            return cast(SourceConnector, factory(http=http, params=params))
        except Exception:
            logger.exception("Плагин источника %r не загрузился", name)
            return None
    return None
