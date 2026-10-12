"""Use-case: сбор вакансий из источников в хранилище."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from job_hunter.domain.ports import SourceConnector, VacancyRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FetchStats:
    source: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    skipped_reason: str | None = None


class IngestVacancies:
    def __init__(self, connectors: Sequence[SourceConnector], repository: VacancyRepository) -> None:
        self._connectors = connectors
        self._repository = repository

    def execute(
        self,
        *,
        source_name: str | None = None,
        query: str | None = None,
        limit: int = 100,
        now: datetime | None = None,
    ) -> list[FetchStats]:
        seen_at = now or datetime.now(UTC)
        stats: list[FetchStats] = []

        for connector in self._select(source_name):
            if not connector.is_available():
                stats.append(FetchStats(source=connector.name, skipped_reason=connector.unavailable_reason()))
                continue
            fetched = created = updated = 0
            try:
                for vacancy in connector.search(query, limit=limit):
                    fetched += 1
                    if self._repository.upsert(vacancy, seen_at=seen_at):
                        created += 1
                    else:
                        updated += 1
            except Exception:
                logger.exception("Источник %s упал во время сбора", connector.name)
                stats.append(FetchStats(source=connector.name, skipped_reason="ошибка, см. лог"))
                continue
            stats.append(FetchStats(source=connector.name, fetched=fetched, created=created, updated=updated))
        return stats

    def _select(self, source_name: str | None) -> Sequence[SourceConnector]:
        if source_name is None:
            return self._connectors
        return [c for c in self._connectors if c.name == source_name]
