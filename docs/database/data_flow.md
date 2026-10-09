# Поток данных: executor → таблицы

Связь с логической цепочкой: [trade_lifecycle.md](../trade_lifecycle.md).

## Baseline: успешный цикл (happy path)

```mermaid
sequenceDiagram
    participant S as strategy
    participant O as orchestrator
    participant X as OKX
    participant DB as PostgreSQL

    Note over O,DB: executor_runs: start run
    S->>O: should_decide + make_decision
    O->>DB: strategy_signals (entry)
    O->>DB: execution_attempts (submit_order ok)
    O->>DB: orders (submitted)
    O->>X: place_limit_post_only
    X-->>O: live → filled
    O->>DB: orders (status=filled)
    O->>DB: order_fills (synthetic on fill; OKX fee fetch at close)
    O->>DB: positions (open)
    Note over O: мониторинг TP/SL/timeout
    O->>DB: execution_attempts (submit_order exit)
    O->>DB: orders (exit, reduce_only)
    X-->>O: exit filled
    O->>DB: positions (closed)
    O->>DB: trade_results (gross/net PnL, fees, execution metrics)
```

## Что пишется в SQLite и PostgreSQL

| Событие в orchestrator | SQLite | PostgreSQL `okx_exec` |
|------------------------|--------|------------------------|
| `make_decision()` | signals | strategy_signals |
| `entry maker order submitted` | orders + service_events | orders + execution_attempts |
| `entry order not filled` (canceled) | orders + service_events | orders + execution_attempts |
| `position opened` | positions + service_events | positions |
| reprice | orders (REPLACE ⚠️) | **новый** orders + cancel attempt |
| `timeout/tp/sl exit submitted` | orders + service_events | orders + execution_attempts |
| `position closed` | positions, trade_results (net PnL, fees, metrics) | positions, trade_results, order_fills |
| `startup reconcile` | service_events | reconciliation_events + positions |
| `50102` / loop error | service_events | execution_attempts + service_events |
| strategy enable/disable | strategies_registry, commands | strategies_registry, commands (опционально) |

При закрытии позиции `TradeLifecycleTracker` (`execution/trade_lifecycle.py`) агрегирует reprice/cancel/wait и передаётся в `trade_results` через `execution/trade_finalize.py`.

## Сценарий: ордер не исполнился (частый)

1. `strategy_signals` — сигнал **есть**
2. `execution_attempts` — submit **ok**
3. `orders` — status `submitted` → `canceled`
4. `positions` — **нет** (позиция не открылась)
5. `trade_results` — **нет**

Аналитика: `COUNT(orders WHERE status=canceled) / COUNT(signals)`.

## Сценарий: reprice

1. Ордер A: `orders` status live
2. Cancel → `execution_attempts` action_type=cancel_order
3. Ордер B: **новая** строка `orders`, `parent_order_id_local` = A.order_id_local
4. В SQLite сейчас — только последний id (история теряется)

## Сценарий: рестарт / редеплой

1. Новый `executor_runs` (новый run_id)
2. `reconciliation_events` + `positions` если на OKX есть позиция
3. `service_events` / `execution_attempts`: startup_reconcile
4. Продолжение exit по TP/SL/timeout с восстановленным `position_id`

См. [reconciliation.md](../reconciliation.md).

## Сценарий: несколько стратегий

Strategy manager запускает **отдельный asyncio task** на стратегию:

- у каждой свой `run_id` (через `ExecutorStore` / `executor_runs`)
- общий `inst_id` возможен, но позиция **одна на инструмент** в one-way mode — не запускать две стратегии на один `inst_id` без hedge-логики

Фильтр аналитики: всегда `strategy_name` + период `ts_*`.

## Поля JSONB — что класть

| Таблица | Поле | Пример содержимого |
|---------|------|-------------------|
| strategy_signals | market_snapshot | best_bid, best_ask, last, spread_ticks |
| strategy_signals | features_json | фичи модели v2 |
| execution_attempts | request_payload | instId, side, px, sz, clOrdId |
| execution_attempts | response_payload | OKX code, msg, ordId |
| executor_runs | extra_json | snapshot RandomBaselineConfig |
| orders | raw_response_json | полный ответ place order |

## Идемпотентность

- `signal_id` = `cl_ord_id` на **входе** (baseline)
- Повторный submit с тем же clOrdId → OKX отклонит; писать `execution_attempts` с error
- `fill_id_exchange` + `inst_id` — UNIQUE в `order_fills`
