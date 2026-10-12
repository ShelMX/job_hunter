"""Коннектор «Работа в России» (trudvsem): официальный открытый API Роструда.

Документация: https://trudvsem.ru/opendata/api — GET-only, JSON, без авторизации,
есть текстовый поиск (?text=), пагинация limit/offset и delta-sync (modifiedFrom).
"""

import logging
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

from job_hunter.domain.models import Employer, RemoteMode, Salary, Vacancy
from job_hunter.infrastructure.http import HttpClient

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100  # лимит самого API


class TrudvsemConnector:
    name = "trudvsem"

    def __init__(self, http: HttpClient, *, base_url: str = "https://opendata.trudvsem.ru/api/v1") -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")

    def is_available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def search(self, query: str | None = None, *, limit: int = 100) -> Iterable[Vacancy]:
        return self._iter(query=query, limit=limit)

    def _iter(self, *, query: str | None, limit: int) -> Iterator[Vacancy]:
        fetched = 0
        offset = 0
        while fetched < limit:
            page_limit = min(_PAGE_SIZE, limit - fetched)
            data: dict[str, Any] = self._http.get_json(
                f"{self._base_url}/vacancies",
                params={"offset": offset, "limit": page_limit, "text": query or ""},
            )
            items = (data.get("results") or {}).get("vacancies") or []
            if not items:
                return
            for item in items:
                vacancy = self._map(item.get("vacancy") or {})
                if vacancy is not None:
                    fetched += 1
                    yield vacancy
                    if fetched >= limit:
                        return
            total = int((data.get("meta") or {}).get("total") or 0)
            offset += page_limit
            if offset >= total:
                return

    @staticmethod
    def _map(vac: dict[str, Any]) -> Vacancy | None:
        external_id = str(vac.get("id") or "")
        title = str(vac.get("job-name") or "").strip()
        if not external_id or not title:
            return None

        company = vac.get("company") or {}
        region = vac.get("region") or {}
        salary_min, salary_max = vac.get("salary_min"), vac.get("salary_max")
        salary = None
        if salary_min or salary_max:
            salary = Salary(
                min_amount=int(salary_min) if salary_min else None,
                max_amount=int(salary_max) if salary_max else None,
                currency="RUR",
            )

        schedule = str(vac.get("schedule") or "")
        remote = RemoteMode.REMOTE if "далённ" in schedule.lower() else RemoteMode.UNKNOWN

        description = "\n\n".join(
            part
            for part in (str(vac.get("requirements") or "").strip(), str(vac.get("duty") or "").strip())
            if part
        )

        published: datetime | None = None
        if creation := vac.get("creation-date"):
            published = datetime.fromisoformat(str(creation)).replace(tzinfo=UTC)

        return Vacancy(
            source=TrudvsemConnector.name,
            external_id=external_id,
            url=str(vac.get("vac_url") or company.get("url") or ""),
            title=title,
            employer=Employer(
                name=str(company.get("name") or "—"),
                external_id=str(company["inn"]) if company.get("inn") else None,
                url=company.get("url"),
            ),
            description=description,
            salary=salary,
            locations=(str(region["name"]),) if region.get("name") else (),
            remote=remote,
            published_at=published,
        )
