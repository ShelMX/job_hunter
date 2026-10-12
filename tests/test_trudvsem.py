import httpx
import respx

from job_hunter.connectors.trudvsem import TrudvsemConnector
from job_hunter.domain.models import RemoteMode
from job_hunter.infrastructure.http import HttpClient

# сокращённая, но структурно верная выжимка реального ответа API (2026-10-12)
PAYLOAD = {
    "status": "200",
    "request": {"api": "v1"},
    "meta": {"total": "2", "limit": 100},
    "results": {
        "vacancies": [
            {
                "vacancy": {
                    "id": "c50d9cd8-bbe3-11f1-9a11-2f8460579df4",
                    "region": {"region_code": "6300000000000", "name": "Самарская область"},
                    "company": {
                        "companycode": "1227700702074",
                        "inn": "9703115178",
                        "name": 'ООО "УРАЛАЙТЕХ"',
                        "url": "https://trudvsem.ru/company/1227700702074",
                    },
                    "creation-date": "2026-09-29",
                    "salary": "от 90000",
                    "salary_min": 90000,
                    "salary_max": 100000,
                    "job-name": "Разработчик Python",
                    "vac_url": "https://trudvsem.ru/vacancy/card/1227700702074/c50d9cd8",
                    "schedule": "Полный рабочий день",
                    "requirements": "Опыт разработки на Python. FastAPI, SQLAlchemy.",
                    "duty": "Разработка ПО на Python.",
                }
            },
            {
                "vacancy": {
                    "id": "aaaa-bbbb",
                    "region": {"name": "Москва"},
                    "company": {"name": "Рога и Ко"},
                    "creation-date": "2026-10-01",
                    "job-name": "Data Engineer",
                    "vac_url": "https://trudvsem.ru/vacancy/card/1/aaaa-bbbb",
                    "schedule": "Удалённая работа",
                }
            },
        ]
    },
}


def test_trudvsem_mapping_and_pagination_stop() -> None:
    http = HttpClient(user_agent="test", timeout=5, max_retries=1)
    connector = TrudvsemConnector(http, base_url="https://opendata.trudvsem.ru/api/v1")
    try:
        with respx.mock:
            route = respx.get("https://opendata.trudvsem.ru/api/v1/vacancies").mock(
                return_value=httpx.Response(200, json=PAYLOAD)
            )
            vacancies = list(connector.search("python"))

        assert len(vacancies) == 2
        assert route.call_count == 1  # total=2 меньше limit — второй страницы нет
        assert route.calls.last.request.url.params["text"] == "python"

        v1, v2 = vacancies
        assert v1.source == "trudvsem"
        assert v1.external_id == "c50d9cd8-bbe3-11f1-9a11-2f8460579df4"
        assert v1.title == "Разработчик Python"
        assert v1.employer.name == 'ООО "УРАЛАЙТЕХ"'
        assert v1.employer.external_id == "9703115178"  # ИНН — для наблюдения за работодателем
        assert v1.salary is not None
        assert (v1.salary.min_amount, v1.salary.max_amount, v1.salary.currency) == (
            90000,
            100000,
            "RUR",
        )
        assert v1.locations == ("Самарская область",)
        assert v1.remote == RemoteMode.UNKNOWN
        assert v1.published_at is not None
        assert v1.published_at.date().isoformat() == "2026-09-29"
        assert "FastAPI" in v1.description and "Разработка ПО" in v1.description

        assert v2.salary is None
        assert v2.remote == RemoteMode.REMOTE
    finally:
        http.close()
