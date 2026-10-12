import httpx
import respx

from job_hunter.connectors.remoteok import RemoteOkConnector
from job_hunter.domain.models import RemoteMode
from job_hunter.infrastructure.http import HttpClient

PAYLOAD = [
    {"legal": "API Terms of Service ..."},  # юридическая шапка — пропускается
    {
        "id": "1137475",
        "slug": "remote-senior-python-developer-lemon-io-1137475",
        "url": "https://remoteok.com/remote-jobs/1137475",
        "date": "2026-10-09T13:09:18+00:00",
        "company": "Lemon.io",
        "position": "Senior Python Developer",
        "tags": ["python", "django"],
        "location": "Worldwide",
        "salary_min": 80000,
        "salary_max": 120000,
        "description": "<p>We are looking for <b>Python</b> dev.</p>",
    },
]


def _make() -> tuple[RemoteOkConnector, HttpClient]:
    http = HttpClient(user_agent="test", timeout=5, max_retries=1)
    return RemoteOkConnector(http), http


def test_remoteok_connector_maps_fields_and_skips_legal() -> None:
    connector, http = _make()
    try:
        with respx.mock:
            respx.get("https://remoteok.com/api").mock(return_value=httpx.Response(200, json=PAYLOAD))
            vacancies = list(connector.search())

        assert len(vacancies) == 1
        v = vacancies[0]
        assert v.source == "remoteok"
        assert v.external_id == "1137475"
        assert v.title == "Senior Python Developer"
        assert v.employer.name == "Lemon.io"
        assert v.skills == ("python", "django")
        assert v.locations == ("Worldwide",)
        assert v.remote == RemoteMode.REMOTE
        assert v.salary is not None and v.salary.min_amount == 80000
        assert v.published_at is not None
        assert "Python" in v.description and "<b>" not in v.description
    finally:
        http.close()


def test_remoteok_connector_filters_by_query() -> None:
    connector, http = _make()
    try:
        with respx.mock:
            respx.get("https://remoteok.com/api").mock(return_value=httpx.Response(200, json=PAYLOAD))
            assert len(list(connector.search("rust"))) == 0
            assert len(list(connector.search("python"))) == 1
    finally:
        http.close()
