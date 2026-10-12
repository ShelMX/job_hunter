# Job Hunter

Локальный ассистент поиска работы: сбор вакансий из подключаемых источников, история наблюдений, анализ и ранжирование под ваш профиль. Данные и LLM — локально (Ollama / LM Studio).

Архитектура и роадмап: [PLAN.md](PLAN.md). Команды для агентов: [AGENTS.md](AGENTS.md).

## Быстрый старт

```bash
uv sync                       # установка зависимостей (Python 3.13)
uv run job-hunter init        # создать БД и применить миграции
uv run job-hunter sources list                    # источники и их статус
uv run job-hunter fetch -q "python" --limit 50    # собрать вакансии
uv run job-hunter vacancies list --limit 20       # посмотреть собранное
uv run job-hunter vacancies show 1                # карточка вакансии
```

## Конфигурация

- `config/settings.yaml` — настройки приложения (БД, HTTP; переопределяются env `JOB_HUNTER_*`).
- `config/connectors.yaml` — источники: `enabled`, параметры. Изменение не требует пересборки.
- hh.ru: зарегистрируйте приложение на https://dev.hh.ru, скопируйте `.env.example` → `.env`
  и вставьте `JOB_HUNTER_HH_CLIENT_ID` / `JOB_HUNTER_HH_CLIENT_SECRET`,
  затем включите коннектор в `connectors.yaml`.

## Качество кода

```bash
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src
uv run pytest
```
