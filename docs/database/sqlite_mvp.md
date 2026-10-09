# SQLite MVP (текущая реализация)

Файл: `OKX_SQLITE_PATH` (по умолчанию `data/baseline_mvp.sqlite3`).

Код: `persistence/sqlite_store.py`, вызовы через `persistence/executor_store.py` из `app/orchestrator.py`, `app/strategy_manager.py`, `control/app.py`.

## Назначение

Лёгкий журнал для:

- smoke-run и диагностики на VPS;
- control-api (`strategies_registry`, `strategy_commands`);
- быстрой проверки «сколько сигналов/ордеров» без PostgreSQL;
- локальной копии `trade_results` с fees и execution metrics.

**Ограничение:** нет полной истории reprice по ордерам (`INSERT OR REPLACE` на `orders`); для этого — PostgreSQL `okx_exec.orders`.

## Таблицы

### `signals`

| Колонка | Тип | Описание |
|---------|-----|----------|
| signal_id | TEXT PK | id сигнала (`rb-…`) |
| strategy_name | TEXT | стратегия |
| side | TEXT | long/short (как в baseline) |
| created_at | TEXT ISO | время решения |

### `orders`

| Колонка | Тип | Описание |
|---------|-----|----------|
| local_order_id | TEXT PK | client order id |
| strategy_name | TEXT | |
| exchange_order_id | TEXT | ordId OKX |
| side | TEXT | buy/sell |
| order_type | TEXT | post_only / market |
| price | REAL | |
| size | REAL | |
| status | TEXT | submitted / filled / canceled |
| created_at | TEXT | |
| filled_at | TEXT | |

**Проблема для аналитики:** `INSERT OR REPLACE` по `local_order_id` — при reprice **теряется** предыдущая версия ордера. В PG каждый reprice — новая строка.

### `positions`

| Колонка | Тип | Описание |
|---------|-----|----------|
| position_id | TEXT PK | |
| strategy_name | TEXT | |
| side | TEXT | long/short |
| entry_price | REAL | |
| exit_price | REAL | NULL пока открыта |
| entry_ts | TEXT | |
| exit_ts | TEXT | |
| size | REAL | |
| exit_reason | TEXT | tp / sl / timeout / reconcile / … |

### `trade_results`

Итог сделки (одна строка на `position_id`). Колонки добавляются автоматически при старте (`_ensure_column`).

| Колонка | Тип | Описание |
|---------|-----|----------|
| position_id | TEXT PK | |
| strategy_name | TEXT | |
| gross_pnl | REAL | до комиссий |
| fees | REAL | = `fees_total` (entry + exit) |
| net_pnl | REAL | после комиссий |
| holding_seconds | REAL | |
| entry_fee, exit_fee | REAL | |
| fee_ccy | TEXT | |
| entry_liquidity, exit_liquidity | TEXT | maker / taker |
| entry_avg_px, exit_avg_px | REAL | |
| fee_source | TEXT | `okx_fill` / `estimated_config` / `missing` |
| fee_status | TEXT | `ok` / `pending` |
| exit_reason | TEXT | tp / sl / timeout / reconcile |
| close_source | TEXT | executor_maker / executor_market_fallback / okx_reconcile |
| signal_id | TEXT | исходный entry signal (через reprice) |
| inst_id, position_side, size | | снимок сделки |
| opened_at, closed_at | TEXT ISO | |
| execution_metrics_json | TEXT | JSON: reprice counts, wait_sec, market_fallback, … |

Пример:

```sql
SELECT position_id, gross_pnl, net_pnl, fees, fee_source, exit_reason, close_source
FROM trade_results
ORDER BY closed_at DESC LIMIT 10;
```

### `service_events`

Произвольные события: `event_type`, `message`, `payload` (JSON string), `level`, `strategy_name`.

Примеры `event_type`: `decision`, `entry_submitted`, `entry_not_filled`, `position_reconciled_startup`, `timeout_exit`, `loop_iteration_error`.

### `strategies_registry`

Состояние стратегий для strategy manager / control-api.

### `strategy_commands`

Очередь команд: enable, disable, restart.

## Быстрые запросы (на VPS)

```bash
sudo docker exec okx-hft-executor python -c "
import sqlite3
c = sqlite3.connect('/app/data/baseline_mvp.sqlite3')
for t in ['signals','orders','positions','trade_results','service_events']:
    print(t, c.execute(f'select count(*) from {t}').fetchone()[0])
"
```

```sql
-- fill rate (грубо)
SELECT status, count(*) FROM orders GROUP BY status;

-- открытые позиции в журнале
SELECT * FROM positions WHERE exit_ts IS NULL;
```

## Маппинг SQLite → PostgreSQL (при dual-write)

| SQLite | PostgreSQL |
|--------|------------|
| signals | strategy_signals |
| orders | orders (+ execution_attempts на каждый submit) |
| positions | positions |
| trade_results | trade_results |
| service_events | service_events |
| strategies_registry | strategies_registry |
| strategy_commands | strategy_commands |
| — | executor_runs, order_fills, reconciliation_events, execution_attempts |
