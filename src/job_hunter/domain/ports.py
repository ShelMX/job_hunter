"""Порты (интерфейсы) — контракты, реализуемые адаптерами инфраструктуры и коннекторами."""

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Protocol

from job_hunter.domain.models import Vacancy, VacancyRecord, VacancyState, VacancyStatus


class SourceConnector(Protocol):
    """Порт внешнего источника вакансий. Реализуется плагинами в job_hunter.connectors.*"""

    name: str

    def is_available(self) -> bool:
        """Доступен ли источник прямо сейчас (например, хватает ли credentials)."""
        ...

    def unavailable_reason(self) -> str:
        """Человекочитаемая причина недоступности (для `sources list`)."""
        ...

    def search(self, query: str | None = None, *, limit: int = 100) -> Iterable[Vacancy]:
        """Вернуть нормализованные вакансии, не более `limit`."""
        ...


class VacancyRepository(Protocol):
    """Порт хранилища вакансий."""

    def upsert(self, vacancy: Vacancy, *, seen_at: datetime) -> bool:
        """Сохранить вакансию. Возвращает True, если создана новая запись."""
        ...

    def get(self, record_id: int) -> VacancyRecord | None: ...

    def list_recent(
        self,
        *,
        source: str | None = None,
        limit: int = 50,
        offset: int = 0,
        query: str | None = None,
    ) -> Sequence[VacancyRecord]: ...

    def count(self, *, source: str | None = None, query: str | None = None) -> int: ...


class LLMUnavailableError(Exception):
    """LLM-роль недоступна (RAM/наличие модели) — сигнал деградации, не падения."""


class LLMClient(Protocol):
    """Порт LLM-провайдера (PLAN §7.1: локальные модели, ресурс-дисциплина)."""

    def provider_name(self) -> str: ...

    def is_available(self) -> bool: ...

    def complete(self, *, role: str, system: str, user: str) -> str:
        """Один запрос к модели роли role ("extract"/"score"). Ожидается JSON-ответ."""
        ...

    def decide(self, *, role: str, task: str, options: Sequence[str]) -> int | None:
        """Decision-задача: выбрать индекс одной из options (модели типа tev1)."""
        ...

    def unload_used(self) -> None:
        """Выгрузить из памяти только те модели, которые это клиент загружал в сессии."""
        ...


class LLMCache(Protocol):
    """Порт кэша LLM-ответов (ключ = хэш входа; повторно модель не дёргаем)."""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, *, role: str, model: str, output: str) -> None: ...


class StateRepository(Protocol):
    """Порт состояния вакансий в воронке кандидата и скрытых работодателей."""

    def set_status(
        self,
        record_id: int,
        status: VacancyStatus,
        *,
        applied_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None: ...

    def set_note(self, record_id: int, note: str | None, *, now: datetime | None = None) -> None: ...

    def states(self) -> dict[int, VacancyState]: ...

    def hide_employer(self, name: str, *, now: datetime | None = None) -> None: ...

    def unhide_employer(self, name: str) -> None: ...

    def hidden_employers(self) -> set[str]: ...
