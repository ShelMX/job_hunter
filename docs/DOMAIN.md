# Домен и модель данных Job Hunter (DDD)

> Дата: 2026-10-13. Источник правды по **вездесущему языку, контекстам, агрегатам, инвариантам и событиям**.
> Связи: `PLAN.md` §3, `docs/requirements.yaml`, `docs/adr/0006-hot-cold-liveness.md`, `.opencode/skills/spec/SKILL.md`.
> Ленивое DDD: поведение и инварианты вводим **вместе с касающимися их требованиями**, а не рефакторингом «на будущее».

## 0. Вездесущий язык (ubiquitous language)

| Термин | Значение (и анти-значение) |
|---|---|
| **Vacancy** | нормализованное объявление у источника. НЕ равно «позиция» (одна позиция = много Vacancy при перепостах/кросс-источниках) |
| **VacancyIdentity** | `(source, external_id)` — жёсткая идентичность записи |
| **Observation** | факт встречи Vacancy у источника: `seen_at`; агрегируется в `first_seen/last_seen/times_seen` |
| **Episode** *(план)* | непрерывный период «вакансия видна у источника»; нужен для «10-й раз за 3 месяца» |
| **Liveness** | жизнь Vacancy у источника: `active / stale / gone`. НЕ статус отклика |
| **Funnel / Application** | личная воронка владельца: `new → interested → applied → responded → interview → offer / rejected / archived` |
| **Cluster** | группа Vacancy, считающихся одной позицией (soft identity): перепосты/кросс-источники |
| **Canonical vacancy** | представитель кластера (свежайшая/лучшая по правилам) |
| **Source / Connector** | внешняя площадка / адаптер её к домену (anti-corruption layer) |
| **Watchlist company** | целевая компания владельца для частого опроса (по типу доски/работодателю) |
| **Triage** | LLM-решение «показывать/нет» (decision-модель, вероятность, fail-open) |
| **Extraction** | структурирование текста вакансии (навыки, зонтик, red flags, условия, заметки о работодателе) |
| **Assessment / Signal** | оценка Vacancy для владельца: сумма **сигналов** с причинами |
| **Verdict** | LLM-вердикт соответствия профилю: fit 0..5 + объяснение + риски |
| **Profile** | профиль владельца (файл, не БД): роли, навыки, ожидания, deal-breakers, пресеты |
| **Employer** | работодатель и его производные сигналы (частота публикаций, доля вечных/перепостов) |

## 1. Bounded contexts

| Контекст | Тип | Ответственность | Ключевые агрегаты |
|---|---|---|---|
| **Ingestion** | supporting | подключение источников, сбор, нормализация, наблюдения | `Source`/`Connector` (адаптеры) |
| **Catalog & History** | **core** | реестр Vacancy, идентичность, liveness, история наблюдений | `Vacancy` (+наблюдения), `Employer` |
| **Clustering** | supporting | soft identity: перепосты/кросс-источники | `Cluster` (производная модель) |
| **Analysis** | **core** | rules-скоринг, триаж/экстракция/вердикт (LLM), объяснимость | `Assessment`, `Signal`, `Verdict`, `Extraction` |
| **Matching** | **core** | сопоставление Profile × Vacancy | `Profile` (VO-набор), `Assessment` |
| **Funnel** | supporting | воронка владельца, заметки, дата отклика | `Application` |
| **Watchlist** | supporting | целевые компании, частый опрос | `WatchlistCompany` |
| **Delivery (UI/Reports)** | generic | дайджест, списки, карточки, md-отчёты, планировщик | read-models |

**Ядро (core domain)** = Catalog&History + Analysis + Matching — здесь наша уникальность (объяснимые оценки, живость, история). Остальное — поддержка/генерика; инвестиции в них минимальны.

## 2. Агрегаты, инварианты, места принуждения

