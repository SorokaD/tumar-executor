# База данных OKX HFT Executor

Документация по хранению данных исполнителя: зачем две СУБД, какие таблицы, как применять миграции и как читать аналитику.

**Аудитория:** разработчик, который через полгода разбирается в проекте без устных пояснений.

## Навигация

| Документ | Содержание |
|----------|------------|
| [overview.md](overview.md) | Две СУБД (SQLite + PostgreSQL), роли, план dual-write, идентификаторы |
| [sqlite_mvp.md](sqlite_mvp.md) | Текущий локальный журнал MVP (что пишет код **сейчас**) |
| [okx_exec_schema.md](okx_exec_schema.md) | Полный справочник схемы `okx_exec` в PostgreSQL/TimescaleDB |
| [data_flow.md](data_flow.md) | Как события executor попадают в таблицы (жизненный цикл записи) |
| [operations.md](operations.md) | Миграции, `DATABASE_URL`, сброс, бэкап, типовые ошибки |
| [analytics_queries.md](analytics_queries.md) | Готовые SQL: fill rate, PnL, fees, daily summary, сравнение стратегий |
| [../baseline_measurement.md](../baseline_measurement.md) | Measurement baseline: net PnL, fees, execution metrics |

## DDL в репозитории

```text
migrations/postgres/
  000_reset_okx_exec.sql      # DROP SCHEMA (dev only)
  001_okx_exec_schema.sql     # таблицы
  002_hypertables_indexes.sql # Timescale + индексы
  002_indexes_only.sql        # без Timescale
  003_triggers.sql
  004_align_legacy_schema.sql   # патч старых таблиц (ручная схема)
  005_add_execution_metrics.sql # trade_results: fees, execution metrics
  006_trade_daily_summary_view.sql
  apply_all.sh
  README.md
```

Скрипт без `psql`: `python scripts/apply_pg_migrations.py` (применяет 001, 002, 003, 005, 006).

## Статус интеграции

| Слой | СУБД | Статус |
|------|------|--------|
| Операционный журнал + control-api | SQLite (`OKX_SQLITE_PATH`) | **Работает** |
| Аналитика / DWH | PostgreSQL `okx_exec.*` | **Dual-write работает** (`PostgresJournal`) |
| Measurement baseline | `trade_results` + view | **Работает** (миграции 005/006) |

Связанные документы:

- [trade_lifecycle.md](../trade_lifecycle.md) — логическая цепочка signal → order → fill → PnL
- [reconciliation.md](../reconciliation.md) — сверка с биржей
- [persistence/README.md](../../persistence/README.md) — пакет persistence в коде
