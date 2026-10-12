"""Коннектор hh.ru. С апреля 2026 поиск требует OAuth (client credentials).

Регистрация приложения: https://dev.hh.ru → env JOB_HUNTER_HH_CLIENT_ID / JOB_HUNTER_HH_CLIENT_SECRET.
"""

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
from job_hunter.infrastructure.text import strip_html

logger = logging.getLogger(__name__)

_EMPLOYMENT_MAP = {
    "full": EmploymentType.FULL_TIME,
    "part": EmploymentType.PART_TIME,
    "project": EmploymentType.CONTRACT,
    "probation": EmploymentType.INTERNSHIP,
}


class HHConnector:
    name = "hh"

    def __init__(
        self,
        http: HttpClient,
        *,
        client_id: str | None,
        client_secret: str | None,
        base_url: str = "https://api.hh.ru",
        per_page: int = 50,
        user_agent: str = "job-hunter/0.1",
        max_pages: int = 4,
    ) -> None:
        self._http = http
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._per_page = per_page
        self._user_agent = user_agent
        self._max_pages = max_pages
        self._token: str | None = None

    def is_available(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def unavailable_reason(self) -> str:
        return (
            "нужны JOB_HUNTER_HH_CLIENT_ID / JOB_HUNTER_HH_CLIENT_SECRET "
            "(зарегистрируйте приложение на https://dev.hh.ru)"
        )

    def search(self, query: str | None = None, *, limit: int = 100) -> Iterable[Vacancy]:
        return self._iter(query=query, limit=limit)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "HH-User-Agent": self._user_agent,
        }

    def _get_token(self) -> str:
        if self._token is None:
            data = self._http.post_form(
                f"{self._base_url}/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id or "",
                    "client_secret": self._client_secret or "",
                },
            )
            self._token = str(data["access_token"])
            logger.info("hh.ru: получен OAuth application token")
        return self._token

    def _iter(self, *, query: str | None, limit: int) -> Iterator[Vacancy]:
        fetched = 0
        for page in range(self._max_pages):
            data: dict[str, Any] = self._http.get_json(
                f"{self._base_url}/vacancies",
                params={"text": query or "", "per_page": self._per_page, "page": page},
                headers=self._headers(),
            )
            items: list[dict[str, Any]] = data.get("items") or []
            for item in items:
                if fetched >= limit:
                    return
                vacancy = self._map(item)
                if vacancy is not None:
                    fetched += 1
                    yield vacancy
            if page + 1 >= int(data.get("pages") or 0):
                return

    @staticmethod
    def _map(item: dict[str, Any]) -> Vacancy | None:
        if not item.get("id") or not item.get("name"):
            return None

        employer_raw = item.get("employer") or {}
        salary_raw = item.get("salary") or {}
        snippet = item.get("snippet") or {}
        description = strip_html(f"{snippet.get('requirement') or ''}\n{snippet.get('responsibility') or ''}")
        area = item.get("area") or {}
        schedule = item.get("schedule") or {}
        employment = item.get("employment") or {}

        published = None
        if item.get("published_at"):
            published = datetime.fromisoformat(str(item["published_at"]))

        salary = Salary(
            min_amount=salary_raw.get("from"),
            max_amount=salary_raw.get("to"),
            currency=salary_raw.get("currency"),
            gross=salary_raw.get("gross"),
        )

        return Vacancy(
            source=HHConnector.name,
            external_id=str(item["id"]),
            url=str(item.get("alternate_url") or ""),
            title=str(item["name"]),
            employer=Employer(
                name=str(employer_raw.get("name") or "—"),
                external_id=str(employer_raw["id"]) if employer_raw.get("id") else None,
                url=employer_raw.get("alternate_url"),
            ),
            description=description,
            salary=salary if salary.min_amount or salary.max_amount else None,
            locations=(str(area["name"]),) if area.get("name") else (),
            remote=RemoteMode.REMOTE if schedule.get("id") == "remote" else RemoteMode.UNKNOWN,
            employment=_EMPLOYMENT_MAP.get(str(employment.get("id")), EmploymentType.UNKNOWN),
            published_at=published,
        )
