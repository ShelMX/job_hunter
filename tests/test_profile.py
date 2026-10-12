from job_hunter.domain.profile import CandidateProfile
from job_hunter.infrastructure.profile import load_profile, save_profile


def test_profile_roundtrip(tmp_path) -> None:
    profile = CandidateProfile(
        desired_roles=("backend-разработчик",),
        skills=("python", "fastapi"),
        skill_aliases={"fastapi": ("FastAPI", "fast api")},
        seniorities=("Senior",),
        locations=("Москва",),
        remote_modes=("remote",),
        salary_min=250_000,
        deal_breakers=("php",),
        search_queries=("python backend", "fastapi"),
    )
    save_profile(tmp_path, profile)
    loaded = load_profile(tmp_path)

    assert loaded is not None
    assert loaded == profile
    assert loaded.canonical_skills()["fast api"] == "fastapi"


def test_missing_profile_returns_none(tmp_path) -> None:
    assert load_profile(tmp_path) is None
