"""PostgresJournal: переживает обрыв соединения и не теряет незакоммиченные события."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

import psycopg
import pytest
from psycopg import errors as pg_errors

from app.healthcheck import evaluate
from persistence.postgres_journal import (
    PostgresJournal,
    RunStartParams,
    journal_health_file,
    write_journal_health_file,
)

RUN_ID = 7


class _Cursor:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...]:
        return self._row


class FakeConn:
    def __init__(self, db: FakeDb) -> None:
        self.db = db
        self.pending: list[tuple[Any, ...]] = []
        self.closed = False
        self.broken = False

    def execute(self, query: Any, params: tuple[Any, ...] = ()) -> _Cursor:
        if self.closed or self.broken:
            raise psycopg.OperationalError("the connection is lost")
        if self.db.reject_message is not None and self.db.reject_message in params:
            raise pg_errors.UniqueViolation("duplicate key value violates unique constraint")
        self.pending.append(params)
        return _Cursor((RUN_ID,))

    def commit(self) -> None:
        if self.broken:
            raise psycopg.OperationalError("the connection is lost")
        if self.db.fail_next_commit:
            self.db.fail_next_commit = False
            self.broken = True
            raise psycopg.OperationalError(
                "consuming input failed: could not receive data from server: Connection timed out"
            )
        self.db.committed.extend(self.pending)
        self.pending = []

    def rollback(self) -> None:
        if self.broken:
            raise psycopg.OperationalError("the connection is lost")
        self.pending = []

    def close(self) -> None:
        self.closed = True


class FakeDb:
    def __init__(self) -> None:
        self.committed: list[tuple[Any, ...]] = []
        self.connects = 0
        self.connect_failures = 0
        self.fail_next_commit = False
        self.reject_message: str | None = None
        self.last_kwargs: dict[str, Any] = {}

    def connect(self, dsn: str, **kwargs: Any) -> FakeConn:
        self.connects += 1
        self.last_kwargs = kwargs
        if self.connect_failures > 0:
            self.connect_failures -= 1
            raise psycopg.OperationalError("connection refused")
        return FakeConn(self)

    def event_messages(self) -> list[str]:
        # service_events: (run_id, severity, event_type, strategy_name, inst_id, message, payload)
        return [p[5] for p in self.committed if len(p) == 7 and p[2] == "test_event"]


@pytest.fixture(autouse=True)
def _health_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OKX_HFT_JOURNAL_HEALTH_FILE", str(tmp_path / "journal_health.json"))


def _wait_until(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


def _journal(db: FakeDb) -> PostgresJournal:
    return PostgresJournal("postgresql://u:p@db/x", connect=db.connect, reconnect_backoff=(0.01, 0.02))


def _start(journal: PostgresJournal) -> int:
    return journal.start_run(
        RunStartParams(
            runtime_mode="demo",
            environment_name="test",
            strategy_name="random_baseline_v1",
            inst_id="BTC-USDT-SWAP",
        )
    )


def _event(journal: PostgresJournal, message: str) -> None:
    journal.enqueue_service_event(
        run_id=RUN_ID,
        strategy_name="random_baseline_v1",
        event_type="test_event",
        message=message,
    )


def test_connects_with_keepalive_and_timeouts() -> None:
    db = FakeDb()
    journal = _journal(db)
    assert _start(journal) == RUN_ID
    journal.stop()
    assert db.last_kwargs["keepalives"] == 1
    assert db.last_kwargs["connect_timeout"] > 0
    assert db.last_kwargs["tcp_user_timeout"] > 0
    assert "options" not in db.last_kwargs


def test_connection_loss_reconnects_and_replays_uncommitted_events() -> None:
    db = FakeDb()
    journal = _journal(db)
    _start(journal)

    db.fail_next_commit = True
    for i in range(3):
        _event(journal, f"msg-{i}")
    _wait_until(lambda: len(db.event_messages()) == 3)

    health = journal.health_snapshot()
    journal.stop()

    assert db.event_messages() == ["msg-0", "msg-1", "msg-2"]
    assert db.connects == 2
    assert health["worker_alive"] is True
    assert health["connected"] is True
    assert health["reconnects"] == 1
    assert "Connection timed out" in (health["last_error"] or "")


def test_worker_keeps_retrying_until_postgres_is_reachable() -> None:
    db = FakeDb()
    db.connect_failures = 3
    journal = _journal(db)

    assert _start(journal) == RUN_ID
    _event(journal, "after-outage")
    _wait_until(lambda: db.event_messages() == ["after-outage"])
    health = journal.health_snapshot()
    journal.stop()

    assert db.connects == 4
    assert health["connected"] is True
    assert health["disconnected_for_sec"] is None


def test_rejected_event_is_dropped_without_losing_the_rest_of_the_batch() -> None:
    db = FakeDb()
    journal = _journal(db)
    _start(journal)

    db.reject_message = "bad"
    for message in ("ok-1", "bad", "ok-2"):
        _event(journal, message)
    _wait_until(lambda: len(db.event_messages()) == 2)
    alive = journal.health_snapshot()["worker_alive"]
    journal.stop()

    assert db.event_messages() == ["ok-1", "ok-2"]
    assert db.connects == 1
    assert alive is True


def test_queue_overflow_is_visible_in_health() -> None:
    db = FakeDb()
    journal = PostgresJournal("postgresql://u:p@db/x", queue_size=1, connect=db.connect)
    gate = threading.Event()
    original_connect = db.connect

    def slow_connect(dsn: str, **kwargs: Any) -> FakeConn:
        gate.wait(5)
        return original_connect(dsn, **kwargs)

    journal._connect = slow_connect
    starter = threading.Thread(target=_start, args=(journal,))
    starter.start()
    _wait_until(lambda: journal.health_snapshot()["worker_alive"])
    _wait_until(lambda: journal.health_snapshot()["queue_size"] == 1)
    _event(journal, "overflow")
    health = journal.health_snapshot()
    gate.set()
    starter.join(5)
    journal.stop()

    assert health["dropped_events"] == 1
    assert health["last_drop_age_sec"] is not None


def test_health_file_lists_running_journals() -> None:
    db = FakeDb()
    journal = _journal(db)
    _start(journal)
    write_journal_health_file()
    report = json.loads(journal_health_file().read_text(encoding="utf-8"))
    journal.stop()

    assert evaluate(report, now=time.time()) == []
    [entry] = [j for j in report["journals"] if j["run_id"] == RUN_ID]
    assert entry["worker_alive"] is True
    assert entry["connected"] is True


def _report(**journal: Any) -> dict[str, Any]:
    base = {
        "run_id": RUN_ID,
        "worker_alive": True,
        "connected": True,
        "disconnected_for_sec": None,
        "loop_age_sec": 0.1,
        "last_drop_age_sec": None,
        "dropped_events": 0,
        "last_error": None,
    }
    base.update(journal)
    return {"written_at": 1000.0, "journals": [base]}


@pytest.mark.parametrize(
    ("journal", "expected"),
    [
        ({}, None),
        ({"worker_alive": False}, "worker thread is dead"),
        ({"loop_age_sec": 600.0}, "stalled"),
        ({"connected": False, "disconnected_for_sec": 300.0}, "unreachable"),
        ({"connected": False, "disconnected_for_sec": 20.0}, None),
        ({"last_drop_age_sec": 10.0, "dropped_events": 5700}, "dropping events"),
        ({"last_drop_age_sec": 3600.0, "dropped_events": 5}, None),
    ],
)
def test_healthcheck_evaluate(journal: dict[str, Any], expected: str | None) -> None:
    problems = evaluate(_report(**journal), now=1010.0)
    if expected is None:
        assert problems == []
    else:
        assert any(expected in p for p in problems), problems


def test_healthcheck_stale_file_is_unhealthy() -> None:
    problems = evaluate(_report(), now=1000.0 + 3600)
    assert problems and "stale" in problems[0]
