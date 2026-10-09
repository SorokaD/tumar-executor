# Baseline measurement & analytics

Как `random_baseline_v1` записывает сделки для честного сравнения с будущими baseline и ML-моделями.

Связанные документы: [database/okx_exec_schema.md](database/okx_exec_schema.md), [database/analytics_queries.md](database/analytics_queries.md), [strategies/random_execution_baseline.md](strategies/random_execution_baseline.md).

## Зачем

После каждой **завершённой** сделки можно восстановить:

- почему вошли (`entry_signal_id`, цепочка entry-ордеров);
- почему вышли (`exit_reason`: `tp` / `sl` / `timeout` / `reconcile`, …);
- как закрыли (`close_source`: maker / market fallback / reconcile);
- gross и **net** PnL, комиссии, maker/taker;
- качество исполнения (reprice, cancel, wait_sec, market fallback).

Все поля привязаны к `strategy_name`, `run_id`, `signal_id` — не захардкожены под random.

## Где смотреть

| Слой | Назначение |
|------|------------|
| **SQLite** (`OKX_SQLITE_PATH`) | ops, быстрая диагностика на VPS |
| **PostgreSQL** `okx_exec` | аналитика, сравнение стратегий, daily summary |

Dual-write: `persistence/executor_store.py` → SQLite синхронно, PostgreSQL через фоновый `PostgresJournal` (не блокирует торговый цикл).

## Ключевые поля `trade_results`

| Поле | Смысл |
|------|--------|
| `gross_pnl` | PnL до комиссий: `(exit - entry) × size` (long/short) |
| `entry_fee`, `exit_fee`, `fees_total` | комиссии |
| `net_pnl` | `gross_pnl - fees_total` — **главная метрика для сравнения** |
| `fee_source` | `okx_fill` (с биржи) или `estimated_config` (оценка из YAML) |
| `entry_liquidity`, `exit_liquidity` | `maker` / `taker` |
| `exit_reason` / `final_exit_reason` | `tp`, `sl`, `timeout`, `reconcile`, … |
| `close_source` | `executor_maker`, `executor_market_fallback`, `okx_reconcile` |
| `exit_market_fallback_used` | был ли market reduce-only после неудачных maker exit |
| `entry_reprice_count`, `exit_reprice_count` | перестановки maker |
| `entry_wait_sec`, `exit_wait_sec` | время от submit до fill |

Полный список колонок PG: [okx_exec_schema.md](database/okx_exec_schema.md#trade_results).

## Конфиг комиссий (оценка)

В `config/strategies.yaml` → `execution`:

```yaml
fee_rate_maker: "0.0002"
fee_rate_taker: "0.0005"
```

Используются, если OKX fills недоступны или пустые (timeout fetch 2 сек, не блокирует exit).

## Цепочка signal → trade_result

```text
strategy_signals (signal_id)
  → orders (entry, parent_order_id_local при reprice)
  → positions (open)
  → orders (exit, reduce_only)
  → trade_results (одна строка на position_id)
```

`signal_id` на entry сохраняется в `TradeLifecycleTracker` при `make_decision()` и не теряется при reprice (`entry-...` clOrdId).

Код: `execution/trade_lifecycle.py`, `execution/trade_finalize.py`, хуки в `app/orchestrator.py`.

## Миграции PostgreSQL

После деплоя кода с measurement-логикой:

```bash
python scripts/apply_pg_migrations.py
```

Новые файлы:

- `migrations/postgres/005_add_execution_metrics.sql` — колонки в `trade_results`
- `migrations/postgres/006_trade_daily_summary_view.sql` — view `okx_exec.v_trade_daily_summary`

На VPS:

```bash
sudo docker compose exec executor python scripts/apply_pg_migrations.py
```

SQLite мигрирует сам при старте (`_ensure_column` в `sqlite_store.py`).

## Дневной summary

**CLI:**

```bash
python scripts/trade_daily_summary.py --strategy random_baseline_v1 --from-day 2026-07-01
```

**SQL:**

```sql
SELECT * FROM okx_exec.v_trade_daily_summary
WHERE strategy_name = 'random_baseline_v1'
ORDER BY trade_day DESC;
```

Метрики: `trades_count`, `winrate_net`, `net_pnl_sum`, `total_fee_sum`, `market_fallback_ratio`, `maker_entry_ratio`, `avg_entry_reprice_count`, …

## Сравнение с ML (через 7–14 дней)

Фильтр: один `inst_id`, одинаковый период, разные `strategy_name`:

- `net_pnl_sum`, `winrate_net` — итог после комиссий;
- `market_fallback_ratio` — насколько maker-first «ломается» на выходе;
- распределение `exit_reason` — структура поведения, не только PnL.

Примеры SQL: [analytics_queries.md](database/analytics_queries.md).

## Логи при закрытии сделки

В `docker compose logs executor` после fill exit:

```text
position closed: id=pos-... gross_pnl=... net_pnl=... fees=...
exit_reason=sl close_source=executor_maker fee_source=okx_fill
```
