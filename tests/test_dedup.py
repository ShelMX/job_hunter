from job_hunter.application.dedup import find_duplicate_clusters


def test_clusters_same_employer_similar_titles(make_vacancy, make_record) -> None:
    records = [
        make_record(1, make_vacancy("a", title="Senior Python Developer", employer_name="Рога")),
        make_record(
            2, make_vacancy("b", title="Senior Python developer!", employer_name="Рога", source="other")
        ),
        make_record(3, make_vacancy("c", title="DevOps инженер", employer_name="Рога")),
        make_record(4, make_vacancy("d", title="Senior Python Developer", employer_name="Копыта")),
    ]
    clusters = find_duplicate_clusters(records, similarity_threshold=90.0)

    assert len(clusters) == 1
    cluster = clusters[0]
    assert set(cluster.record_ids) == {1, 2}  # разные источники — один кластер
    assert cluster.canonical_id in {1, 2}


def test_no_clusters_when_all_distinct(make_vacancy, make_record) -> None:
    records = [
        make_record(1, make_vacancy("a", title="Аналитик данных", employer_name="Рога")),
        make_record(2, make_vacancy("b", title="Backend разработчик", employer_name="Рога")),
        make_record(3, make_vacancy("c", title="Backend разработчик", employer_name="Другая")),
    ]
    assert find_duplicate_clusters(records, similarity_threshold=96.0) == []
