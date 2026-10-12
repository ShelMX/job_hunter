# Тест-стратегия Job Hunter (коротко)

## Уровни
| Уровень | Что покрывает | Инструменты | Где |
|---|---|---|---|
| Unit | маппинг коннекторов, парсеры LLM-ответов, скоринг, дедуп, аналитика | pytest, respx-моки | `tests/test_*.py` |
| Интеграция | ingest↔SQLite, кэш LLM, воронка/статусы, миграции (create_all) | pytest, in-memory/file SQLite | те же |
| Web | маршруты, действия, фильтрация, XSS-эскейпинг | FastAPI TestClient | `tests/test_web.py` |
| Реестр требований | схема, ID, трассы, «done ⇒ тесты+CHANGELOG» | pytest | `tests/test_requirements_registry.py` |
| Живые источники/LLM | ручной live-прогон перед релизом (не в CI) | CLI + скрипты | вне pytest |

## Гейты (обязательны перед коммитом/тегом)
```bash
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src            # strict
uv run pytest              # всё зелёное
```

## Definition of Ready / Done
См. `.opencode/skills/spec/SKILL.md` — единый источник правил процесса.

## Осознанные пробелы (не баги)
- Живые сети и локальные LLM не тестируются автоматически (флаки/ресурсы): покрываем respx-моками по реальным образцам ответов; live-проверка — ритуал релиза.
- Планировщик не запускает боевые задачи в тестах: проверяется создание job'ов и изоляция.
- UI — без браузерных тестов до v1.0 (server-rendered + TestClient достаточно; пересмотрим при живых жалобах).
