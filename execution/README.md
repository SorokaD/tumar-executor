# Пакет `execution`

## Назначение блока

**Исполнительное ядро**: превращение допустимых намерений в жизненный цикл заявок и позиций, state machine, reconciliation, менеджеры ордеров/позиции/выхода.

## Почему он существует

Стратегия отвечает на «что хотим», risk — «можно ли», exchange — «как говорить с биржей». **Execution** связывает это в **упорядоченные переходы состояния** с восстановлением после сбоев.

## Основные обязанности

- координация order manager, position manager, exit manager;
- применение политики исполнения (post-only, chase, cancel-replace — в будущем);
- запуск reconciliation и обработка расхождений;
- эмиссия доменных событий / вызов persistence.

## Что здесь должно находиться

- `engine` — главный цикл исполнения;
- `order_manager`, `position_manager`, `exit_manager`;
- `state_machine` — переходы статусов заявки/позиции;
- `reconciliation` — сверка с биржей (оркестрация вызовов exchange + обновление состояния).

## Что здесь находиться не должно

- расчёт alpha-сигналов;
- детальная формула PnL (делегировать `accounting`);
- низкоуровневый парсинг JSON OKX (делегировать `exchange`).

## Baseline (реализовано в `app/orchestrator.py`)

| Модуль | Роль |
|--------|------|
| `trade_lifecycle.py` | In-memory метрики одной сделки (reprice, wait, signal_id) |
| `trade_finalize.py` | Сборка `trade_results`: fees, net PnL, exit_reason, close_source |

См. [docs/baseline_measurement.md](../docs/baseline_measurement.md).

## Основные сущности / модули

См. модули пакета; граница с `exchange` — через порт `ExchangeClient`.

## Связи с другими блоками

- **domain** — состояние заявок и позиций.
- **risk** — вызовы до отправки и во время жизни позиции.
- **exchange** — транспорт.
- **persistence** — журнал и снимки.
- **observability** — метрики переходов и задержек.

## Примеры будущего расширения

- plug-in execution policies;
- отдельный worker только для reconciliation;
- приоритетные очереди намерений per instrument.
