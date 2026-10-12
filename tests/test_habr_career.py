from datetime import timedelta

import httpx
import respx

from job_hunter.connectors.habr_career import HabrCareerConnector
from job_hunter.domain.models import EmploymentType, RemoteMode
from job_hunter.infrastructure.http import HttpClient

PAYLOAD = {
    "list": [
        {
            "id": 1000168684,
            "href": "/vacancies/1000168684",
            "title": "ML-инженер (Python · PyTorch)",
            "remoteWork": True,
            "publishedDate": {"date": "2026-10-06T10:23:57+03:00"},
            "company": {"id": 1000125680, "href": "/companies/ulgroup", "title": "Урал Логистика"},
            "employment": "full_time",
            "salary": {"from": 250000, "to": 320000, "currency": "rur"},
            "skills": [{"title": "Python"}, {"title": "PyTorch"}],
            "locations": [{"title": "Москва"}],
            "qualification": "Middle",
        }
    ]
}


def _handler(request: httpx.Request) -> httpx.Response:
    page = request.url.params.get("page", "1")
    return httpx.Response(200, json={"list": PAYLOAD["list"] if page == "1" else []})


def test_habr_connector_maps_fields() -> None:
    http = HttpClient(user_agent="test", timeout=5, max_retries=1)
    try:
        with respx.mock:
            respx.get("https://career.habr.com/api/frontend/vacancies").mock(side_effect=_handler)
            vacancies = list(HabrCareerConnector(http).search("python"))

        assert len(vacancies) == 1
        v = vacancies[0]
        assert v.source == "habr_career"
        assert v.external_id == "1000168684"
        assert v.url == "https://career.habr.com/vacancies/1000168684"
        assert v.employer.name == "Урал Логистика"
        assert v.employer.url == "https://career.habr.com/companies/ulgroup"
        assert v.salary is not None
        assert v.salary.min_amount == 250000
        assert v.salary.max_amount == 320000
        assert v.salary.currency == "rur"
        assert v.skills == ("Python", "PyTorch")
        assert v.locations == ("Москва",)
        assert v.remote == RemoteMode.REMOTE
        assert v.employment == EmploymentType.FULL_TIME
        assert v.seniority == "Middle"
        assert v.published_at is not None
        assert v.published_at.utcoffset() == timedelta(hours=3)
    finally:
        http.close()


def test_habr_connector_respects_limit_and_stops_on_empty_page() -> None:
    http = HttpClient(user_agent="test", timeout=5, max_retries=1)
    try:
        with respx.mock:
            route = respx.get("https://career.habr.com/api/frontend/vacancies").mock(side_effect=_handler)
            vacancies = list(HabrCareerConnector(http).search(limit=1))
        assert len(vacancies) == 1
        assert route.call_count == 1  # limit сработал — вторая страница не запрашивалась
    finally:
        http.close()
