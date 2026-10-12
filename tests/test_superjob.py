import httpx
import respx

from job_hunter.connectors.superjob import SuperJobConnector
from job_hunter.domain.models import RemoteMode
from job_hunter.infrastructure.http import HttpClient

ITEM = {
    "id": 50000001,
    "profession": "Python-разработчик",
    "firm_name": "ООО Супер",
    "link": "https://superjob.ru/vakansii/python-razrabotchik-50000001.html",
    "date_published": 1791551358,
    "payment_from": 250000,
    "payment_to": 320000,
    "currency": "rub",
    "town": {"title": "Москва"},
    "type_of_work": {"title": "Полный рабочий день"},
    "place_of_work": {"title": "на дому"},
    "candidat": "Опыт Python от 3 лет, FastAPI",
    "work": "Разработка backend-сервисов",
}


def _make(key: str | None) -> tuple[SuperJobConnector, HttpClient]:
    http = HttpClient(user_agent="test", timeout=5, max_retries=1)
    return SuperJobConnector(http, secret_key=key), http


def test_unavailable_without_key() -> None:
    connector, http = _make(None)
    try:
        assert not connector.is_available()
        assert "api.superjob.ru" in connector.unavailable_reason()
    finally:
        http.close()


def test_key_as_header_and_mapping() -> None:
    connector, http = _make("sj-secret-123")
    try:
        with respx.mock:
            route = respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
                return_value=httpx.Response(200, json={"objects": [ITEM], "total": 1, "more": False})
            )
            vacancies = list(connector.search("python"))

        assert route.calls.last.request.headers.get("X-Api-App-Id") == "sj-secret-123"
        assert route.calls.last.request.url.params["keyword"] == "python"

        assert len(vacancies) == 1
        v = vacancies[0]
        assert v.source == "superjob"
        assert v.external_id == "50000001"
        assert v.title == "Python-разработчик"
        assert v.employer.name == "ООО Супер"
        assert v.salary is not None
        assert (v.salary.min_amount, v.salary.max_amount, v.salary.currency) == (
            250000,
            320000,
            "RUB",
        )
        assert v.locations == ("Москва",)
        assert v.remote == RemoteMode.REMOTE
        assert v.published_at is not None and v.published_at.year == 2026
        assert "FastAPI" in v.description and "backend" in v.description
    finally:
        http.close()
