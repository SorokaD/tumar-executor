# Запуск сервиса с нуля

Этот документ — **единая точка входа**: после прочтения можно поднять процесс, понять, какой режим выбран, и где смотреть результат. Остальная документация ([architecture.md](architecture.md), [baseline_demo_mvp.md](baseline_demo_mvp.md)) углубляет детали.

## Требования

- **Python 3.11+** (в проекте указано `requires-python >= 3.11` в `pyproject.toml`).
- Сеть до `OKX_BASE_URL` (по умолчанию `https://www.okx.com`).
- Для **реальных запросов к OKX** (demo или prod): ключ, секрет, passphrase из личного кабинета OKX.

Рабочая директория в примерах ниже — **корень репозитория** (`okx-hft-executor/`).

## 1. Клонирование и виртуальное окружение

**Windows (PowerShell):**

```powershell
cd D:\path\to\okx-hft-executor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e .
```

**Linux / macOS:**

```bash
cd /path/to/okx-hft-executor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e .
```

Для разработки (тесты, ruff, mypy):

```bash
pip install -e ".[dev]"
```

## 2. Конфигурация: `.env`

1. Скопируйте шаблон:

   ```bash
   cp .env.example .env
   ```

   На Windows: `copy .env.example .env`

2. Откройте `.env` в редакторе. Все значения читает `config/settings.py` (префиксы переменных как в [.env.example](../.env.example)).

### Stub vs OKX REST (reference)

`app/bootstrap.py` подключает **реальный REST-клиент** только если **не** выполняется ни одно из условий «использовать заглушку»:

| Условие | Результат |
|--------|-----------|
| `OKX_HFT_RUNTIME_MODE=replay` | всегда заглушка |
| `OKX_HFT_SAFE_MODE=1` | всегда заглушка (ордера на биржу не уходят) |
| `OKX_HFT_RUNTIME_MODE=paper` **и** `OKX_ENABLE_REAL_OKX_IN_PAPER=0` | заглушка |
| Иначе | **OkxRestClient** — реальные HTTP-запросы к OKX |

**Практика:**

- **Локально «погонять цикл» без биржи:** `OKX_HFT_RUNTIME_MODE=paper`, `OKX_HFT_SAFE_MODE=1` (или оставить `OKX_ENABLE_REAL_OKX_IN_PAPER=0`). Ключи не обязательны для заглушки, но для единообразия `.env` можно заполнить.
- **OKX demo с реальными заявками:** `OKX_FLAG_DEMO=1`, ключи demo, **`OKX_HFT_SAFE_MODE=0`**, и дальше один из вариантов:
  - `OKX_HFT_RUNTIME_MODE=live` — типичный путь для demo/live API; или
  - `OKX_HFT_RUNTIME_MODE=paper` и **`OKX_ENABLE_REAL_OKX_IN_PAPER=1`** — тот же REST, но режим в настройках остаётся `paper`.

Без валидных `OKX_API_*` реальный клиент при старте не соберётся (см. `OkxRestClient`).

### 2.2. Минимальный набор переменных для demo-торговли

Имеет смысл выставить явно:

```env
OKX_HFT_RUNTIME_MODE=live
OKX_HFT_SAFE_MODE=0
OKX_FLAG_DEMO=1
OKX_API_KEY=...
OKX_API_SECRET=...
OKX_API_PASSPHRASE=...
OKX_BASE_URL=https://www.okx.com
OKX_SQLITE_PATH=data/baseline_mvp.sqlite3
OKX_LOOP_SLEEP_SEC=1
OKX_HFT_POSTGRES_ENABLED=0
```

Параметры стратегии (инструмент, размер, TP/SL) — в **`config/strategies.yaml`**, не в `.env`.

Остальное — из `.env.example` (Postgres, control-api token и т.д.).

## 3. Команды запуска

Точка входа: **`python -m app.main`** (или установленный скрипт `okx-hft-executor` после `pip install -e .`).
По умолчанию запускается **strategy manager** (мульти-стратегийный режим).

