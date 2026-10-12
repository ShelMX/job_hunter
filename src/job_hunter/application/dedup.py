"""Дедупликация и детект перепостов: нечёткая кластеризация (rapidfuzz, MIT) + union-find."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz

from job_hunter.domain.models import VacancyRecord

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    value = _PUNCT_RE.sub(" ", value.lower())
    return _WS_RE.sub(" ", value).strip()


@dataclass(frozen=True, slots=True)
class DuplicateCluster:
    """Группа вакансий, являющихся одной и той же позицией (дубли между источниками / перепосты)."""

    canonical_id: int  # представитель — самый свежий по published_at
    record_ids: tuple[int, ...]
    min_similarity: float


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]  # path halving
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def find_duplicate_clusters(
    records: Sequence[VacancyRecord], *, similarity_threshold: float = 96.0
) -> list[DuplicateCluster]:
    """Кластеры из ≥2 записей: одинаковый работодатель + почти совпадающий заголовок."""
    uf = _UnionFind()
    similarities: dict[frozenset[int], float] = {}

    titles = {r.id: normalize_text(r.vacancy.title) for r in records}
    employers = {r.id: normalize_text(r.vacancy.employer.name) for r in records}

    for i, left in enumerate(records):
        for right in records[i + 1 :]:
            if employers[left.id] != employers[right.id]:
                continue
            sim = fuzz.token_sort_ratio(titles[left.id], titles[right.id])
            if sim >= similarity_threshold:
                uf.union(left.id, right.id)
                similarities[frozenset({left.id, right.id})] = sim

    groups: dict[int, list[int]] = {}
    for record in records:
        root = uf.find(record.id)
        groups.setdefault(root, []).append(record.id)

    by_id = {r.id: r for r in records}
    clusters: list[DuplicateCluster] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        canonical = max(
            members,
            key=lambda rid: by_id[rid].vacancy.published_at or by_id[rid].first_seen_at,
        )
        cluster_sims = [s for pair, s in similarities.items() if pair.issubset(set(members) | pair)]
        clusters.append(
            DuplicateCluster(
                canonical_id=canonical,
                record_ids=tuple(sorted(members)),
                min_similarity=min(cluster_sims, default=similarity_threshold),
            )
        )
    return clusters
