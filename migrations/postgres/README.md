# PostgreSQL migrations — `okx_exec`

Краткая шпаргалка. **Полная документация:** [docs/database/README.md](../../docs/database/README.md).

## Быстрый старт

```bash
export DATABASE_URL='postgresql://executor_rw:PASSWORD@HOST:5432/okx_hft'
bash migrations/postgres/apply_all.sh
# или
python scripts/apply_pg_migrations.py
```

Сброс (dev): `RESET=1 bash migrations/postgres/apply_all.sh`

## Файлы

| Файл | Назначение |
|------|------------|
| `000_reset_okx_exec.sql` | DROP SCHEMA |
| `001_okx_exec_schema.sql` | 11 таблиц |
| `002_hypertables_indexes.sql` | Timescale + индексы |
| `002_indexes_only.sql` | без Timescale |
| `003_triggers.sql` | updated_at на positions |
| `004_align_legacy_schema.sql` | Патч старых таблиц (если создавали вручную до 001) |
| `005_add_execution_metrics.sql` | trade_results: fees, execution metrics, close_source |
| `006_trade_daily_summary_view.sql` | VIEW `v_trade_daily_summary` |
| `006_trade_daily_summary_view.sql` | VIEW `v_trade_daily_summary` |
| `007_russian_column_comments.sql` | Русские COMMENT ON для таблиц/колонок |

## Документация

- [Справочник таблиц](../../docs/database/okx_exec_schema.md)
- [Поток данных](../../docs/database/data_flow.md)
- [Операции и бэкап](../../docs/database/operations.md)
- [SQL для аналитики](../../docs/database/analytics_queries.md)
