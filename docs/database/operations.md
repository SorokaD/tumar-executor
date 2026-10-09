# Операции с базой данных

## Применить миграции PostgreSQL

Полная инструкция также в [migrations/postgres/README.md](../../migrations/postgres/README.md).

### Без Python / из контейнера VPS

```bash
sudo docker compose exec executor python scripts/apply_pg_migrations.py
```

### Linux / VPS (apply_all.sh)

```bash
export DATABASE_URL='postgresql://executor_rw:PASSWORD@HOST:5432/okx_hft'
cd /path/to/okx-hft-executor
bash migrations/postgres/apply_all.sh
# затем вручную 005 и 006, если apply_all.sh ещё не обновлён:
psql "$DATABASE_URL" -f migrations/postgres/005_add_execution_metrics.sql
psql "$DATABASE_URL" -f migrations/postgres/006_trade_daily_summary_view.sql
```

Полный сброс схемы (удаляет данные `okx_exec`):

```bash
RESET=1 bash migrations/postgres/apply_all.sh
```

### Windows (PowerShell)

```powershell
$env:DATABASE_URL = "postgresql://executor_rw:PASSWORD@HOST:5432/okx_hft"
psql $env:DATABASE_URL -v ON_ERROR_STOP=1 -f migrations/postgres/001_okx_exec_schema.sql
psql $env:DATABASE_URL -v ON_ERROR_STOP=1 -f migrations/postgres/002_hypertables_indexes.sql
psql $env:DATABASE_URL -v ON_ERROR_STOP=1 -f migrations/postgres/003_triggers.sql
```

Если TimescaleDB недоступен — вместо `002_hypertables_indexes.sql` используйте `002_indexes_only.sql`.

### Проверка после apply

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'okx_exec'
ORDER BY 1;
-- ожидается 11 таблиц

SELECT hypertable_name
FROM timescaledb_information.hypertables
WHERE hypertable_schema = 'okx_exec';
-- ожидается 6 hypertables (если Timescale установлен)
```

## SQLite на VPS

Путь в контейнере: `/app/data/baseline_mvp.sqlite3`  
Переменная: `OKX_SQLITE_PATH`

```bash
# права (если OperationalError: unable to open database file)
sudo chown -R 100:101 /opt/okx-hft-executor/okx-hft-executor/data

# интерактивно
sudo docker exec -it okx-hft-executor python -c "
import sqlite3
c=sqlite3.connect('/app/data/baseline_mvp.sqlite3')
print(c.execute('select count(*) from signals').fetchone())
"
```

Бэкап SQLite:

```bash
cp data/baseline_mvp.sqlite3 data/baseline_mvp.sqlite3.bak.$(date +%Y%m%d)
```

## Подключение к PostgreSQL

Рекомендуется отдельный пользователь с минимальными правами (`executor_rw`):

- `CONNECT` на БД `okx_hft`
- `USAGE` на schema `okx_exec`
- `SELECT, INSERT, UPDATE` на таблицы (без `DROP` в prod)

`DATABASE_URL` для приложения (`PostgresJournal` / миграции):

```text
postgresql://executor_rw:SECRET@167.86.110.201:5432/okx_hft
```

Не коммитить в git. В `.env` на VPS / GitHub Secrets.

## Версионирование схемы

| Версия | Файлы | Примечание |
|--------|-------|------------|
| v1 | 001, 002, 003 | начальная схема okx_exec |
| v2 | 005, 006 | trade_results metrics + daily summary view |
| patch | 004 | выравнивание legacy-таблиц |

Новые изменения — **новые файлы** `004_*.sql`, не править 001 задним числом на prod.

## Типовые ошибки

| Ошибка | Причина | Решение |
|--------|---------|---------|
| `extension "timescaledb" does not exist` | PG без Timescale | `002_indexes_only.sql` |
| `unique constraint` на hypertable | UNIQUE без time column | уже исправлено в 001: `(ts_*, id)` |
| `permission denied for schema okx_exec` | мало прав у user | `GRANT USAGE ON SCHEMA okx_exec TO executor_rw` |
| `relation already exists` | повторный apply без reset | `RESET=1` или ручной DROP |
| `column "strategy_name" does not exist` | старые таблицы из DBeaver до миграции 001 | см. ниже |

### Ошибка `column "strategy_name" does not exist`

Таблицы созданы **вручную** по скриншотам, затем запущен только `002_indexes`.  
`CREATE TABLE IF NOT EXISTS` в `001` **не обновляет** уже существующие таблицы.

**Вариант A (рекомендуется, если нет ценных данных):**

```bash
RESET=1 bash migrations/postgres/apply_all.sh
```

**Вариант B (сохранить данные / дотянуть колонки):**

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/postgres/004_align_legacy_schema.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/postgres/002_indexes_only.sql
# или 002_hypertables_indexes.sql
```

**Диагностика:**

```bash
psql "$DATABASE_URL" -f migrations/postgres/diagnose_okx_exec_schema.sql
```
| SQLite `unable to open database file` | права volume | `chown 100:101 data` |

## Бэкап PostgreSQL

На стороне админа БД (пример):

```bash
pg_dump "$DATABASE_URL" -n okx_exec -Fc -f okx_exec_$(date +%Y%m%d).dump
```

Восстановление:

```bash
pg_restore -d okx_hft -n okx_exec --clean okx_exec_YYYYMMDD.dump
```

## Retention (будущее)

Для hypertables можно настроить Timescale retention policy:

```sql
-- пример: удалять execution_attempts старше 180 дней
-- SELECT add_retention_policy('okx_exec.execution_attempts', INTERVAL '180 days');
```

Пока не включено — хранить всё.

## Связь с Docker Compose

`docker-compose.yml` монтирует SQLite volume. PostgreSQL подключается через `.env`:

```env
OKX_HFT_POSTGRES_ENABLED=1
POSTGRES_LINK=167.86.110.201
POSTGRES_PORT=6432
POSTGRES_DB=okx_hft
POSTGRES_USER=executor_rw
POSTGRES_PASSWORD=...
```

После обновления кода с measurement-логикой — один раз `python scripts/apply_pg_migrations.py` (см. [baseline_measurement.md](../baseline_measurement.md)).
