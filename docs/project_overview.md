# Обзор проекта `okx-hft-executor`

`okx-hft-executor` — сервис исполнения торговых стратегий для OKX с локальным журналированием
сделок и событий в SQLite. Текущая версия ориентирована на baseline execution и управление
несколькими стратегиями через strategy manager.

## Что делает сервис

- поднимает runtime-менеджер стратегий;
- запускает стратегии параллельно (сейчас торговая логика baseline);
- исполняет заявки через OKX REST или stub (по режиму);
- сопровождает позицию (TP/SL/timeout, maker reprice, reconciliation);
- сохраняет сигналы, ордера, позиции, **gross/net PnL**, комиссии и execution metrics в SQLite + PostgreSQL `okx_exec`;
- принимает команды включения/выключения стратегий через очередь команд.

## Ключевые подсистемы

- `app/main.py` — вход в процесс, запуск strategy manager или служебных команд.
- `app/strategy_manager.py` — управление жизненным циклом стратегий (enable/disable/restart).
- `app/orchestrator.py` — baseline-оркестрация `signal -> order -> position -> exit`.
- `strategy/contracts.py` — единый контракт стратегии (plugin API).
- `strategy/registry.py` — реестр `strategy_name -> factory`.
- `exchange/okx/rest_client.py` — интеграция с OKX v5 REST (+ `get_order_fills` для комиссий).
- `persistence/executor_store.py` — dual-write SQLite + PostgresJournal.
- `persistence/sqlite_store.py` — локальный ops-журнал.
- `execution/trade_lifecycle.py`, `execution/trade_finalize.py` — measurement baseline.
- `accounting/fee_engine.py`, `accounting/pnl_engine.py` — gross/net PnL и комиссии.
- `control/app.py` — удаленный Control API для управления без SSH.

## Модель масштабирования

Проект построен в гибридной модели:

- изоляция по контейнерам (`executor` + `control-api`);
- параллельные стратегии внутри `executor` через strategy manager;
- стратегия маркируется `strategy_name` во всех ключевых таблицах:
  - `signals`
  - `orders`
  - `positions`
  - `trade_results`
  - `service_events`

Это позволяет безопасно добавлять новые стратегии и управлять ими без остановки сервиса.

## Хранение данных

| Слой | Где | Документация |
|------|-----|--------------|
| Операционный журнал | SQLite `OKX_SQLITE_PATH` | [database/sqlite_mvp.md](database/sqlite_mvp.md) |
| Аналитика / DWH | PostgreSQL `okx_exec` | [database/README.md](database/README.md), [baseline_measurement.md](baseline_measurement.md) |

DDL: `migrations/postgres/` (включая `005`, `006`). Dual-write: `ExecutorStore`.

## Как добавить новую стратегию

1. Создать модуль стратегии в `strategy/<name>/service.py`.
2. Реализовать контракт из `strategy/contracts.py`.
3. Зарегистрировать стратегию в `strategy/registry.py`.
4. Добавить блок в `config/strategies.yaml` и зарегистрировать в `strategy/registry.py`.

После этого strategy manager и control-api изменений не требуют.

## Runtime-режимы

- `live` — боевая торговля;
- `paper` — бумажный/демо-контур;
- `replay` — воспроизведение (развитие в roadmap).

Детали: `docs/runtime_modes.md`.

## Управление стратегиями

Варианты управления:

- локально/удаленно через команду внутри контейнера (`python -m app.main --strategy-*`);
- удаленно через HTTP (`control-api`) с токеном `X-API-Key`.

Команды поддерживают:

- `enable`
- `disable` (`drain` или `force`)
- `restart`
- просмотр статуса (`list` / `GET /strategies`)

## Для кого документ

Этот файл — верхнеуровневая карта. Для практической эксплуатации смотри:

- `docs/getting_started.md` — старт и базовые команды;
- `docs/deployment_vps_runbook.md` — **VPS: Docker, .env, live mode, troubleshooting**;
- `docs/deployment_hybrid.md` — деплой на хост и операционные шаги;
- `docs/SECURITY_BASELINE_VPS_SSH_AND_NETWORK.md` — SSH, UFW, Fail2Ban на VPS;
- `docs/control_api.md` — удаленное управление стратегиями;
- `docs/trade_lifecycle.md` — жизненный цикл сделки.
