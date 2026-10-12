"""Коннектор RemoteOK: открытый JSON API (проверен 2026-10-12). Фильтрация — на клиенте."""

import logging
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

from job_hunter.domain.models import Employer, RemoteMode, Salary, Vacancy
from job_hunter.infrastructure.http import HttpClient
from job_hunter.infrastructure.text import strip_html

logger = logging.getLogger(__name__)


class RemoteOkConnector:
    """GET https://remoteok.com/api возвращает полный список; первый элемент — юридическая шапка."""

    name = "remoteok"

    def __init__(
        self,
        http: HttpClient,
        *,
        base_url: str = "https://remoteok.com/api",
        timeout_seconds: float = 90.0,
    ) -> None:
        self._http = http
        self._base_url = base_url
        self._timeout = timeout_seconds

    def is_available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def search(self, query: str | None = None, *, limit: int = 100) -> Iterable[Vacancy]:
        return self._iter(query=query, limit=limit)

    def _iter(self, *, query: str | None, limit: int) -> Iterator[Vacancy]:
        # RemoteOK отдаёт полный список одним большим ответом и может троттлить —
        # поэтому отдельный длинный таймаут чтения.
        data: list[dict[str, Any]] = self._http.get_json(self._base_url, timeout=self._timeout)
        needle = query.lower() if query else None
        fetched = 0
        for item in data:
            if fetched >= limit:
                return
            if not isinstance(item, dict) or "position" not in item:
                continue  # юридическая шапка и служебные записи
            if needle and needle not in self._haystack(item):
                continue
            vacancy = self._map(item)
            if vacancy is not None:
                fetched += 1
                yield vacancy

    @staticmethod
    def _haystack(item: dict[str, Any]) -> str:
        tags = " ".join(str(t) for t in item.get("tags") or [])
        parts = [str(item.get("position", "")), str(item.get("company", "")), tags]
        return " ".join(parts).lower()

    @staticmethod
    def _map(item: dict[str, Any]) -> Vacancy | None:
        external_id = item.get("id") or item.get("slug")
        if not external_id or not item.get("position"):
            return None

        published: datetime | None = None
        if item.get("date"):
            published = datetime.fromisoformat(str(item["date"]))
        elif item.get("epoch"):
            published = datetime.fromtimestamp(int(item["epoch"]), tz=UTC)

        salary_min, salary_max = item.get("salary_min") or None, item.get("salary_max") or None
        salary = Salary(min_amount=salary_min, max_amount=salary_max) if salary_min or salary_max else None

        location = item.get("location")
        url = item.get("url") or (
            f"https://remoteok.com/remote-jobs/{item['slug']}" if item.get("slug") else ""
        )
        description = strip_html(str(item.get("description") or ""))

        return Vacancy(
            source=RemoteOkConnector.name,
            external_id=str(external_id),
            url=str(url),
            title=str(item["position"]),
            employer=Employer(name=str(item.get("company") or "—")),
            description=description,
            salary=salary,
            skills=tuple(str(t) for t in (item.get("tags") or [])),
            locations=(str(location),) if location else (),
            remote=RemoteMode.REMOTE,
            published_at=published,
        )
