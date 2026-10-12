# ruff: noqa: E501
# XML-фикстуры сознательно однострочные — это реальные сырые ответы источников.
import httpx
import respx

from job_hunter.connectors.rss import RssConnector
from job_hunter.domain.models import RemoteMode
from job_hunter.infrastructure.http import HttpClient

HH_XML = """<?xml version='1.0' encoding='utf-8'?>
<rss version="2.0"><channel><title>HeadHunter Vacancy</title>
<item><pubDate>2026-10-09T16:43:35.440+03:00</pubDate><title>Системный администратор/AI Engineer</title><link>https://hh.ru/vacancy/138313195</link><guid isPermaLink="true">https://hh.ru/vacancy/138313195</guid><description><![CDATA[<p>Вакансия компании: GETWOLA</p> <p>Создана: 09.10.2026</p> <p>Регион: Москва</p> <p>Предполагаемый уровень месячного дохода: от 100 000 ₽</p>]]></description></item>
<item><pubDate>2026-10-06T06:40:27.078+03:00</pubDate><title>Senior backend developer (PHP, Symfony) в Fintech</title><link>https://hh.ru/vacancy/136083468</link><guid isPermaLink="true">https://hh.ru/vacancy/136083468</guid><description><![CDATA[<p>Вакансия компании: еКапуста</p> <p>Создана: 06.10.2026</p> <p>Регион: Санкт-Петербург</p> <p>Предполагаемый уровень месячного дохода: от 400 000 до 600 000 ₽</p>]]></description></item>
<item><pubDate>2026-10-06T17:34:29.389+03:00</pubDate><title>Антифрод-исследователь</title><link>https://hh.ru/vacancy/138185923</link><guid isPermaLink="true">https://hh.ru/vacancy/138185923</guid><description><![CDATA[<p>Вакансия компании: HeadHunter</p> <p>Создана: 06.10.2026</p> <p>Регион: Москва</p> <p>Предполагаемый уровень месячного дохода: не указан</p>]]></description></item>
</channel></rss>
"""

WWR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>We Work Remotely</title>
<item><title>ZoomInfo Technologies LLC: Director, Applied AI</title><link>https://weworkremotely.com/jobs/12345</link><guid>https://weworkremotely.com/jobs/12345</guid><region>Anywhere in the World</region><pubDate>Thu, 09 Oct 2026 13:43:35 +0000</pubDate><description>&lt;p&gt;Great role&lt;/p&gt;</description></item>
</channel></rss>
"""


def _http() -> HttpClient:
    return HttpClient(user_agent="test", timeout=5, max_retries=1)


def test_hh_rss_mapping() -> None:
    http = _http()
    connector = RssConnector(
        http,
        name="hh_rss",
        feed_url="https://hh.ru/search/vacancy/rss",
        query_param="text",
        item_parser="hh_ru",
    )
    try:
        with respx.mock:
            route = respx.get("https://hh.ru/search/vacancy/rss").mock(
                return_value=httpx.Response(200, content=HH_XML.encode())
            )
            vacancies = list(connector.search("python"))

        assert route.called
        assert route.calls.last.request.url.params["text"] == "python"
        assert len(vacancies) == 3

        v1, v2, v3 = vacancies
        assert v1.external_id == "138313195"
        assert v1.employer.name == "GETWOLA"
        assert v1.locations == ("Москва",)
        assert v1.salary is not None
        assert (v1.salary.min_amount, v1.salary.max_amount) == (100000, None)
        assert v1.salary.currency == "RUR"
        assert "Вакансия компании: GETWOLA" in v1.description
        assert v1.published_at is not None and v1.published_at.tzinfo is not None

        assert v2.salary is not None
        assert (v2.salary.min_amount, v2.salary.max_amount) == (400000, 600000)

        assert v3.salary is None  # "не указан"
    finally:
        http.close()


def test_generic_rss_mapping_and_client_side_query() -> None:
    http = _http()
    connector = RssConnector(
        http,
        name="wwr",
        feed_url="https://weworkremotely.com/x.rss",
        item_parser="generic",
    )
    try:
        with respx.mock:
            respx.get("https://weworkremotely.com/x.rss").mock(
                return_value=httpx.Response(200, content=WWR_XML.encode())
            )
            assert len(list(connector.search("no-such-thing"))) == 0
            vacancies = list(connector.search("applied ai"))

        assert len(vacancies) == 1
        v = vacancies[0]
        assert v.employer.name == "ZoomInfo Technologies LLC"
        assert v.title == "Director, Applied AI"
        assert v.remote == RemoteMode.REMOTE
        assert v.locations == ("Anywhere in the World",)
    finally:
        http.close()
