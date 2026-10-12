# AGENTS.md

## Проект

Job Hunter — локальный сервис поиска работы (Python 3.13, uv). План и решения — в `PLAN.md` (обновлять при изменениях!).

## Команды

| Действие | Команда |
|---|---|
| Установка | `uv sync` |
| Тесты | `uv run pytest` |
| Линт | `uv run ruff check src tests` |
| Формат | `uv run ruff format src tests` |
| Типы | `uv run mypy src` |
| CLI | `uv run job-hunter --help` |
| Миграция (новая) | `uv run alembic revision -c <через код отсутствует; использовать infrastructure/db/migrations> ` — см. `migrate.py` |

Перед коммитом: `ruff check`, `ruff format --check`, `mypy src`, `pytest` — всё зелёное.

## Документы

- `docs/PRODUCT.md` — продуктовая рамка: проблема, JTBD, метрики, реестр ограничений C1–C7, риски. **Обновлять при изменениях.**
- `PLAN.md` — план, роадмап, журнал решений (обновлять!).
- `docs/SOURCES.md` — живой реестр источников вакансий: статус + дата эмпирической проверки (curl), не по памяти.
- `CHANGELOG.md` — Keep a Changelog; любая фича/изменение → запись в Unreleased или в новый релиз.

## Релизы (SemVer, теги)

- Этапная фича с демо → minor (0.X.0); фиксы/полировка между этапами → patch (0.x.Y+1); v1.0.0 — критерий в PRODUCT.md §8.
- Перед тегом: все гейты зелёные, CHANGELOG дописан, `pyproject.version` синхронизирован.
- Публикация: `git tag -a vX.Y.Z -m "<что/почему/для чего>"` + `git push --tags`; GitHub Release — из тега (gh или веб).

## Архитектура (Clean Architecture, строго по слоям)

- `src/job_hunter/domain/` — чистые dataclass-сущности и `ports.py` (Protocol-интерфейсы). **Никаких импортов из infrastructure/presentation.**
- `src/job_hunter/application/` — use-cases; зависят только от domain.
- `src/job_hunter/infrastructure/` — адаптеры: `db/` (SQLAlchemy + Alembic), `http.py` (httpx + tenacity), `config.py` (pydantic-settings + YAML), `llm/` (M2).
- `src/job_hunter/connectors/` — плагины источников (реализуют `domain.ports.SourceConnector`). Внешние плагины — через entry point `job_hunter.connectors`.
- `src/job_hunter/presentation/` — CLI (Typer). Composition root — `src/job_hunter/bootstrap.py`.
- Конфиги: `config/settings.yaml`, `config/connectors.yaml`, `config/scoring.yaml` (M1).

## Конвенции

- Абсолютные импорты, типизация везде (mypy strict), `from __future__ import annotations` не требуется (py3.12+).
- Даты — timezone-aware (`datetime.now(UTC)`).
- Connector добавляется: новый модуль в `connectors/` + фабрика в `connectors/__init__.py` + запись в `config/connectors.yaml`. Ядро не трогаем.
- Секреты — только через `.env` (в `.gitignore`; шаблон `.env.example`) или env-переменные `JOB_HUNTER_*`. Никогда не хардкодим и не коммитим. В проекте действует `opencode.jsonc` (жёстко): любые `*.env*` закрыты для read/edit (deny, кроме чтения `.env.example`), любые действия вне каталога проекта — deny, shell — `ask` кроме белого списка (`uv run/uv sync/ls`), веб — `ask`.
- **LLM (M2+, требование владельца)**: только локальные; одновременно загружена ≤1 модели; LLM-задачи — батчами, конкурентность 1; после батча — явная выгрузка (Ollama: запрос с `keep_alive=0` нативным `/api/*`; LM Studio: auto-unload по idle + `lms unload`); перед загрузкой тяжёлой модели проверять свободную RAM и деградировать к меньшей; детали — PLAN.md §7.1.
- **Не изобретать велосипеды**: перед добавлением функциональности ищем зрелые решения (PyPI/GitHub/Хабр); берём только permissive-лицензии (MIT/BSD/Apache), живые проекты без маркеров небезопасного контента; выбор фиксируем в PLAN.md.
- Ответы пользователю — на русском.
