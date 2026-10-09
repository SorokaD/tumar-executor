# Пакет `accounting`

## Назначение блока

**Учёт и атрибуция**: PnL, комиссии, funding, качество исполнения и slippage относительно эталонной цены.

## Реализовано (baseline measurement)

| Модуль | Роль |
|--------|------|
| `pnl_engine.py` | `calc_gross_pnl` (long/short), `calc_net_pnl` |
| `fee_engine.py` | комиссии из OKX fills (`fees_from_okx_fills`) или оценка (`estimate_fees`) |
| `execution_quality.py` | каркас slippage (расширение позже) |
| `funding_engine.py` | каркас funding |

Используется при закрытии позиции: `execution/trade_finalize.py` → `trade_results` в SQLite/PG.

`fee_source` в журнале: `okx_fill` | `estimated_config` | `missing`.

Конфиг ставок: `config/strategies.yaml` → `execution.fee_rate_maker` / `fee_rate_taker`.

См. [docs/baseline_measurement.md](../docs/baseline_measurement.md).

## Почему он существует

Домен фиксирует факты исполнения; **интерпретация** финансового результата и отчётность — отдельная задача с собственными правилами и источниками данных (например funding из другого потока).

## Основные обязанности

- расчёт realized/unrealized PnL по правилам инструмента;
- учёт комиссий мейкера/тейкера;
- учёт funding для perpetual (roadmap);
- метрики качества исполнения (slippage, impact).

## Что здесь находиться не должно

- выставление ордеров;
- прямые REST вызовы (кроме опциональной подгрузки справочников, если не вынесено).

## Связи с другими блоками

- **domain** — fills, позиции, снимки.
- **persistence** — хранение агрегатов отчётности (`trade_results`).
- **observability** — бизнес-метрики учёта.

## Примеры будущего расширения

- мульти-валютный учёт с хеджем курса;
- сверка с выгрузками биржи для бухгалтерии;
- атрибуция по стратегиям и под-счетам.
