"""Коннектор Habr Career: открытый JSON API фронтенда (проверен 2026-10-12, без авторизации)."""

import logging
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any

from job_hunter.domain.models import (
    Employer,
    EmploymentType,
    RemoteMode,
    Salary,
    Vacancy,
)
from job_hunter.infrastructure.http import HttpClient

logger = logging.getLogger(__name__)

_SITE = "https://career.habr.com"

_EMPLOYMENT_MAP = {
    "full_time": EmploymentType.FULL_TIME,
    "part_time": EmploymentType.PART_TIME,
    "contract": EmploymentType.CONTRACT,
    "internship": EmploymentType.INTERNSHIP,
}


class HabrCareerConnector:
    """Поиск вакансий Habr Career через GET /api/frontend/vacancies?q=...&page=N."""

    name = "habr_career"

    def __init__(self, http: HttpClient, *, max_pages: int = 5) -> None:
        self._http = http
        self._max_pages = max_pages

    def is_available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def search(self, query: str | None = None, *, limit: int = 100) -> Iterable[Vacancy]:
        return self._iter(query=query, limit=limit)

    def _iter(self, *, query: str | None, limit: int) -> Iterator[Vacancy]:
        fetched = 0
        for page in range(1, self._max_pages + 1):
            data: dict[str, Any] = self._http.get_json(
                f"{_SITE}/api/frontend/vacancies", params={"q": query or "", "page": page}
            )
            items: list[dict[str, Any]] = data.get("list") or []
            if not items:
                break
            for item in items:
                if fetched >= limit:
                    return
                vacancy = self._map(item)
                if vacancy is not None:
                    fetched += 1
                    yield vacancy
            if fetched >= limit:
                return

    @staticmethod
    def _map(item: dict[str, Any]) -> Vacancy | None:
        if not item.get("id") or not item.get("title"):
            return None

        company = item.get("company") or {}
        salary_raw = item.get("salary") or {}
        salary = Salary(
            min_amount=salary_raw.get("from"),
            max_amount=salary_raw.get("to"),
            currency=(salary_raw.get("currency") or None),
        )
        published = (item.get("publishedDate") or {}).get("date")
        locations = tuple(loc["title"] for loc in (item.get("locations") or []) if loc.get("title"))
        skills = tuple(s["title"] for s in (item.get("skills") or []) if s.get("title"))

        return Vacancy(
            source=HabrCareerConnector.name,
            external_id=str(item["id"]),
            url=f"{_SITE}{item.get('href', '')}",
            title=str(item["title"]),
            employer=Employer(
                name=str(company.get("title") or "—"),
                external_id=str(company["id"]) if company.get("id") else None,
                url=f"{_SITE}{company['href']}" if company.get("href") else None,
            ),
            salary=salary if salary.min_amount or salary.max_amount else None,
            skills=skills,
            locations=locations,
            remote=RemoteMode.REMOTE if item.get("remoteWork") else RemoteMode.ONSITE,
            employment=_EMPLOYMENT_MAP.get(str(item.get("employment")), EmploymentType.UNKNOWN),
            seniority=item.get("qualification"),
            published_at=datetime.fromisoformat(published) if published else None,
        )
