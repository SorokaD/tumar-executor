"""Docker healthcheck: жив ли журнал PostgreSQL в торговом процессе.

Торговый процесс раз в несколько секунд пишет состояние журналов в файл
(`persistence.postgres_journal.journal_health_file`). Без этой проверки executor
оставался `healthy`, пока сутки выбрасывал события из-за мёртвого соединения.

Выход 0 — здоров, 1 — нет (причины печатаются в stdout, видны в `docker inspect`).
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from config.settings import get_settings
from persistence.postgres_journal import journal_health_file

STALE_FILE_SEC = 60.0
# keepalive (30 + 3×10 с) и tcp_user_timeout (30 с) рвут мёртвое соединение быстрее.
LOOP_STALL_SEC = 90.0
DISCONNECTED_SEC = 120.0
RECENT_DROP_SEC = 300.0


def evaluate(report: dict[str, Any], *, now: float) -> list[str]:
    problems: list[str] = []
    age = now - float(report.get("written_at", 0))
    if age > STALE_FILE_SEC:
        problems.append(f"journal health file is stale ({age:.0f}s)")
        return problems
    for j in report.get("journals", []):
        name = f"journal run_id={j.get('run_id')}"
        if not j.get("worker_alive"):
            problems.append(f"{name}: worker thread is dead")
            continue
        loop_age = j.get("loop_age_sec")
        if loop_age is not None and loop_age > LOOP_STALL_SEC:
            problems.append(f"{name}: worker stalled for {loop_age:.0f}s")
        offline = j.get("disconnected_for_sec")
        if offline is not None and offline > DISCONNECTED_SEC:
            problems.append(f"{name}: postgres unreachable for {offline:.0f}s ({j.get('last_error')})")
        drop_age = j.get("last_drop_age_sec")
        if drop_age is not None and drop_age < RECENT_DROP_SEC:
            problems.append(f"{name}: dropping events (dropped_events={j.get('dropped_events')})")
    return problems


def main() -> int:
    settings = get_settings()
    if not settings.postgres_is_configured():
        return 0
    path = journal_health_file()
    if not path.exists():
        # Ни одна стратегия ещё не открыла журнал (старт или все стратегии выключены).
        return 0
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"journal health file unreadable: {exc}")
        return 1
    problems = evaluate(report, now=time.time())
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
