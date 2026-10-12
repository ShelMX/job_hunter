from datetime import timedelta

import httpx
import respx

from job_hunter.connectors.hh import HHConnector
from job_hunter.domain.models import EmploymentType, RemoteMode
from job_hunter.infrastructure.http import HttpClient

ITEM = {
    "id": "12345678",
    "name": "Python-разработчик",
    "alternate_url": "https://hh.ru/vacancy/12345678",
    "published_at": "2026-10-06T10:23:57+0300",
    "employer": {"id": "999", "name": "ООО Ромашка", "alternate_url": "https://hh.ru/employer/999"},
    "salary": {"from": 200000, "to": 300000, "currency": "RUR", "gross": True},
    "area": {"name": "Москва"},
    "schedule": {"id": "remote", "name": "Удалённая работа"},
    "employment": {"id": "full"},
    "snippet": {
        "requirement": "Опыт работы с <b>Python</b> от 3 лет",
        "responsibility": "Разработка сервисов",
    },
}


def _make(client_id: str | None, client_secret: str | None) -> tuple[HHConnector, HttpClient]:
    http = HttpClient(user_agent="test-agent", timeout=5, max_retries=1)
    connector = HHConnector(http, client_id=client_id, client_secret=client_secret, user_agent="test-agent")
    return connector, http


def test_hh_unavailable_without_credentials() -> None:
    connector, http = _make(None, None)
    try:
        assert not connector.is_available()
        assert "dev.hh.ru" in connector.unavailable_reason()
    finally:
        http.close()


def test_hh_oauth_flow_and_mapping() -> None:
    connector, http = _make("cid", "csecret")
    try:
        with respx.mock:
            respx.post("https://api.hh.ru/token").mock(
                return_value=httpx.Response(200, json={"access_token": "tok123"})
            )
            search = respx.get("https://api.hh.ru/vacancies").mock(
                return_value=httpx.Response(200, json={"items": [ITEM], "pages": 1, "page": 0})
            )
            vacancies = list(connector.search("python"))

        assert len(vacancies) == 1
        v = vacancies[0]
        assert v.external_id == "12345678"
        assert v.title == "Python-разработчик"
        assert v.employer.name == "ООО Ромашка"
        assert v.salary is not None
        assert (v.salary.min_amount, v.salary.max_amount, v.salary.currency) == (200000, 300000, "RUR")
        assert v.salary.gross is True
        assert v.locations == ("Москва",)
        assert v.remote == RemoteMode.REMOTE
        assert v.employment == EmploymentType.FULL_TIME
        assert v.published_at is not None and v.published_at.utcoffset() == timedelta(hours=3)
        assert "Python от 3 лет" in v.description and "Разработка сервисов" in v.description

        # OAuth-токен реально был получен и передан в Authorization
        auth = search.calls.last.request.headers.get("Authorization")
        assert auth == "Bearer tok123"
    finally:
        http.close()
