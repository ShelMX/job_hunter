"""Универсальный RSS-коннектор (категория B источников).

Не изобретаем велосипед: XML разбирает feedparser (BSD-2). Специфика источников
выносится в именованные профили разбора описаний (item_parser).
"""

import calendar
import logging
import re
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

import feedparser

from job_hunter.domain.models import Employer, RemoteMode, Salary, Vacancy
from job_hunter.infrastructure.http import HttpClient
from job_hunter.infrastructure.text import strip_html

logger = logging.getLogger(__name__)

# --- hh.ru RSS: CDATA-описание вида
# <p>Вакансия компании: GETWOLA</p><p>Создана: 09.10.2026</p><p>Регион: Москва</p>
# <p>Предполагаемый уровень месячного дохода: от 100 000 ₽ | от 400 000 до 600 000 ₽ | не указан</p>
_HH_COMPANY_RE = re.compile(r"Вакансия компании:\s*(.+)")
_HH_REGION_RE = re.compile(r"Регион:\s*(.+)")
_HH_SALARY_RANGE_RE = re.compile(r"от\s+([\d\s ]+?)\s+до\s+([\d\s ]+)\s*₽")
_HH_SALARY_FROM_RE = re.compile(r"от\s+([\d\s ]+?)\s*₽")
_HH_SALARY_TO_RE = re.compile(r"до\s+([\d\s ]+?)\s*₽")


def _parse_amount(raw: str) -> int | None:
    digits = re.sub(r"[\s ]", "", raw)
    return int(digits) if digits.isdigit() else None


def _entry_datetime(entry: Any) -> datetime | None:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed:
        return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
    return None


def _hh_map(name: str, entry: Any) -> Vacancy | None:
    text = strip_html(getattr(entry, "description", "") or "")
    company = m.group(1).strip() if (m := _HH_COMPANY_RE.search(text)) else "—"
    region = m.group(1).strip() if (m := _HH_REGION_RE.search(text)) else None

    salary = None
    if "не указан" not in text:
        if m := _HH_SALARY_RANGE_RE.search(text):
            salary = Salary(
                min_amount=_parse_amount(m.group(1)),
                max_amount=_parse_amount(m.group(2)),
                currency="RUR",
            )
        elif m := _HH_SALARY_FROM_RE.search(text):
            salary = Salary(min_amount=_parse_amount(m.group(1)), currency="RUR")
        elif m := _HH_SALARY_TO_RE.search(text):
            salary = Salary(max_amount=_parse_amount(m.group(1)), currency="RUR")

    link = str(getattr(entry, "link", "") or getattr(entry, "id", ""))
    external_id = link.rstrip("/").rsplit("/", 1)[-1]
    title = str(getattr(entry, "title", "")).strip()
    if not external_id or not title:
        return None

    return Vacancy(
        source=name,
        external_id=external_id,
        url=link,
        title=title,
        employer=Employer(name=company),
        description=text,
        salary=salary,
        locations=(region,) if region else (),
        remote=RemoteMode.UNKNOWN,
        published_at=_entry_datetime(entry),
    )


def _generic_map(name: str, entry: Any) -> Vacancy | None:
    """Общий профиль (формат заголовка «Компания: Роль», как у WeWorkRemotely)."""
    raw_title = str(getattr(entry, "title", "")).strip()
    if not raw_title:
        return None
    employer_name, title = ("—", raw_title)
    if ": " in raw_title:
        employer_name, title = (p.strip() for p in raw_title.split(": ", 1))
    region = getattr(entry, "region", None)
    link = str(getattr(entry, "link", "") or getattr(entry, "id", ""))
    external_id = str(getattr(entry, "id", "") or link)
    return Vacancy(
        source=name,
        external_id=external_id[:64],
        url=link,
        title=title,
        employer=Employer(name=employer_name),
        description=strip_html(getattr(entry, "description", "") or ""),
        locations=(str(region),) if region else (),
        remote=RemoteMode.REMOTE,
        published_at=_entry_datetime(entry),
    )


_ITEM_PARSERS: dict[str, Callable[[str, Any], Vacancy | None]] = {
    "hh_ru": _hh_map,
    "generic": _generic_map,
}


class RssConnector:
    """Читает RSS-фид вакансий: query уходит серверу (query_param) либо фильтруется на клиенте."""

    def __init__(
        self,
        http: HttpClient,
        *,
        name: str,
        feed_url: str,
        query_param: str | None = None,
        extra_params: dict[str, str] | None = None,
        item_parser: str = "generic",
        timeout_seconds: float | None = None,
    ) -> None:
        self._http = http
        self.name = name
        self._feed_url = feed_url
        self._query_param = query_param
        self._extra_params = extra_params or {}
        self._timeout = timeout_seconds
        if item_parser not in _ITEM_PARSERS:
            raise ValueError(f"Неизвестный item_parser {item_parser!r}: есть {_ITEM_PARSERS}")
        self._item_parser = item_parser

    def _map(self, entry: Any) -> Vacancy | None:
        return _ITEM_PARSERS[self._item_parser](self.name, entry)

    def is_available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def search(self, query: str | None = None, *, limit: int = 100) -> Iterable[Vacancy]:
        return self._iter(query=query, limit=limit)

    def _iter(self, *, query: str | None, limit: int) -> Iterator[Vacancy]:
        params = dict(self._extra_params)
        if query and self._query_param:
            params[self._query_param] = query
        text = self._http.get_text(self._feed_url, params=params, timeout=self._timeout)
        feed = feedparser.parse(text)
        if feed.bozo and not feed.entries:
            raise ValueError(f"RSS-фид {self._feed_url} не распарсился: {feed.bozo_exception}")

        needle = query.lower() if (query and not self._query_param) else None
        yielded = 0
        for entry in feed.entries:
            if yielded >= limit:
                return
            if needle and needle not in (
                f"{getattr(entry, 'title', '')} {getattr(entry, 'description', '')}".lower()
            ):
                continue
            vacancy = self._map(entry)
            if vacancy is not None:
                yielded += 1
                yield vacancy