| Задача | Команда |
|--------|---------|
| Запустить strategy manager | `python -m app.main` |
| Запустить только одну baseline-стратегию (legacy) | `python -m app.main --single-strategy` |
| Ограничение по времени (legacy single strategy) | `python -m app.main --single-strategy --run-seconds 259200` |
| Ограничение числа итераций (legacy single strategy) | `python -m app.main --single-strategy --max-loops 500` |
| Только проверить конфиг и контекст | `python -m app.main --dry-run` |
| Проверить доступность OKX API **без** торгового цикла | `python -m app.main --check-okx` |

Остановка интерактивного запуска: **Ctrl+C**. В конце корректного завершения в лог пишется сводка по SQLite (`signals`, `orders`, `positions`, …).

### Управление стратегиями из CLI

```bash
python -m app.main --list-strategies
python -m app.main --strategy-enable random_baseline_v1
python -m app.main --strategy-disable random_baseline_v1 --strategy-disable-mode drain
python -m app.main --strategy-disable random_baseline_v1 --strategy-disable-mode force
python -m app.main --strategy-restart random_baseline_v1
```

### Долгий фоновый запуск (Windows)

Если нужен процесс без окна терминала (как при ручном `Start-Process`):

```powershell
Set-Location D:\path\to\okx-hft-executor
Start-Process python -ArgumentList "-m","app.main","--run-seconds","259200" -WorkingDirectory (Get-Location) -WindowStyle Hidden
```

Проверка, что процесс жив: диспетчер задач или `Get-CimInstance Win32_Process` с фильтром по `CommandLine`, содержащему `app.main`.

## 4. Как убедиться, что всё работает

1. **Логи (stdout):** `starting baseline executor`, `postgres journal enabled`, `run_id=…`, решения стратегии, `position closed` с net PnL.
2. **SQLite:** `OKX_SQLITE_PATH` — см. [database/sqlite_mvp.md](database/sqlite_mvp.md).
3. **PostgreSQL** (если `OKX_HFT_POSTGRES_ENABLED=1`): `okx_exec.trade_results`, view `v_trade_daily_summary` — см. [baseline_measurement.md](baseline_measurement.md).
4. **Быстрая проверка API:** `python -m app.main --check-okx`.

## 5. Частые проблемы

| Симптом | Что проверить |
|---------|----------------|
| Процесс крутится, но на OKX ничего нет | `OKX_HFT_SAFE_MODE=1` или paper без `OKX_ENABLE_REAL_OKX_IN_PAPER=1` → заглушка. |
| Ошибка при старте про ключи | Заполнены `OKX_API_KEY` / `OKX_API_SECRET` / `OKX_API_PASSPHRASE`, режим не заглушка. |
| `503` / таймауты OKX | Сеть или сторона биржи; цикл делает паузы и повторяет итерации (см. логи `executor unhealthy`, `loop_iteration_error`). |
| На бирже есть позиция, сервис «не видит» | В актуальной логике оркестратора выполняется восстановление позиции из REST snapshot позиций; в `service_events` ищите `position_reconciled`. |
| Docker: `unable to open database file` | На хосте `chown` для `./data` под пользователя `app` в контейнере (`uid=100`, см. [deployment_vps_runbook.md](deployment_vps_runbook.md)). |

## 6. Деплой на VPS

Пошаговый runbook (Contabo/Ubuntu, SSH, Docker, scp `.env`, live mode):

→ [deployment_vps_runbook.md](deployment_vps_runbook.md)

## 7. Куда идти дальше

- Поведение baseline: [baseline_demo_mvp.md](baseline_demo_mvp.md).
- Measurement & analytics: [baseline_measurement.md](baseline_measurement.md).
- Стратегия подробно: [strategies/random_execution_baseline.md](strategies/random_execution_baseline.md).
- Режимы `live` / `paper` / `replay`: [runtime_modes.md](runtime_modes.md).
- Идея сверки с биржей: [reconciliation.md](reconciliation.md).
- Карта каталогов: [project_structure.md](project_structure.md).
- Обзор проекта: [project_overview.md](project_overview.md).
- Деплой на хост: [deployment_hybrid.md](deployment_hybrid.md).
- **VPS runbook:** [deployment_vps_runbook.md](deployment_vps_runbook.md).
- Удаленное управление: [control_api.md](control_api.md).
