# Обзор хранения данных

## Зачем две базы

```mermaid
flowchart TB
  subgraph executor [Executor process]
    OR[orchestrator]
    SM[strategy_manager]
    CA[control-api]
  end
  subgraph sqlite [SQLite на VPS / локально]
    SQ[(baseline_mvp.sqlite3)]
  end
  subgraph pg [PostgreSQL / TimescaleDB]
    PG[(okx_exec schema)]
  end
  OR --> SQ
  SM --> SQ
  CA --> SQ
  OR -.->|dual-write| PG
```

| | SQLite | PostgreSQL `okx_exec` |
|--|--------|------------------------|
| **Где** | Файл `data/baseline_mvp.sqlite3` (Docker: `/app/data/`) | Сервер TimescaleDB (отдельный хост) |
| **Зачем** | Быстрый ops-журнал, очередь команд control-api, smoke-run | Полная аналитика, сравнение стратегий, аудит |
| **Объём** | Малый, один файл на VPS | Растёт со временем, hypertables |
| **Кто пишет** | `ExecutorStore` → `SqliteMvpStore` | `ExecutorStore` → `PostgresJournal` (фоновая очередь) |
| **Кто читает** | control-api, ручная диагностика на VPS | SQL, `trade_daily_summary.py`, Superset/Grafana |

SQLite **не заменяется**: control-api и strategy manager завязаны на локальный файл. PostgreSQL — **источник правды для аналитики** (dual-write из `ExecutorStore`).

Подробнее про measurement baseline: [baseline_measurement.md](../baseline_measurement.md).

## Схема PostgreSQL

- Имя схемы: **`okx_exec`**
- Имя БД (пример): **`okx_hft`**
- Расширение: **TimescaleDB** (hypertables по времени для высокочастотных таблиц)

Полный справочник таблиц: [okx_exec_schema.md](okx_exec_schema.md).

## Идентификаторы (важно для JOIN)

Executor генерирует строковые id с префиксом (`services/id_generation.py`):

| Префикс | Пример | Сущность |
|---------|--------|----------|
| `rb-` | `rb-a1b2c3…` | signal (random baseline) |
| `exit-` / `exit-mkt-` | `exit-…` | exit order client id |
| `pos-` | `pos-…` | position (открыта executor) |
| `pos-ex-` | `pos-ex-{okx_pos_id}` | position (восстановлена с биржи) |

В PostgreSQL поля `signal_id`, `position_id`, `order_id_local`, `trade_id` — тип **`TEXT`**, не UUID.

Отдельно:

- `run_id` — `BIGINT`, суррогатный ключ запуска процесса
- `run_uuid`, `attempt_uuid`, `event_uuid` — UUID для глобальной уникальности

## Группировка по стратегии

Каждая торговая таблица содержит как минимум:

- `strategy_name` — например `random_baseline_v1`, `mean_reversion_v1`
- `inst_id` — например `BTC-USDT-SWAP`
- `run_id` — привязка к конкретному запуску после рестарта/деплоя

Сравнение baseline vs модели = фильтр `WHERE strategy_name = '…'` + одинаковый `inst_id` и период.

## Принципы проектирования PG-схемы

1. **Append-only для ордеров** — reprice создаёт **новую строку** в `orders`, старая остаётся со статусом `canceled`. Не повторять ошибку SQLite `INSERT OR REPLACE`.
2. **`execution_attempts`** — каждая попытка submit/cancel/skip/API error. Ответ на вопрос «почему ордер не сработал».
3. **`service_events`** — аудит, не дублировать сюда всё из attempts.
4. **Exchange as source of truth** — расхождения в `reconciliation_events`.
5. **Hypertables** — time-series таблицы партиционируются по `ts_*` для масштабирования.

## Переменные окружения

| Переменная | Назначение |
|------------|------------|
| `OKX_SQLITE_PATH` | Путь к SQLite (см. `.env.example`) |
| `DATABASE_URL` / `POSTGRES_*` | PostgreSQL (journal, миграции) |
| `OKX_HFT_POSTGRES_ENABLED` | `1` — включить запись в PG |

Пример `DATABASE_URL`:

```text
postgresql://executor_rw:PASSWORD@HOST:5432/okx_hft
```

Секреты — только в `.env` на сервере, не в git.

## Дорожная карта persistence

1. ✅ DDL `migrations/postgres/*` (включая `005`, `006`)
2. ✅ Dual-write из orchestrator (`ExecutorStore` + `PostgresJournal`)
3. ⬜ Полные `order_fills` из OKX WS / детальный fee на каждый partial fill
4. ✅ Fees и net PnL в `trade_results` (`fee_engine`, `trade_finalize`)
5. ✅ View `v_trade_daily_summary` + CLI `scripts/trade_daily_summary.py`
6. ⬜ Materialized views / дашборды Grafana
