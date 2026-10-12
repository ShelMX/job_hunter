# ruff: noqa: E501
# Однострочные JSON-фикстуры здесь читаемее.
from job_hunter.application.llm_extract import parse_extraction
from job_hunter.application.llm_score import parse_verdict
from job_hunter.application.profile_import import parse_profile


def test_parse_extraction_plain_json() -> None:
    raw = '{"skills": ["python", "fastapi"], "is_umbrella": false, "red_flags": [], "conditions": "ок", "employer_notes": ""}'
    ext = parse_extraction(raw)
    assert ext is not None
    assert ext.skills == ["python", "fastapi"]


def test_parse_extraction_fenced_and_think() -> None:
    raw = '<think>тут рассуждение</think>```json\n{"skills": ["sql"], "is_umbrella": true, "red_flags": ["дежурства"], "conditions": "", "employer_notes": ""}\n```'
    ext = parse_extraction(raw)
    assert ext is not None and ext.is_umbrella and ext.skills == ["sql"]


def test_parse_extraction_garbage_returns_none() -> None:
    assert parse_extraction("не json вообще") is None
    assert parse_extraction('{"skills": "не список"}') is None


def test_parse_verdict_bounds() -> None:
    assert parse_verdict('{"fit": 4, "verdict": "ок", "risks": ""}').fit == 4  # type: ignore[union-attr]
    assert parse_verdict('{"fit": 9, "verdict": "x"}') is None  # fit вне 0..5
    assert parse_verdict("…") is None


def test_parse_profile() -> None:
    raw = '{"desired_roles": ["backend"], "skills": ["python"], "salary_min": 300000, "search_queries": ["python"]}'
    p = parse_profile(raw)
    assert p is not None and p.salary_min == 300000 and p.skills == ["python"]