| Агрегат (root) | Сущности/VO внутри | Инварианты | Где принуждается |
|---|---|---|---|
| **Vacancy** | `VacancyIdentity`, `Salary`, `RemoteMode`, `EmploymentType`, `ContentHash`, наблюдения | 1) `(source, external_id)` уникален; 2) `last_seen ≥ first_seen`; 3) `times_seen ≥ 1`; 4) повторная встреча = **обновление той же записи**, не вставка; 5) смена контента фиксируется `content_hash` | DB constraint (UNIQUE) + `SqlVacancyRepository.upsert` + тесты `test_ingest`, `test_state_repo` |
| **Employer** | нормализованное имя/ИНН, производные статистики | 1) статистика считается только по своим Vacancy; 2) агрегаты переживают удаление сырья (retention) | `application/employers.py` + тесты `test_employers` |
| **Cluster** (производный) | участники, canonical, сходство | 1) ≥2 участника; 2) canonical ∈ участники; 3) порог сходства из `scoring.yaml` | `application/dedup.py` + тесты `test_dedup` |
| **Application** (funnel) | статус, заметка, `applied_at` | 1) `applied_at` ставится только при `applied`; 2) переходы по разрешённому графу; 3) funnel ≠ liveness; 4) архив/отказ — терминальны для активной доски | `vacancy_states` + `SqlStateRepository` + UI-действия (граф переходов усиливаем при касании FR по воронке) |
| **Assessment** (read-model) | `Signal[]`, total | 1) total = clamp(Σ сигналов); 2) каждый балл имеет причину (объяснимость) | `application/analyze.py` + `test_analyze` |
| **WatchlistCompany** *(план, FR-001)* | тип источника, id, интервал | 1) уникальность (type, external_id); 2) опрос ≤ лимита источника | будущая `watchlist_companies` + тесты FR-001 |
| **Profile** | VO: роли, навыки(+алиасы), ожидания, deal-breakers | 1) файл — вне БД; 2) алиасы не дублируют канонические имена; 3) salary_min — int ≥0 | `infrastructure/profile.py` + `test_profile` |

## 3. Value objects (неизменяемые, равны по значению)

`VacancyIdentity(source, external_id)` · `Salary(min, max, currency, gross)` · `RemoteMode` · `EmploymentType` · `ContentHash` · `Similarity(0..100)` · `Score(total, signals)` · `Signal(name, points, reason)` · `Money/Currency` · `PostingLag(days)` · `Liveness(active|stale|gone)` · `FunnelStatus` · `SourceRef(name, url)`.
Правило: VO создаются/валидируются на границе (коннектор/LLM-адаптер → домен), не «где-то по пути».

## 4. Доменные события (для аналитики/автоматизации; сейчас — лог/кэш, дальше — подписки)

| Событие | Когда | Потребитель сегодня | Потребитель в будущем |
|---|---|---|---|
| `VacancyDiscovered` | первая встреча | отчёты/дайджест | алерты (watchlist 🔥) |
| `VacancySeenAgain` | повторная встреча | `times_seen` | живость, «вечные» |
| `VacancyContentChanged` | изменился `content_hash` | отметка в БД | пересчёт Assessment |
| `VacancyWentStale` / `VacancyGone` | по порогам liveness *(план FR-009)* | — | вывод из горячего, аналитика |
| `ClusterFormed` | возник новый кластер | штраф `repost` | «пулы» в UI |
| `TriageDecided` | decision-модель ответила | кэш/фильтр дайджеста | калибровка порога |
| `AssessmentProduced` | rules/LLM-оценка | отчёт | обучение весов |
| `ApplicationStatusChanged` | смена статуса воронки | UI/воронка | метрики конверсий (v0.5) |
| `EmployerHidden` | скрытие работодателя | фильтры выдач | отчёты |

## 5. Политики (правила, не привязанные к одной сущности)

