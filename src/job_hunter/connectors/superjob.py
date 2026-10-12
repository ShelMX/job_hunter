"""Коннектор SuperJob: официальный API, ключ приложения заголовком X-Api-App-Id.

Регистрация: https://api.superjob.ru/register/ (обычный аккаунт соискателя подходит).
Ключ — в env JOB_HUNTER_SJ_SECRET_KEY или .env (sj = SuperJob).
"""

import logging
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

from job_hunter.domain.models import Employer, RemoteMode, Salary, Vacancy
from job_hunter.infrastructure.http import HttpClient

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100  # максимум API (count)


class SuperJobConnector:
    name = "superjob"

    def __init__(
        self,
        http: HttpClient,
        *,
        secret_key: str | None,
        base_url: str = "https://api.superjob.ru/2.0",
        max_pages: int = 5,
    ) -> None:
        self._http = http
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._max_pages = max_pages

    def is_available(self) -> bool:
        return bool(self._secret_key)

    def unavailable_reason(self) -> str:
        return (
            "нужен JOB_HUNTER_SJ_SECRET_KEY (Secret key приложения с "
            "https://api.superjob.ru/register/ — регистрация без модерации)"
        )

    def _headers(self) -> dict[str, str]:
        return {"X-Api-App-Id": self._secret_key or ""}

    def search(self, query: str | None = None, *, limit: int = 100) -> Iterable[Vacancy]:
        return self._iter(query=query, limit=limit)

    def _iter(self, *, query: str | None, limit: int) -> Iterator[Vacancy]:
        fetched = 0
        for page in range(self._max_pages):
            data: dict[str, Any] = self._http.get_json(
                f"{self._base_url}/vacancies/",
                params={"keyword": query or "", "count": _PAGE_SIZE, "page": page},
                headers=self._headers(),
            )
            for item in data.get("objects") or []:
                if fetched >= limit:
                    return
                vacancy = self._map(item)
                if vacancy is not None:
                    fetched += 1
                    yield vacancy
            if not data.get("more"):
                return

    @staticmethod
    def _map(item: dict[str, Any]) -> Vacancy | None:
        if not item.get("id") or not item.get("profession"):
            return None

        town = item.get("town") or {}
        # remote у SuperJob — это place_of_work="на дому", а не type_of_work (by docs)
        place = str((item.get("place_of_work") or {}).get("title") or "").lower()
        work_type = str((item.get("type_of_work") or {}).get("title") or "").lower()
        is_remote = "на дому" in place or "далённ" in work_type
        payment_from, payment_to = item.get("payment_from"), item.get("payment_to")
        salary = None
        if payment_from or payment_to:
            salary = Salary(
                min_amount=int(payment_from) if payment_from else None,
                max_amount=int(payment_to) if payment_to else None,
                currency=str(item.get("currency") or "").upper() or None,
            )

        published: datetime | None = None
        if ts := item.get("date_published"):
            published = datetime.fromtimestamp(int(ts), tz=UTC)

        description = "\n\n".join(
            part
            for part in (
                str(item.get("candidat") or "").strip(),  # требования
                str(item.get("work") or "").strip(),  # обязанности
                str(item.get("compensation") or "").strip(),  # условия работы
            )
            if part
        )

        return Vacancy(
            source=SuperJobConnector.name,
            external_id=str(item["id"]),
            url=str(item.get("link") or ""),
            title=str(item["profession"]),
            employer=Employer(name=str(item.get("firm_name") or "—")),
            description=description,
            salary=salary,
            locations=(str(town["title"]),) if town.get("title") else (),
            remote=RemoteMode.REMOTE if is_remote else RemoteMode.UNKNOWN,
            published_at=published,
        )
