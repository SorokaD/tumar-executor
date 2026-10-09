# Baseline Demo MVP

Документ описывает рабочий MVP: случайная baseline-стратегия на **REST polling** выставляет maker **post-only** заявки на вход и выход, сопровождает позицию по TP/SL/timeout и пишет журнал в SQLite + PostgreSQL.

Первый запуск с нуля: **[getting_started.md](getting_started.md)**.  
Measurement baseline (net PnL, fees, analytics): **[baseline_measurement.md](baseline_measurement.md)**.

## Что уже работает

- baseline decision раз в 30 секунд (`strategy/random_baseline`);
- конфиг стратегий в **`config/strategies.yaml`** (не в `.env`);
- вход и выход через **post-only** лимитки (maker), перестановка по `maker_reprice_sec` / `maker_max_wait_sec`;
- локальный мониторинг TP/SL/timeout по последней цене тикера;
- cooldown после закрытия;
- market fallback на выходе после N неудачных maker exit;
- при расхождении памяти и биржи: восстановление позиции (`position_reconciled`);
- **dual-write**: SQLite (ops) + PostgreSQL `okx_exec` (аналитика, async);
- `trade_results` с **gross/net PnL**, комиссиями, execution metrics, `exit_reason`, `close_source`.

## Быстрый запуск

1. Скопируйте `.env.example` в `.env` (см. [getting_started.md](getting_started.md)).
2. Для **реальной торговли на OKX** (demo или иначе): заполните `OKX_API_KEY`, `OKX_API_SECRET`, `OKX_API_PASSPHRASE`, выставите `OKX_HFT_SAFE_MODE=0` и нужный `OKX_HFT_RUNTIME_MODE` / `OKX_ENABLE_REAL_OKX_IN_PAPER` по [таблице в getting_started.md](getting_started.md#stub-vs-okx-rest-reference).
3. Запустите:

```bash
python -m app.main
```

## Ключевые переменные окружения

Инфраструктура — в `.env` ([.env.example](../.env.example)). **Параметры стратегии** — в `config/strategies.yaml` (`OKX_HFT_STRATEGIES_CONFIG`).

| Переменная | Смысл |
|------------|--------|
| `OKX_HFT_RUNTIME_MODE` | `live` / `paper` / `replay` |
| `OKX_HFT_SAFE_MODE` | `1` — stub, без ордеров на биржу |
| `OKX_FLAG_DEMO` | demo API OKX |
| `OKX_SQLITE_PATH` | SQLite ops-журнал |
| `OKX_HFT_POSTGRES_ENABLED` | `1` — запись в PostgreSQL |
| `POSTGRES_LINK`, `POSTGRES_PORT`, … | подключение к `okx_exec` |
| `OKX_LOOP_SLEEP_SEC` | пауза главного цикла |

Стратегия (`config/strategies.yaml`):

| Параметр | Смысл |
|----------|--------|
| `inst_id`, `order_size`, `td_mode` | инструмент и размер |
| `decision_step_sec`, `cooldown_sec` | тайминг решений |
| `take_profit_ticks`, `stop_loss_ticks`, `timeout_sec` | выход |
| `exit_maker_max_attempts`, `exit_market_fallback_enabled` | maker exit + fallback |
| `fee_rate_maker`, `fee_rate_taker` | оценка комиссий, если нет OKX fills |

## Как понять, что система торгует

По логам:

- `starting baseline executor`
- `strategy decided LONG/SHORT`
- `entry maker order submitted` / `entry order submitted` (формулировки в логах см. `app/orchestrator.py`)
- `position opened` (после fill входа)
- `tp` / `sl` / `timeout` и выход maker (или market fallback)
- `position closed` с `gross_pnl`, `net_pnl`, `exit_reason`, `close_source`

В SQLite / PostgreSQL:

- таблицы `signals`, `orders`, `positions`, `trade_results`, `service_events`;
- в `trade_results`: `net_pnl`, `fee_source`, execution metrics;
- при reconcile: `position_reconciled`, `exit_reason=reconcile`.

## Smoke-run (авто-остановка)

```bash
python -m app.main --run-seconds 60
```

или

```bash
python -m app.main --max-loops 120
```

После завершения приложение печатает summary:

```text
Run finished
signals: N
orders: N
positions: N
trade_results: N
service_events: N
```

## Быстрый check OKX без торговли

```bash
python -m app.main --check-okx
```

Команда проверяет:

- доступность account endpoint;
- доступность ticker;
- получение tick size.

## Три практичных сценария

### A) Локально без биржи (stub)

- `OKX_HFT_RUNTIME_MODE=paper`
- `OKX_HFT_SAFE_MODE=1` **или** `OKX_ENABLE_REAL_OKX_IN_PAPER=0`
- запуск: `python -m app.main --run-seconds 60`

Ожидание: цикл и SQLite работают, заявки на OKX не уходят.

### B) OKX demo с реальными заявками

- `OKX_FLAG_DEMO=1`
- валидные **demo** credentials
- **`OKX_HFT_SAFE_MODE=0`**
- либо `OKX_HFT_RUNTIME_MODE=live`, либо `paper` + `OKX_ENABLE_REAL_OKX_IN_PAPER=1`

Ожидание: HTTP к OKX, реальные post-only ордера на demo-счёте.

### C) Короткий smoke после правок кода

```bash
python -m app.main --max-loops 60
```

## Ограничения MVP

- только REST polling (без private WebSocket в этом контуре);
- одна включённая стратегия на инструмент в net mode (см. strategy manager);
- funding в `trade_results` пока не учитывается;
- нет отдельного HTTP control plane в обязательном пути запуска (optional `control` в `pyproject.toml`).

## Аналитика после сбора данных

```bash
python scripts/trade_daily_summary.py --strategy random_baseline_v1
```

См. [baseline_measurement.md](baseline_measurement.md).
