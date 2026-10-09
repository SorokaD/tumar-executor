# Пакет `persistence`

## Назначение блока

**Долговечность данных**: репозитории, unit of work, опциональные storage-модели (ORM), восстановление состояния после рестарта.

## Почему он существует

Память процесса эфемерна. Журнал намерений, снимки позиций и события риска должны переживать рестарт и использоваться в reconciliation.

## Основные обязанности

- абстрагировать БД/файл/облако от домена;
- обеспечить атомарность записи связанных сущностей (UoW);
- поддерживать идемпотентность записи событий (по event_id).

## Что здесь должно находиться

- интерфейсы репозиториев и их реализации;
- `unit_of_work` — граница транзакции;
- `storage_models` — таблицы/документы, если не используется чистый event store.

## Что здесь находиться не должно

- торговые правила;
- вызовы OKX;
- расчёт PnL (только сохранение уже посчитанных снимков).

## Основные сущности / модули

| Модуль | Роль |
|--------|------|
| `repositories` | CRUD и запросы по типам сущностей. |
| `unit_of_work` | Транзакционная граница. |
| `storage_models` | ORM-модели (если применимо). |

## Связи с другими блоками

- **execution** сохраняет переходы и результаты.
- **accounting** может читать историю исполнений.
- **observability** — метрики ошибок БД.

| `sqlite_store.py` | SQLite ops-журнал |
| `executor_store.py` | Фасад dual-write (SQLite + `PostgresJournal`) |
| `postgres_journal.py` | Асинхронная очередь записи в `okx_exec` |

## Документация по СУБД

Полное описание SQLite MVP и PostgreSQL `okx_exec`: **[docs/database/README.md](../docs/database/README.md)**.

- **SQLite** — ops, control-api (`sqlite_store.py`).
- **PostgreSQL** — аналитика (`postgres_journal.py` через `ExecutorStore`).
- DDL: `migrations/postgres/` (`005`, `006` — measurement baseline).
- См. [docs/baseline_measurement.md](../docs/baseline_measurement.md).

## Примеры будущего расширения

- event sourcing + проекции;
- отдельное холодное хранилище для архива;
- миграции Alembic вне runtime-пути.
