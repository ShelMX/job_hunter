# 🎯 Job Hunter

**Локальный, приватный ассистент поиска работы.** Собирает вакансии из подключаемых источников, ведёт историю наблюдений (перепосты, «вечные» вакансии), оценивает условия и работодателя, сопоставляет с вашим профилем и выдаёт **ранжированный список с объяснимыми баллами**. Анализ — на локальных LLM (Ollama / LM Studio) со строгой ресурсной дисциплиной. Ничего не уходит наружу.

![Python](https://img.shields.io/badge/python-3.13-blue) ![tests](https://img.shields.io/badge/tests-60%20passed-brightgreen) ![mypy](https://img.shields.io/badge/mypy-strict-brightgreen) ![ruff](https://img.shields.io/badge/lint-ruff-blueviolet)

## Возможности

- **Источники-плагины без пересборки**: Habr Career (JSON API), hh (легальный RSS; полный API — по OAuth-ключам), Trudvsem «Работа в России» (официальный открытый API), SuperJob (бесплатный ключ), универсальный RSS-коннектор (новый фид = 5 строк YAML). Реестр и статусы: [docs/SOURCES.md](docs/SOURCES.md).
- **История наблюдений**: `first_seen/last_seen/times_seen` → перепосты, «вечные» вакансии, пулы-«зонтики» (кластеры rapidfuzz).
- **Explainable rules-скоринг**: навыки, зарплата против ожиданий, свежесть, формат, грейд, локация + штрафы (красные флаги, перепосты, «вечная», без вилки, зонтик). Веса — `config/scoring.yaml`.
- **LLM-слой (только локальные)**:
  - **триаж** decision-моделями (`tev1:4b` через `/v1/systemone`, ~1 с/решение, вероятность соответствия), fallback qwen3:4b;
  - **экстракция** структуры из описания и **вердикт** fit 0..5 с объяснением (Pydantic-JSON),
  - **импорт резюме** (md/txt/pdf/docx) → профиль;
  - кэш LLM-ответов в БД, повторно модель не дёргается.
- **Ресурсная дисциплина LLM** ([PLAN §7.1](PLAN.md)): ≤1 загруженной модели, батч-резидентность, выгрузка после батча (`keep_alive=0` / `lms unload`), выбор по свободной RAM с деградацией к меньшей, чужие модели не трогаем.
- **Web UI (localhost)**: утренний дайджест → быстрые отметки → воронка откликов; карточка с продольной аналитикой и он-деманд LLM-анализом; скрытие работодателей; кластеры; чтение md-отчётов.
- **Планировщик**: фоновый сбор по пресетам профиля (09:00/18:00) + триаж свежих — внутри web-процесса.
- **CLI**: `fetch / report / clusters / profile (init|show|import) / llm / web`, отчёты — Markdown в `data/reports/`.

## Быстрый старт

```bash
uv sync
uv run job-hunter init            # БД + миграции
cp config/profile.example.yaml config/profile.yaml   # или: job-hunter profile init | profile import data/resume.pdf
uv run job-hunter fetch           # сбор по пресетам профиля
uv run job-hunter report          # ранжированный отчёт (rules + LLM)
uv run job-hunter web             # UI: http://127.0.0.1:8777
```

Ключи источников (опционально): hh.ru → `.env` `JOB_HUNTER_HH_CLIENT_ID/SECRET` (dev.hh.ru); SuperJob → `JOB_HUNTER_SJ_SECRET_KEY` (api.superjob.ru/register, бесплатно, без модерации). Секреты — только в `.env` (в `.gitignore`); резюме и профиль (`data/`, `config/profile.yaml`) — в репозиторий не попадают.

## Документация

| Файл | Что внутри |
|---|---|
| [PLAN.md](PLAN.md) | архитектура (Clean Architecture, плагины), роадмап M0–M4, решения |
| [docs/SOURCES.md](docs/SOURCES.md) | живой реестр источников (статусы + даты проверок) |
| [docs/UI_SCENARIOS.md](docs/UI_SCENARIOS.md) | сценарии UI, модель воронки, скоуп M3 |
| [AGENTS.md](AGENTS.md) | команды разработки, конвенции, правила для агентов |

## Качество

```bash
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src        # strict
uv run pytest          # 60 тестов
```

## Changelog

### 2026-10-12 — M3: продуктовый UI и эксплуатация
- **Что:** web UI (FastAPI+Jinja, localhost): дайджест, вакансии с фильтрами, карточка с аналитикой (возраст/перепосты/кластер/работодатель), воронка откликов (интересно→отклик→ответ/интервью→оффер/отказ), заметки, скрытие работодателей, страница кластеров смежных ролей, чтение md-отчётов; фоновой планировщик (fetch+триаж 02 раза в день); on-demand LLM-анализ карточки; миграция 0003 (vacancy_states, hidden_employers).
- **Почему:** интервью владельца показало главный сценарий «утренний дайджест → отметки → фиксация ручных откликов»; аналоги (Huntr/Teal/JobOps/jobsync/JobHunt/First2Apply) дали каноничный паттерн Inbox→Действие→Воронка.
- **Для чего:** ежедневный обзор за один заход за 5–10 минут без CLI.
- Репозиторий: добавлен README с changelog, `.gitignore` для персональных данных (резюме `data/`, профиль), `profile.example.yaml`.

### 2026-10-12 — M2: локальный LLM-слой с ресурсной дисциплиной
- **Что:** порт `LLMClient` (+`LLMCache`), адаптеры Ollama (native `/api/*`, `/v1/systemone` для decision-моделей tev1/clef-flash) и LM Studio; роли `triage/extract/score` с тирами по свободной RAM; кэш (миграция 0002); LLM-экстрактор и скорер c Pydantic-JSON; импорт резюме; `--no-llm`; fail-open при сбоях; выгрузка после батча; чужие модели не трогаем.
- **Почему:** владелец потребовал разумного использования ресурсов MacBook (выгрузка после работы, без захвата всей памяти).
- **Для чего:** семантический отсев шума и объяснимые вердикты без затрат и утечек наружу.

### 2026-10-12 — M1: аналитика и ранжирование без LLM
- **Что:** дедуп/перепосты/«вечные» (rapidfuzz, MIT), hard-фильтры, skills match, взвешенные сигналы с причинами, отчёт Markdown, профиль визардом, универсальный RSS-коннектор (feedparser, BSD) + hh_rss; SuperJob-код с бесплатным ключом.
- **Почему:** hh закрыл анонимный поиск (апрель 2026) → легальный RSS; решение — без ключей.
- **Для чего:** ранжированный список «от интересных к наименее» уже без LLM.

### 2026-10-12 — M0: каркас
- **Что:** Clean Architecture (domain/application/infrastructure/connectors/presentation), SQLite+Alembic, плагинный реестр источников (builtins + entry points), CLI (Typer), тесты/линт/mypy strict, политика доступа агента (`opencode.jsonc`: `.env`/внешние пути — deny), SecretStr для секретов.
- **Почему/для чего:** фундамент под расширяемость без пересборки и приватность по умолчанию.