1. **Идентичность**: жёсткая `(source, external_id)`; фолбэки — хеш каноничного URL → `channel:message_id` → хеш контента (помечать как нестабильный).
2. **Дедуп/перепост**: UPDATE при том же id; кластер при том же работодателе + сходстве ≥ `repost_similarity`; canonical = свежайшая.
3. **Liveness**: `stale`/`gone` по порогам от `last_seen`. **Зафиксированные владельцем дефолты (2026-10-13):** `stale` = 7 дней, `gone` = 21 день; retention сырья `gone` = 12 месяцев (агрегаты — бессрочно); порог catch-up-сбора = 3 часа.
4. **Горячий/холодный**: горячие выдачи фильтруют liveness; аналитика читает всё; retention сырья + бессрочные агрегаты.
5. **Триаж**: decision-модель, `fail-open`, порог вероятности; результат кэшируется; «подходящее» = прошло триаж/скоринг.
6. **Ресурсная дисциплина LLM**: ≤1 модель, батчи, явная выгрузка, деградация по RAM (§7.1).
7. **Приватность**: профиль/резюме вне БД и вне репозитория; исходящие запросы — только белые источники и локальные LLM (инвариант NFR-007).

## 6. Карта контекстов (взаимодействия)

```
Ingestion ──(RawVacancy → anti-corruption)──▶ Catalog&History ──▶ Clustering
                                                   │
                                                   ▼
                              Analysis ◀── Profile (Matching) ──▶ Assessment
                                                   │
                                                   ▼
                             Delivery (UI/Reports) ◀── Funnel ◀── Watchlist
```
- Ingestion **конформист** к домену: коннектор переводит источник → `Vacancy`.
- Analysis — поставщик `Assessment` для Delivery; Funnel независим (не влияет на liveness).
- Delivery — read-model: не содержит доменной логики (кроме явных политик фильтрации).

## 7. Сценарий «слесарь-аналитик» (иллюстрация агрегата Vacancy)

| День | Факт | Домен |
|---|---|---|
| Д1 | пришла (hh, id=123) | `VacancyDiscovered` → 1 строка, first=last=Д1, times=1 |
| Д2 | та же | `VacancySeenAgain` → та же строка, last=Д2, times=2 |
| Д3 | исчезла | ничего (нет события «gone» до порога) — строка живёт |
| Д10 | нет появлений > 7 дн | `VacancyWentStale` (liveness=stale) |
| Д25 | нет появлений > 21 дн | `VacancyGone` → вне горячего, в аналитике |
| Д40 | перевыложили (новый id) | `VacancyDiscovered` (новая строка) + `ClusterFormed` (штраф repost) |

## 8. Миграционная готовность к физическому разделению (требование владельца → NFR-009)

Даже при единой БД сейчас проектируем так, чтобы вынос холодного сырья был **миграцией, а не перепиской**:
1. `liveness` — первоклассное поле с индексом (не вычисляемое ad-hoc в каждом запросе без опоры на колонку).
2. **Единственная точка доступа** — репозитории-порты (`VacancyRepository`); никакие модули не лезут в SQL напрямую.
3. Горячие запросы не делают предположений о «физическом соседстве» холодного: только фильтр по liveness.
4. Задокументированный план выноса: `vacancies → vacancies_archive` (или отдельный файл) по `liveness=gone` + retention; агрегаты остаются; read-совместимость через представление/репозиторий.
5. Никаких кросс-сущностных FK на «сырьё» из горячих агрегатов (Employer-статистика — производная, не FK).

## 9. Соответствие коду (на дату) и что вводим лениво

- Работает сейчас: идентичность/upsert/наблюдения, кластеры, Assessment/Signal, Profile, Funnel-статусы, приватность.
- Вводим с требованиями: `Liveness` + события stale/gone + retention (FR-009/FR-011/NFR-008/NFR-009), `WatchlistCompany` (FR-001), де-LLM-политика текстов (FR-004), `Application`-граф переходов (при касании воронки), Episodes (когда появится боль аналитики «10-й раз»).
