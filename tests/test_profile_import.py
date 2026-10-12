from job_hunter.application.profile_import import (
    ProfileExtraction,
    merge_profiles,
    read_resume_text,
)
from job_hunter.domain.profile import CandidateProfile


def test_read_resume_text_md(tmp_path) -> None:
    path = tmp_path / "resume.md"
    path.write_text("# Иван\nBackend, python", encoding="utf-8")
    assert read_resume_text(path) == "# Иван\nBackend, python"


def test_read_resume_unknown_ext(tmp_path) -> None:
    path = tmp_path / "resume.exe"
    path.write_text("x", encoding="utf-8")
    try:
        read_resume_text(path)
        raise AssertionError("ожидали ValueError")
    except ValueError:
        pass


def test_merge_preserves_manual_fields() -> None:
    existing = CandidateProfile(
        skills=("java",),
        skill_aliases={"java": ("jvm",)},
        deal_breakers=("php",),
        salary_min=100_000,
    )
    extracted = ProfileExtraction(
        skills=["python", "fastapi"], desired_roles=["backend"], salary_min=0 or None
    )
    merged = merge_profiles(existing, extracted)
    assert merged.skills == ("python", "fastapi")  # извлечённое перекрывает
    assert merged.skill_aliases == {"java": ("jvm",)}  # ручное сохранено
    assert merged.deal_breakers == ("php",)
    assert merged.salary_min == 100_000  # null в извлечении не затирает ручное


def test_merge_into_empty() -> None:
    merged = merge_profiles(None, ProfileExtraction(skills=["go"], salary_min=150000))
    assert merged.skills == ("go",) and merged.salary_min == 150000
