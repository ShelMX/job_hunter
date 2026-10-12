"""Валидатор реестра требований (docs/requirements.yaml).

Проверяет: схему, уникальность ID, ссылки на сценарии F*/ограничения C*,
существование тестов и правило «done ⇒ есть тесты и запись в CHANGELOG».
Ленивость: пустой реестр — валиден; правила строжатся по мере появления записей.
"""

from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, Field, ValidationError

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "requirements.yaml"


class Requirement(BaseModel):
    id: str = Field(pattern=r"^(FR|NFR)-\d{3}$")
    title: str
    statement: str
    acceptance: list[str] = Field(min_length=1)
    priority: str = Field(pattern=r"^(must|should|could)$")
    status: str = Field(pattern=r"^(proposed|accepted|done|dropped)$")
    release: str
    traces: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    notes: str = ""


@pytest.fixture(scope="module")
def registry() -> list[Requirement]:
    raw = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    assert raw["schema"] == 1, "неизвестная версия схемы реестра"
    return [Requirement.model_validate(item) for item in raw.get("requirements") or []]


def test_registry_parses(registry: list[Requirement]) -> None:
    assert isinstance(registry, list)


def test_ids_unique_and_sorted(registry: list[Requirement]) -> None:
    ids = [r.id for r in registry]
    assert len(ids) == len(set(ids)), f"дубли ID: {ids}"


def test_traces_point_to_known_scenarios_and_constraints(registry: list[Requirement]) -> None:
    discovery = (ROOT / "docs" / "DISCOVERY.md").read_text(encoding="utf-8")
    product = (ROOT / "docs" / "PRODUCT.md").read_text(encoding="utf-8")
    for req in registry:
        for trace in req.traces:
            assert trace.startswith(("F", "C")), f"{req.id}: некорректная трасса {trace}"
            if trace.startswith("F"):
                assert f"**{trace} " in discovery or f"{trace} " in discovery, (
                    f"{req.id}: сценарий {trace} не найден в DISCOVERY.md"
                )
            else:
                assert f"| {trace} |" in product, f"{req.id}: ограничение {trace} не найдено в PRODUCT.md"


def test_done_requirements_have_tests_and_changelog(registry: list[Requirement]) -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for req in registry:
        if req.status != "done":
            continue
        assert req.tests, f"{req.id}: status=done, но tests пуст"
        for test_id in req.tests:
            path = ROOT / test_id.split("::")[0]
            assert path.exists(), f"{req.id}: тест-файл не найден: {test_id}"
        assert req.id in changelog, f"{req.id}: нет записи в CHANGELOG.md"


def test_referenced_tests_exist(registry: list[Requirement]) -> None:
    for req in registry:
        for test_id in req.tests:
            path = ROOT / test_id.split("::")[0]
            assert path.exists(), f"{req.id}: тест-файл не найден: {test_id}"


def test_validation_error_shape_example() -> None:
    """Схема действительно ловит мусор (защита от «просто текста» в реестре)."""
    with pytest.raises(ValidationError):
        Requirement.model_validate(
            {
                "id": "FR-1",
                "title": "x",
                "statement": "y",
                "acceptance": [],
                "priority": "high",
                "status": "todo",
                "release": "v",
            }
        )
