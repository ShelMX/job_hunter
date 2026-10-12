# 🎯 Job Hunter

**Сервис разумного поиска работы, живущий на вашем MacBook.** Собирает вакансии из подключаемых «белых» источников, ведёт историю наблюдений (перепосты, «вечные», пулы-зонтики), оценивает условия и работодателя, сопоставляет с вашим профилем и выдаёт ранжированный список **с объяснимыми баллами**. Анализ — на локальных LLM (Ollama/LM Studio) с жёсткой ресурсной дисциплиной. Приватность: ничего не уходит наружу; резюме и профиль — вне репозитория.

![Python](https://img.shields.io/badge/python-3.13-blue) ![release](https://img.shields.io/badge/release-v0.3.0-blue) ![tests](https://img.shields.io/badge/tests-60%20passed-brightgreen) ![mypy](https://img.shields.io/badge/mypy-strict-brightgreen) ![lint-ruff](https://img.shields.io/badge/lint-ruff-blueviolet)

## Проблема → Решение

Площадки 2026 года шумят: «вечные» вакансии, перепосты одной позиции пулом, бот-фильтры, закрывшиеся API (hh). Кандидат теряет время на фильтрацию и теряет follow-up'ы. Job Hunter превращает это в утренний ритуал на 5–10 минут: **дайджест ранжирован → отметил интересные → откликнулся вручную → зафиксировал в воронке**.

Пример объяснимости балла (из реального отчёта):

```
### 1. Fullstack-аналитика по Битрикс24 — Top Selection (53.0)
- +20.0 — вилка от 270 000 покрывает ожидание 250 000
- +10.0 — грейд Senior — совпадение
- +3.0  — LLM 3/5 — соответствие по грейду и BA, но Битрикс24 не указан в резюме — ключевой риск
- −0.0  — формат remote не из списка (hybrid)
```

## Возможности

- **Источники-плагины без пересборки** — включение источника = запись в `config/connectors.yaml`:
  Habr Career (JSON API) · hh (легальный RSS; полный API по OAuth-ключам) · Trudvsem «Работа в России» (официальный открытый API Роструда) · SuperJob (бесплатный ключ) · универсальный RSS (`type: rss` + 5 строк YAML на новый фид). Реестр и статусы: [docs/SOURCES.md](docs/SOURCES.md).
- **История наблюдений** → перепосты, «вечные» вакансии, кластеры смежных ролей (rapidfuzz).
- **Explainable rules-скоринг**: навыки/зарплата против ожиданий/свежесть/формат/грейд/локация + штрафы (красные флаги, перепост, «вечная», нет вилки, зонтик). Веса — `config/scoring.yaml`.
- **Локальный LLM-слой**: триаж decision-моделями (`tev1:4b` через `/v1/systemone` — ~1 с/решение с вероятностью), экстракция структуры и вердикт fit 0..5 с объяснением (Pydantic-JSON), импорт резюме md/txt/pdf/docx → профиль, кэш ответов в SQLite.
- **Ресурсная дисциплина LLM** ([PLAN §7.1](PLAN.md)): ≤1 загруженной модели, выгрузка после батча, выбор по свободной RAM с деградацией, чужие модели не трогаем.
- **Web UI (localhost)**: дайджест → быстрые отметки (✨/📨/🙈) → **воронка** откликов; карточка с аналитикой и on-demand LLM-анализом; скрытие работодателей; кластеры; md-отчёты.
- **Фоновый планировщик** в web-процессе: сбор по пресетам 09:00/18:00 + триаж свежих.
- **CLI**: `fetch · report · clusters · profile (init|show|import) · llm · web`; отчёты Markdown в `data/reports/`.

## Архитектура (Clean Architecture)

```
connectors/ ──▶ application/ (ingest · dedup · analyze · score · report · digest · pipeline)
     ▲ ports: SourceConnector, VacancyRepository, LLMClient, LLMCache, StateRepository
domain/ (чистые dataclass-модели, порты-протоколы)
     ▲ implements
infrastructure/ (SQLAlchemy+Alembic (SQLite) · httpx+tenacity · Ollama/LM Studio · APScheduler)
     ▲
presentation/ (CLI: Typer · Web: FastAPI+Jinja на 127.0.0.1)
```

Новый источник = модуль в `connectors/` (или 5 строк YAML для RSS) — ядро не трогаем. Новый скорер/анализатор — реализация порта в `application/`.

## Быстрый старт

```bash
uv sync
uv run job-hunter init                     # БД + миграции
cp config/profile.example.yaml config/profile.yaml   # или job-hunter profile init | profile import data/resume.pdf
uv run job-hunter fetch                    # сбор по пресетам профиля
uv run job-hunter report                   # ранжированный отчёт (rules + LLM)
uv run job-hunter web                      # UI: http://127.0.0.1:8777
```

Ключи источников (опционально, бесплатно): hh — `.env` `JOB_HUNTER_HH_CLIENT_ID/SECRET` (заявка на dev.hh.ru); SuperJob — `JOB_HUNTER_SJ_SECRET_KEY` (api.superjob.ru/register, без модерации).

## Приватность (архитектурное требование, не настройка)

- секреты: `.env` (gitignore) + `SecretStr` + deny-правила для агента (`opencode.jsonc`);
- резюме/БД/отчёты: `data/` в gitignore; документы `data/*.{pdf,docx,doc}` закрыты от агента даже на чтение;
- профиль `config/profile.yaml` — вне репозитория (в индексе лежит обезличенный `profile.example.yaml`);
- LLM — только локальные; сеть — только к источникам из `connectors.yaml`.

## Качество и процесс

```bash
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src            # strict
uv run pytest              # 60 тестов
```

Коммитим только зелёное; версии — SemVer на аннотированных тегах (`v0.3.0` ← этап M3). История изменений: [CHANGELOG.md](CHANGELOG.md).

## Документация

| Файл | Содержание |
|---|---|
| [docs/PRODUCT.md](docs/PRODUCT.md) | зачем сервис: проблема, JTBD, метрики успеха, **реестр ограничений C1–C7**, риски, релизная политика |
| [PLAN.md](PLAN.md) | архитектура, роадмап (этапы M0–M4), журнал решений с датами |
| [docs/SOURCES.md](docs/SOURCES.md) | живой реестр источников: статусы + даты эмпирических проверок |
| [docs/UI_SCENARIOS.md](docs/UI_SCENARIOS.md) | сценарии UI (исследование аналогов → интервью → IA → модель воронки) |
| [AGENTS.md](AGENTS.md) | команды и конвенции для разработчиков/агентов |

## Роадмап

| Релиз | Этап | Статус |
|---|---|---|
| v0.1.0 | M0 Каркас (слои, БД, плагины, CLI) | ✅ 2026-10-12 |
| v0.2.0 | M1 rules-аналитика + M2 локальный LLM | ✅ 2026-10-12 |
| v0.3.0 | M3 Web UI, воронка, планировщик, кластеры | ✅ 2026-10-12 |
| v0.4.0 | M4: hh OAuth, новые источники (Adzuna/SuperJob→done…), Greenhouse/Lever-наблюдение, Telegram-каналы (white) | впереди |
| v1.0.0 | зрелость: неделя ежедневного использования без критичных правок | цель |

Лицензия — Proprietary (личный портфолио-проект; можно поменять на MIT по решению владельца).
