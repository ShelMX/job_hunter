from datetime import UTC, datetime

from job_hunter.application.dedup import find_duplicate_clusters
from job_hunter.application.employers import compute_vacancy_analytics, employer_stats

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def test_vacancy_analytics_repost_hints(make_vacancy, make_record) -> None:
    repost1 = make_record(1, make_vacancy("a", title="Аналитик", employer_name="Рога"), times_seen=4)
    repost2 = make_record(2, make_vacancy("b", title="Аналитик!", employer_name="Рога"))
    solo = make_record(3, make_vacancy("c", title="Девопс", employer_name="Другие"), days_old=45)
    records = [repost1, repost2, solo]
    clusters = find_duplicate_clusters(records, similarity_threshold=90.0)

    a = compute_vacancy_analytics(repost1, records, clusters, now=NOW)
    assert a.cluster_size == 2
    assert a.times_seen == 4
    assert a.employer_open_vacancies == 2

    s = compute_vacancy_analytics(solo, records, clusters, now=NOW)
    assert s.cluster_size == 1
    assert s.age_days == 45


def test_employer_stats(make_vacancy, make_record) -> None:
    records = [
        make_record(1, make_vacancy("a", title="Аналитик", employer_name="Рога")),
        make_record(2, make_vacancy("b", title="Аналитик!", employer_name="Рога")),
        make_record(3, make_vacancy("c", title="Старая", employer_name="Рога"), days_old=40),
    ]
    clusters = find_duplicate_clusters(records, similarity_threshold=90.0)
    stats = employer_stats("Рога", records, clusters, evergreen_days=30, now=NOW)
    assert stats.vacancies_total == 3
    assert stats.duplicate_groups == 1
    assert abs(stats.evergreen_share - 1 / 3) < 0.01
