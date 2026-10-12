"""Профиль кандидата: файловое хранение config/profile.yaml ↔ domain.CandidateProfile."""

from pathlib import Path
from typing import Any

import yaml

from job_hunter.domain.profile import CandidateProfile

PROFILE_FILENAME = "profile.yaml"

_TUPLE_FIELDS = frozenset(
    {
        "desired_roles",
        "skills",
        "seniorities",
        "locations",
        "remote_modes",
        "deal_breakers",
        "search_queries",
    }
)


def profile_path(config_dir: Path) -> Path:
    return config_dir / PROFILE_FILENAME


def load_profile(config_dir: Path) -> CandidateProfile | None:
    path = profile_path(config_dir)
    if not path.exists():
        return None
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ожидается mapping, получено {type(raw).__name__}")

    data: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _TUPLE_FIELDS and isinstance(value, list):
            data[key] = tuple(str(v) for v in value)
        elif key == "skill_aliases" and isinstance(value, dict):
            data[key] = {str(k): tuple(str(x) for x in v) for k, v in value.items()}
        elif key == "salary_min":
            data[key] = int(value) if value is not None else None
        else:
            data[key] = value
    return CandidateProfile(**data)


def _to_plain(value: Any) -> Any:  # noqa: ANN401 — рекурсивная нормализация
    if isinstance(value, tuple):
        return [_to_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    return value


def save_profile(config_dir: Path, profile: CandidateProfile) -> Path:
    import dataclasses

    path = profile_path(config_dir)
    plain = _to_plain(dataclasses.asdict(profile))
    header = "# Профиль кандидата Job Hunter. Правьте вручную или через `job-hunter profile init`.\n"
    path.write_text(header + yaml.safe_dump(plain, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path
