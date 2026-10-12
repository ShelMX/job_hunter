from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine

from job_hunter.domain.models import Employer, Vacancy, VacancyRecord
from job_hunter.infrastructure.db.orm import Base


@pytest.fixture()
def engine() -> Iterator[Engine]:
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def make_vacancy():
    def _make(
        external_id: str,
        *,
        title: str = "Инженер",
        source: str = "fake",
        employer_name: str = "ООО Тест",
    ) -> Vacancy:
        return Vacancy(
            source=source,
            external_id=external_id,
            url=f"https://example.com/jobs/{external_id}",
            title=title,
            employer=Employer(name=employer_name),
        )

    return _make


@pytest.fixture()
def make_record():
    import dataclasses

    def _make(
        record_id: int,
        vacancy: Vacancy,
        *,
        days_old: int = 0,
        times_seen: int = 1,
    ) -> VacancyRecord:
        now = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
        published = now - timedelta(days=days_old)
        vacancy = dataclasses.replace(vacancy, published_at=vacancy.published_at or published)
        return VacancyRecord(
            id=record_id,
            vacancy=vacancy,
            first_seen_at=published,
            last_seen_at=now,
            times_seen=times_seen,
            content_hash=f"h{record_id}",
        )

    return _make
