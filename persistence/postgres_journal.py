"""Фоновая запись в PostgreSQL: очередь + отдельный поток, не блокирует asyncio loop."""

from __future__ import annotations

import json
import logging
import os
import queue
import socket
import tempfile
import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg import errors as pg_errors
from psycopg import sql
from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)

# Без keepalive мёртвое TCP-соединение обнаруживается через десятки минут (ядро ретраит молча).
# Startup-параметры (options=-c statement_timeout) не передаём: PgBouncer их отвергает.
_CONNECT_KWARGS: dict[str, Any] = {
    "connect_timeout": 10,
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
    "tcp_user_timeout": 30_000,
}
_COMMIT_EVERY = 25
_COMMIT_NOW_OPS = frozenset({"start_run", "finish_run"})
_MAX_BATCH_FAILURES = 5
# Обрыв посреди пачки обычно сетевой — даём больше попыток, чем ошибке данных.
_MAX_BATCH_RECONNECTS = 20
_HEALTH_WRITE_INTERVAL_SEC = 5.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class StrategyParams:
    take_profit_ticks: int | None = None
    stop_loss_ticks: int | None = None
    timeout_sec: int | None = None


@dataclass(slots=True)
class RunStartParams:
    runtime_mode: str
    environment_name: str
    strategy_name: str
    inst_id: str
    app_version: str = "0.1.0"
    extra_json: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunFinishParams:
    run_id: int
    status: str
    stop_reason: str | None = None


class PostgresJournal:
    """
    Неблокирующий журнал в PostgreSQL.

    Торговый цикл только кладёт события в очередь (put_nowait).
    Отдельный daemon-поток выполняет INSERT/UPDATE и commit. При обрыве соединения поток
    переподключается и повторяет незакоммиченную пачку; завершается только по stop().
    """

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "okx_exec",
        queue_size: int = 10_000,
        connect: Callable[..., psycopg.Connection[Any]] | None = None,
        reconnect_backoff: tuple[float, float] = (1.0, 30.0),
    ) -> None:
        self._dsn = dsn
        self._schema = schema
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=queue_size)
        self._thread: threading.Thread | None = None
        self._run_id: int | None = None
        self._run_id_ready = threading.Event()
        self._dropped = 0
        self._stop_sentinel = object()
        self._stop_requested = threading.Event()
        self._connect = connect or psycopg.connect
        self._backoff_initial, self._backoff_max = reconnect_backoff
        self._connected = False
        self._disconnected_since: float | None = None
        self._last_loop_at = time.monotonic()
        self._last_commit_at: float | None = None
        self._last_drop_at: float | None = None
        self._last_error: str | None = None
        self._reconnects = 0
        self._batch_failures = 0

    @property
    def run_id(self) -> int | None:
        return self._run_id

    @property
    def dropped_events(self) -> int:
        return self._dropped

    def health_snapshot(self) -> dict[str, Any]:
        """Состояние журнала для healthcheck (читается из другого потока, значения атомарны)."""
        now = time.monotonic()

        def age(ts: float | None) -> float | None:
            return None if ts is None else round(now - ts, 1)

        thread = self._thread
        return {
            "schema": self._schema,
            "run_id": self._run_id,
            "worker_alive": thread is not None and thread.is_alive(),
            "connected": self._connected,
            "disconnected_for_sec": age(self._disconnected_since),
            "loop_age_sec": age(self._last_loop_at),
            "last_commit_age_sec": age(self._last_commit_at),
            "last_drop_age_sec": age(self._last_drop_at),
            "dropped_events": self._dropped,
            "queue_size": self._queue.qsize(),
            "reconnects": self._reconnects,
            "last_error": self._last_error,
        }

    def start_run(self, params: RunStartParams) -> int:
        """Запускает worker-поток и синхронно создаёт executor_runs (один раз при старте loop)."""
        if self._thread is not None:
            raise RuntimeError("PostgresJournal already started")
        self._thread = threading.Thread(target=self._worker, name="pg-journal", daemon=True)
        self._thread.start()
        _register_for_health(self)
        self._enqueue(("start_run", params))
        if not self._run_id_ready.wait(timeout=30.0):
            raise TimeoutError("postgres executor_runs insert timed out after 30s")
        if self._run_id is None:
            raise RuntimeError("postgres run_id was not set after start_run")
        return self._run_id

    def finish_run(self, params: RunFinishParams) -> None:
        self._enqueue(("finish_run", params))

    def stop(self) -> None:
        """Останавливает worker после сброса очереди; если БД недоступна — не дольше ~20 с."""
        try:
            self._queue.put(self._stop_sentinel, timeout=5.0)
        except queue.Full:
            log.error("postgres journal stop: queue full, abandoning %s events", self._queue.qsize())
            self._stop_requested.set()
        if self._thread is not None:
            self._thread.join(timeout=15.0)
            if self._thread.is_alive():
                log.error(
                    "postgres journal stop: worker did not drain in 15s, abandoning %s events",
                    self._queue.qsize(),
                )
                self._stop_requested.set()
                self._thread.join(timeout=5.0)
            self._thread = None
        _unregister_for_health(self)

    def enqueue_signal(
        self,
        *,
        run_id: int,
        signal_id: str,
        strategy_name: str,
        inst_id: str,
        side: str,
        ts_decision: str,
        take_profit_ticks: int | None,
        stop_loss_ticks: int | None,
        timeout_sec: int | None,
        market_snapshot: dict[str, Any] | None = None,
    ) -> None:
        self._enqueue(
            (
                "signal",
                {
                    "run_id": run_id,
                    "signal_id": signal_id,
                    "strategy_name": strategy_name,
                    "inst_id": inst_id,
                    "side": side,
                    "ts_decision": ts_decision,
                    "take_profit_ticks": take_profit_ticks,
                    "stop_loss_ticks": stop_loss_ticks,
                    "timeout_sec": timeout_sec,
                    "market_snapshot": market_snapshot or {},
                },
            )
        )

    def enqueue_execution_attempt(
        self,
        *,
        run_id: int,
        inst_id: str,
        strategy_name: str,
        action_type: str,
        status: str,
        signal_id: str | None = None,
        side: str | None = None,
        skip_reason: str | None = None,
        reject_reason: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        request_payload: dict[str, Any] | None = None,
        response_payload: dict[str, Any] | None = None,
    ) -> None:
        self._enqueue(
            (
                "attempt",
                {
                    "run_id": run_id,
                    "signal_id": signal_id,
                    "inst_id": inst_id,
                    "strategy_name": strategy_name,
                    "action_type": action_type,
                    "side": side,
                    "status": status,
                    "skip_reason": skip_reason,
                    "reject_reason": reject_reason,
                    "error_code": error_code,
                    "error_message": error_message,
                    "request_payload": request_payload or {},
                    "response_payload": response_payload or {},
                },
            )
        )

    def enqueue_order_insert(
        self,
        *,
        run_id: int,
        order_id_local: str,
        strategy_name: str,
        inst_id: str,
        side: str,
        ord_type: str,
        price: float | None,
        size: float,
        status: str,
        exchange_order_id: str | None = None,
        signal_id: str | None = None,
        position_id: str | None = None,
        parent_order_id_local: str | None = None,
        position_action: str | None = None,
        reduce_only: bool = False,
        td_mode: str | None = None,
        ts_created: str | None = None,
    ) -> None:
        self._enqueue(
            (
                "order_insert",
                {
                    "run_id": run_id,
                    "order_id_local": order_id_local,
                    "strategy_name": strategy_name,
                    "inst_id": inst_id,
                    "side": side,
                    "ord_type": ord_type,
                    "price": price,
                    "size": size,
                    "status": status,
                    "exchange_order_id": exchange_order_id,
                    "signal_id": signal_id,
                    "position_id": position_id,
                    "parent_order_id_local": parent_order_id_local,
                    "position_action": position_action,
                    "reduce_only": reduce_only,
                    "td_mode": td_mode,
                    "ts_created": ts_created,
                },
            )
        )

    def enqueue_order_update(
        self,
        *,
        order_id_local: str,
        status: str,
        exchange_order_id: str | None = None,
        price: float | None = None,
        filled_size: float | None = None,
        filled_at: str | None = None,
    ) -> None:
        self._enqueue(
            (
                "order_update",
                {
                    "order_id_local": order_id_local,
                    "status": status,
                    "exchange_order_id": exchange_order_id,
                    "price": price,
                    "filled_size": filled_size,
                    "filled_at": filled_at,
                },
            )
        )

    def enqueue_order_fill(
        self,
        *,
        run_id: int,
        order_id_local: str,
        exchange_order_id: str | None,
        inst_id: str,
        strategy_name: str,
        side: str,
        fill_price: float,
        fill_size: float,
        ts_fill: str,
    ) -> None:
        fill_id = f"{exchange_order_id or order_id_local}-fill"
        self._enqueue(
            (
                "order_fill",
                {
                    "run_id": run_id,
                    "fill_id_exchange": fill_id,
                    "order_id_local": order_id_local,
                    "order_id_exchange": exchange_order_id,
                    "inst_id": inst_id,
                    "strategy_name": strategy_name,
                    "side": side,
                    "fill_price": fill_price,
                    "fill_size": fill_size,
                    "ts_fill": ts_fill,
                },
            )
        )

    def enqueue_position_open(
        self,
        *,
        run_id: int,
        position_id: str,
        strategy_name: str,
        inst_id: str,
        side: str,
        entry_price: float,
        entry_ts: str,
        size: float,
        take_profit_ticks: int | None,
        stop_loss_ticks: int | None,
        timeout_sec: int | None,
        entry_signal_id: str | None = None,
        entry_order_id_local: str | None = None,
    ) -> None:
        self._enqueue(
            (
                "position_open",
                {
                    "run_id": run_id,
                    "position_id": position_id,
                    "strategy_name": strategy_name,
                    "inst_id": inst_id,
                    "side": side,
                    "entry_price": entry_price,
                    "entry_ts": entry_ts,
                    "size": size,
                    "take_profit_ticks": take_profit_ticks,
                    "stop_loss_ticks": stop_loss_ticks,
                    "timeout_sec": timeout_sec,
                    "entry_signal_id": entry_signal_id,
                    "entry_order_id_local": entry_order_id_local,
                },
            )
        )

    def enqueue_position_close(
        self,
        *,
        position_id: str,
        exit_price: float,
        exit_ts: str,
        exit_reason: str,
        status: str = "closed",
    ) -> None:
        self._enqueue(
            (
                "position_close",
                {
                    "position_id": position_id,
                    "exit_price": exit_price,
                    "exit_ts": exit_ts,
                    "exit_reason": exit_reason,
                    "status": status,
                },
            )
        )

    def enqueue_trade_result(
        self,
        *,
        run_id: int,
        trade_id: str,
        position_id: str,
        inst_id: str,
        strategy_name: str,
        side: str,
        entry_price: float,
        exit_price: float,
        qty: float,
        gross_pnl: float,
        fees: float,
        net_pnl: float,
        holding_seconds: float,
        entry_ts: str,
        exit_ts: str,
        exit_reason: str | None,
        entry_signal_id: str | None = None,
        entry_order_id_local: str | None = None,
        exit_order_id_local: str | None = None,
        entry_fee: float = 0.0,
        exit_fee: float = 0.0,
        fee_ccy: str | None = None,
        entry_liquidity: str | None = None,
        exit_liquidity: str | None = None,
        fee_source: str = "missing",
        fee_status: str = "pending",
        close_source: str | None = None,
        execution_metrics: dict[str, Any] | None = None,
    ) -> None:
        metrics = execution_metrics or {}
        payload: dict[str, Any] = {
            "run_id": run_id,
            "trade_id": trade_id,
            "position_id": position_id,
            "inst_id": inst_id,
            "strategy_name": strategy_name,
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "qty": qty,
            "gross_pnl": gross_pnl,
            "fees": fees,
            "net_pnl": net_pnl,
            "holding_seconds": holding_seconds,
            "entry_ts": entry_ts,
            "exit_ts": exit_ts,
            "exit_reason": exit_reason,
            "entry_signal_id": entry_signal_id,
            "entry_order_id_local": entry_order_id_local,
            "exit_order_id_local": exit_order_id_local,
            "entry_fee": entry_fee,
            "exit_fee": exit_fee,
            "fee_ccy": fee_ccy,
            "entry_liquidity": entry_liquidity,
            "exit_liquidity": exit_liquidity,
            "fee_source": fee_source,
            "fee_status": fee_status,
            "close_source": close_source,
            "entry_order_count": metrics.get("entry_order_count"),
            "entry_reprice_count": metrics.get("entry_reprice_count"),
            "entry_cancel_count": metrics.get("entry_cancel_count"),
            "entry_wait_sec": metrics.get("entry_wait_sec"),
            "entry_filled_px": metrics.get("entry_filled_px"),
            "entry_first_px": metrics.get("entry_first_px"),
            "entry_last_px": metrics.get("entry_last_px"),
            "entry_slippage_ticks": metrics.get("entry_slippage_ticks_from_touch"),
            "exit_order_count": metrics.get("exit_order_count"),
            "exit_reprice_count": metrics.get("exit_reprice_count"),
            "exit_cancel_count": metrics.get("exit_cancel_count"),
            "exit_wait_sec": metrics.get("exit_wait_sec"),
            "exit_filled_px": metrics.get("exit_filled_px"),
            "exit_first_px": metrics.get("exit_first_px"),
            "exit_last_px": metrics.get("exit_last_px"),
            "exit_slippage_ticks": metrics.get("exit_slippage_ticks_from_touch"),
            "exit_market_fallback_used": metrics.get("exit_market_fallback_used"),
            "exit_market_fallback_reason": metrics.get("exit_market_fallback_reason"),
            "exit_maker_attempts": metrics.get("exit_maker_attempts"),
            "timeout_triggered": metrics.get("timeout_triggered"),
            "final_exit_reason": metrics.get("final_exit_reason"),
            "extra_json": metrics,
        }
        self._enqueue(("trade_result", payload))

    def enqueue_service_event(
        self,
        *,
        run_id: int,
        strategy_name: str,
        event_type: str,
        message: str,
        level: str = "INFO",
        inst_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._enqueue(
            (
                "service_event",
                {
                    "run_id": run_id,
                    "strategy_name": strategy_name,
                    "event_type": event_type,
                    "message": message,
                    "level": level,
                    "inst_id": inst_id,
                    "payload": payload or {},
                },
            )
        )

    def enqueue_reconciliation(
        self,
        *,
        run_id: int,
        inst_id: str,
        strategy_name: str | None,
        severity: str,
        mismatch_type: str,
        message: str,
        local_entity_type: str | None = None,
        local_entity_id: str | None = None,
        exchange_entity_id: str | None = None,
        resolution_status: str = "detected",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._enqueue(
            (
                "reconciliation",
                {
                    "run_id": run_id,
                    "inst_id": inst_id,
                    "strategy_name": strategy_name,
                    "severity": severity,
                    "mismatch_type": mismatch_type,
                    "message": message,
                    "local_entity_type": local_entity_type,
                    "local_entity_id": local_entity_id,
                    "exchange_entity_id": exchange_entity_id,
                    "resolution_status": resolution_status,
                    "payload": payload or {},
                },
            )
        )

    def _enqueue(self, item: Any) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self._dropped += 1
            self._last_drop_at = time.monotonic()
            if self._dropped == 1 or self._dropped % 100 == 0:
                log.warning("postgres journal queue full, dropped_events=%s", self._dropped)

    def _qi(self, table: str) -> sql.Composed:
        """Qualified identifier schema.table (PgBouncer-safe, no search_path)."""
        return sql.SQL("{}.{}").format(
            sql.Identifier(self._schema),
            sql.Identifier(table),
        )

    def _worker(self) -> None:
        conn: psycopg.Connection[Any] | None = None
        # Отправлено в текущую транзакцию, но не закоммичено: при обрыве повторяется целиком.
        batch: list[tuple[str, Any]] = []
        stopping = False
        try:
            while not self._stop_requested.is_set():
                self._last_loop_at = time.monotonic()
                if conn is None:
                    conn = self._connect_with_retry()
                    if conn is None:
                        break
                    if batch and not self._replay(conn, batch):
                        conn = self._drop_connection(conn, "replay", batch)
                        continue

                item: tuple[str, Any] | None = None
                if not stopping:
                    try:
                        got = self._queue.get(timeout=0.1)
                    except queue.Empty:
                        got = None
                    if got is self._stop_sentinel:
                        stopping = True
                    else:
                        item = got

                in_batch = False
                try:
                    if item is not None:
                        self._dispatch(conn, item)
                        batch.append(item)
                        in_batch = True
                    if batch and (
                        item is None or len(batch) >= _COMMIT_EVERY or item[0] in _COMMIT_NOW_OPS
                    ):
                        self._commit(conn, batch)
                except Exception as exc:  # noqa: BLE001 — поток журнала не должен умирать
                    op = item[0] if item is not None else "commit"
                    self._note_error(exc)
                    if _is_connection_error(conn, exc):
                        if item is not None and not in_batch:
                            batch.append(item)
                        conn = self._drop_connection(conn, op, batch)
                        continue
                    log.exception("postgres journal write failed: op=%s", op)
                    conn = self._recover_after_write_error(conn, batch)
                    continue

                if stopping and not batch:
                    break
        except Exception:  # noqa: BLE001
            log.exception("postgres journal worker crashed")
        finally:
            if batch or not self._queue.empty():
                log.error(
                    "postgres journal stopped with unsaved events: batch=%s queued=%s",
                    len(batch),
                    self._queue.qsize(),
                )
            self._close_quietly(conn)
            self._connected = False

    def _connect_with_retry(self) -> psycopg.Connection[Any] | None:
        delay = self._backoff_initial
        while not self._stop_requested.is_set():
            self._last_loop_at = time.monotonic()
            try:
                conn = self._connect(self._dsn, autocommit=False, **_CONNECT_KWARGS)
            except Exception as exc:  # noqa: BLE001
                self._note_error(exc)
                if self._disconnected_since is None:
                    self._disconnected_since = time.monotonic()
                log.warning("postgres journal connect failed, retry in %.1fs: %s", delay, exc)
                self._stop_requested.wait(delay)
                delay = min(delay * 2, self._backoff_max)
                continue
            if self._disconnected_since is not None:
                log.info(
                    "postgres journal connected after %.1fs offline (reconnects=%s)",
                    time.monotonic() - self._disconnected_since,
                    self._reconnects,
                )
            self._connected = True
            self._disconnected_since = None
            return conn
        return None

    def _drop_connection(
        self,
        conn: psycopg.Connection[Any] | None,
        op: str,
        batch: list[tuple[str, Any]] | None = None,
    ) -> None:
        log.warning(
            "postgres journal connection lost: op=%s error=%s; reconnecting",
            op,
            self._last_error,
        )
        self._close_quietly(conn)
        self._connected = False
        if self._disconnected_since is None:
            self._disconnected_since = time.monotonic()
        self._reconnects += 1
        if batch:
            self._batch_failures += 1
            if self._batch_failures >= _MAX_BATCH_RECONNECTS:
                log.error(
                    "postgres journal: batch lost connection %s times, dropping %s events: ops=%s",
                    self._batch_failures,
                    len(batch),
                    [o for o, _ in batch],
                )
                batch.clear()
                self._batch_failures = 0
        self._stop_requested.wait(self._backoff_initial)
        return None

    def _commit(self, conn: psycopg.Connection[Any], batch: list[tuple[str, Any]]) -> None:
        conn.commit()
        batch.clear()
        self._batch_failures = 0
        self._last_commit_at = time.monotonic()
        if self._run_id is not None:
            self._run_id_ready.set()

    def _replay(self, conn: psycopg.Connection[Any], batch: list[tuple[str, Any]]) -> bool:
        """Повторяет пачку в новой транзакции. False — соединение снова потеряно."""
        while True:
            current: tuple[str, Any] | None = None
            try:
                for current in batch:
                    self._dispatch(conn, current)
                return True
            except Exception as exc:  # noqa: BLE001
                self._note_error(exc)
                if _is_connection_error(conn, exc):
                    return False
                log.exception(
                    "postgres journal replay: event rejected, dropped: op=%s",
                    current[0] if current else None,
                )
                if current is not None:
                    batch.remove(current)
                try:
                    conn.rollback()
                except Exception as rb_exc:  # noqa: BLE001
                    self._note_error(rb_exc)
                    return False

    def _recover_after_write_error(
        self, conn: psycopg.Connection[Any], batch: list[tuple[str, Any]]
    ) -> psycopg.Connection[Any] | None:
        """Ошибка данных: событие уже выброшено (не попало в batch), остальную пачку повторяем."""
        self._batch_failures += 1
        if self._batch_failures >= _MAX_BATCH_FAILURES and batch:
            log.error(
                "postgres journal: batch failed %s times, dropping %s events: ops=%s",
                self._batch_failures,
                len(batch),
                [op for op, _ in batch],
            )
            batch.clear()
            self._batch_failures = 0
        try:
            conn.rollback()
        except Exception as exc:  # noqa: BLE001
            self._note_error(exc)
            return self._drop_connection(conn, "rollback", batch)
        if batch and not self._replay(conn, batch):
            return self._drop_connection(conn, "replay", batch)
        return conn

    def _note_error(self, exc: BaseException) -> None:
        self._last_error = f"{type(exc).__name__}: {exc}".strip()[:300]

    @staticmethod
    def _close_quietly(conn: psycopg.Connection[Any] | None) -> None:
        if conn is None:
            return
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            log.debug("postgres journal: close failed", exc_info=True)

    def _dispatch(self, conn: psycopg.Connection[Any], item: tuple[str, Any]) -> None:
        op, payload = item
        if op == "start_run":
            self._run_id = self._insert_run(conn, payload)
            return
        if op == "finish_run":
            self._finish_run(conn, payload)
            return
        if op == "signal":
            self._insert_signal(conn, payload)
        elif op == "attempt":
            self._insert_attempt(conn, payload)
        elif op == "order_insert":
            self._insert_order(conn, payload)
        elif op == "order_update":
            self._update_order(conn, payload)
        elif op == "order_fill":
            self._insert_fill(conn, payload)
        elif op == "position_open":
            self._upsert_position_open(conn, payload)
        elif op == "position_close":
            self._update_position_close(conn, payload)
        elif op == "trade_result":
            self._insert_trade_result(conn, payload)
        elif op == "service_event":
            self._insert_service_event(conn, payload)
        elif op == "reconciliation":
            self._insert_reconciliation(conn, payload)
        else:
            log.warning("unknown postgres journal op: %s", op)

    def _insert_run(self, conn: psycopg.Connection[Any], params: RunStartParams) -> int:
        row = conn.execute(
            sql.SQL(
                """
            INSERT INTO {} (
                runtime_mode, environment_name, host_name, process_id,
                app_version, strategy_name, inst_id, status, extra_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'running', %s)
            RETURNING run_id
            """
            ).format(self._qi("executor_runs")),
            (
                params.runtime_mode,
                params.environment_name,
                socket.gethostname(),
                os.getpid(),
                params.app_version,
                params.strategy_name,
                params.inst_id,
                Jsonb(params.extra_json),
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("executor_runs insert returned no run_id")
        return int(row[0])

    def _finish_run(self, conn: psycopg.Connection[Any], params: RunFinishParams) -> None:
        conn.execute(
            sql.SQL(
                """
            UPDATE {}
            SET finished_at = now(), status = %s, stop_reason = %s
            WHERE run_id = %s
            """
            ).format(self._qi("executor_runs")),
            (params.status, params.stop_reason, params.run_id),
        )

    def _insert_signal(self, conn: psycopg.Connection[Any], p: dict[str, Any]) -> None:
        conn.execute(
            sql.SQL(
                """
            INSERT INTO {} (
                signal_id, run_id, ts_decision, strategy_name, inst_id, side,
                take_profit_ticks, stop_loss_ticks, timeout_sec, market_snapshot
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ts_decision, signal_id) DO NOTHING
            """
            ).format(self._qi("strategy_signals")),
            (
                p["signal_id"],
                p["run_id"],
                p["ts_decision"],
                p["strategy_name"],
                p["inst_id"],
                p["side"],
                p["take_profit_ticks"],
                p["stop_loss_ticks"],
                p["timeout_sec"],
                Jsonb(p["market_snapshot"]),
            ),
        )

    def _insert_attempt(self, conn: psycopg.Connection[Any], p: dict[str, Any]) -> None:
        conn.execute(
            sql.SQL(
                """
            INSERT INTO {} (
                run_id, signal_id, inst_id, strategy_name, action_type, side,
                status, skip_reason, reject_reason, error_code, error_message,
                request_payload, response_payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            ).format(self._qi("execution_attempts")),
            (
                p["run_id"],
                p["signal_id"],
                p["inst_id"],
                p["strategy_name"],
                p["action_type"],
                p["side"],
                p["status"],
                p["skip_reason"],
                p["reject_reason"],
                p["error_code"],
                p["error_message"],
                Jsonb(p["request_payload"]),
                Jsonb(p["response_payload"]),
            ),
        )

    def _insert_order(self, conn: psycopg.Connection[Any], p: dict[str, Any]) -> None:
        ts_created = p.get("ts_created") or _utc_now().isoformat()
        conn.execute(
            sql.SQL(
                """
            INSERT INTO {} (
                order_id_local, run_id, signal_id, position_id, inst_id, strategy_name,
                order_id_exchange, cl_ord_id, parent_order_id_local, side, position_action,
                ord_type, td_mode, reduce_only, price, size, status, ts_created, ts_submitted
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            ).format(self._qi("orders")),
            (
                p["order_id_local"],
                p["run_id"],
                p.get("signal_id"),
                p.get("position_id"),
                p["inst_id"],
                p["strategy_name"],
                p.get("exchange_order_id"),
                p["order_id_local"],
                p.get("parent_order_id_local"),
                p["side"],
                p.get("position_action"),
                p["ord_type"],
                p.get("td_mode"),
                p.get("reduce_only", False),
                p.get("price"),
                p["size"],
                p["status"],
                ts_created,
                ts_created if p["status"] == "submitted" else None,
            ),
        )

    def _update_order(self, conn: psycopg.Connection[Any], p: dict[str, Any]) -> None:
        status = p["status"]
        filled_at = p.get("filled_at")
        conn.execute(
            sql.SQL(
                """
            UPDATE {} SET
                status = %s,
                order_id_exchange = COALESCE(%s, order_id_exchange),
                price = COALESCE(%s, price),
                filled_size = COALESCE(%s, filled_size),
                avg_fill_price = COALESCE(%s, avg_fill_price),
                ts_closed = CASE WHEN %s IN ('filled', 'canceled', 'rejected') THEN COALESCE(%s::timestamptz, now()) ELSE ts_closed END,
                ts_canceled = CASE WHEN %s = 'canceled' THEN COALESCE(%s::timestamptz, now()) ELSE ts_canceled END,
                ts_last_fill = CASE WHEN %s = 'filled' THEN COALESCE(%s::timestamptz, now()) ELSE ts_last_fill END
            WHERE order_id_local = %s
            """
            ).format(self._qi("orders")),
            (
                status,
                p.get("exchange_order_id"),
                p.get("price"),
                p.get("filled_size"),
                p.get("price"),
                status,
                filled_at,
                status,
                filled_at,
                status,
                filled_at,
                p["order_id_local"],
            ),
        )

    def _insert_fill(self, conn: psycopg.Connection[Any], p: dict[str, Any]) -> None:
        conn.execute(
            sql.SQL(
                """
            INSERT INTO {} (
                fill_id_exchange, order_id_local, order_id_exchange, run_id,
                inst_id, strategy_name, side, fill_price, fill_size, ts_fill
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ts_fill, fill_id_exchange, inst_id) DO NOTHING
            """
            ).format(self._qi("order_fills")),
            (
                p["fill_id_exchange"],
                p["order_id_local"],
                p.get("order_id_exchange"),
                p["run_id"],
                p["inst_id"],
                p["strategy_name"],
                p["side"],
                p["fill_price"],
                p["fill_size"],
                p["ts_fill"],
            ),
        )

    def _upsert_position_open(self, conn: psycopg.Connection[Any], p: dict[str, Any]) -> None:
        conn.execute(
            sql.SQL(
                """
            INSERT INTO {} (
                position_id, run_id, inst_id, strategy_name, side, status,
                qty, qty_open, entry_price, entry_ts,
                take_profit_ticks, stop_loss_ticks, timeout_sec,
                entry_signal_id, entry_order_id_local
            )
            VALUES (%s, %s, %s, %s, %s, 'open', %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (position_id) DO UPDATE SET
                status = 'open',
                qty = EXCLUDED.qty,
                qty_open = EXCLUDED.qty_open,
                entry_price = EXCLUDED.entry_price,
                entry_ts = EXCLUDED.entry_ts,
                updated_at = now()
            """
            ).format(self._qi("positions")),
            (
                p["position_id"],
                p["run_id"],
                p["inst_id"],
                p["strategy_name"],
                p["side"],
                p["size"],
                p["size"],
                p["entry_price"],
                p["entry_ts"],
                p["take_profit_ticks"],
                p["stop_loss_ticks"],
                p["timeout_sec"],
                p.get("entry_signal_id"),
                p.get("entry_order_id_local"),
            ),
        )

    def _update_position_close(self, conn: psycopg.Connection[Any], p: dict[str, Any]) -> None:
        conn.execute(
            sql.SQL(
                """
            UPDATE {} SET
                status = %s,
                exit_price = %s,
                exit_ts = %s,
                exit_reason = %s,
                updated_at = now()
            WHERE position_id = %s
            """
            ).format(self._qi("positions")),
            (
                p.get("status", "closed"),
                p["exit_price"],
                p["exit_ts"],
                p["exit_reason"],
                p["position_id"],
            ),
        )

    def _insert_trade_result(self, conn: psycopg.Connection[Any], p: dict[str, Any]) -> None:
        win_flag = p["net_pnl"] > 0
        extra = p.get("extra_json") or {}
        conn.execute(
            sql.SQL(
                """
            INSERT INTO {} (
                trade_id, run_id, position_id, inst_id, strategy_name, side,
                entry_signal_id, entry_order_id_local, exit_order_id_local,
                entry_price, exit_price, qty, gross_pnl, fees_total, net_pnl,
                holding_seconds, entry_ts, exit_ts, exit_reason, win_flag,
                entry_fee, exit_fee, fee_ccy, entry_liquidity, exit_liquidity,
                fee_source, fee_status, close_source,
                entry_order_count, entry_reprice_count, entry_cancel_count, entry_wait_sec,
                entry_filled_px, entry_first_px, entry_last_px, entry_slippage_ticks,
                exit_order_count, exit_reprice_count, exit_cancel_count, exit_wait_sec,
                exit_filled_px, exit_first_px, exit_last_px, exit_slippage_ticks,
                exit_market_fallback_used, exit_market_fallback_reason, exit_maker_attempts,
                timeout_triggered, final_exit_reason, extra_json
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (trade_id) DO NOTHING
            """
            ).format(self._qi("trade_results")),
            (
                p["trade_id"],
                p["run_id"],
                p["position_id"],
                p["inst_id"],
                p["strategy_name"],
                p["side"],
                p.get("entry_signal_id"),
                p.get("entry_order_id_local"),
                p.get("exit_order_id_local"),
                p["entry_price"],
                p["exit_price"],
                p["qty"],
                p["gross_pnl"],
                p["fees"],
                p["net_pnl"],
                p["holding_seconds"],
                p["entry_ts"],
                p["exit_ts"],
                p["exit_reason"],
                win_flag,
                p.get("entry_fee", 0.0),
                p.get("exit_fee", 0.0),
                p.get("fee_ccy"),
                p.get("entry_liquidity"),
                p.get("exit_liquidity"),
                p.get("fee_source", "missing"),
                p.get("fee_status", "pending"),
                p.get("close_source"),
                p.get("entry_order_count"),
                p.get("entry_reprice_count"),
                p.get("entry_cancel_count"),
                p.get("entry_wait_sec"),
                p.get("entry_filled_px"),
                p.get("entry_first_px"),
                p.get("entry_last_px"),
                p.get("entry_slippage_ticks"),
                p.get("exit_order_count"),
                p.get("exit_reprice_count"),
                p.get("exit_cancel_count"),
                p.get("exit_wait_sec"),
                p.get("exit_filled_px"),
                p.get("exit_first_px"),
                p.get("exit_last_px"),
                p.get("exit_slippage_ticks"),
                p.get("exit_market_fallback_used"),
                p.get("exit_market_fallback_reason"),
                p.get("exit_maker_attempts"),
                p.get("timeout_triggered"),
                p.get("final_exit_reason"),
                json.dumps(extra),
            ),
        )

    def _insert_service_event(self, conn: psycopg.Connection[Any], p: dict[str, Any]) -> None:
        severity = _level_to_severity(p.get("level", "INFO"))
        conn.execute(
            sql.SQL(
                """
            INSERT INTO {} (
                run_id, severity, event_type, strategy_name, inst_id, message, payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            ).format(self._qi("service_events")),
            (
                p["run_id"],
                severity,
                p["event_type"],
                p["strategy_name"],
                p.get("inst_id"),
                p["message"],
                Jsonb(p["payload"]),
            ),
        )

    def _insert_reconciliation(self, conn: psycopg.Connection[Any], p: dict[str, Any]) -> None:
        conn.execute(
            sql.SQL(
                """
            INSERT INTO {} (
                run_id, inst_id, strategy_name, severity, mismatch_type,
                local_entity_type, local_entity_id, exchange_entity_id,
                resolution_status, message, payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            ).format(self._qi("reconciliation_events")),
            (
                p["run_id"],
                p["inst_id"],
                p.get("strategy_name"),
                p["severity"],
                p["mismatch_type"],
                p.get("local_entity_type"),
                p.get("local_entity_id"),
                p.get("exchange_entity_id"),
                p.get("resolution_status", "detected"),
                p["message"],
                Jsonb(p["payload"]),
            ),
        )


def _level_to_severity(level: str) -> str:
    normalized = level.upper()
    if normalized in {"ERROR", "CRITICAL"}:
        return "error"
    if normalized == "WARNING":
        return "warning"
    return "info"


def _is_connection_error(conn: psycopg.Connection[Any] | None, exc: BaseException) -> bool:
    if conn is None or conn.closed or conn.broken:
        return True
    # QueryCanceled — тоже OperationalError, но соединение живо: это ошибка конкретного запроса.
    return isinstance(exc, psycopg.OperationalError) and not isinstance(
        exc, pg_errors.QueryCanceled
    )


# --- health file для docker healthcheck (app/healthcheck.py) ---------------------------------

_health_lock = threading.Lock()
_health_journals: weakref.WeakSet[PostgresJournal] = weakref.WeakSet()
_health_writer: threading.Thread | None = None


def journal_health_file() -> Path:
    override = os.environ.get("OKX_HFT_JOURNAL_HEALTH_FILE")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "okx_exec_journal_health.json"


def clear_journal_health_file() -> None:
    """Вызывается при старте торгового процесса: файл от прошлого запуска не должен считаться живым."""
    journal_health_file().unlink(missing_ok=True)


def write_journal_health_file() -> None:
    with _health_lock:
        journals = list(_health_journals)
    report = {
        "written_at": time.time(),
        "pid": os.getpid(),
        "journals": [j.health_snapshot() for j in journals],
    }
    path = journal_health_file()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(report), encoding="utf-8")
    os.replace(tmp, path)


def _health_writer_loop() -> None:
    while True:
        try:
            write_journal_health_file()
        except Exception:  # noqa: BLE001
            log.exception("postgres journal: health file write failed")
        time.sleep(_HEALTH_WRITE_INTERVAL_SEC)


def _register_for_health(journal: PostgresJournal) -> None:
    global _health_writer
    with _health_lock:
        _health_journals.add(journal)
        if _health_writer is None:
            _health_writer = threading.Thread(
                target=_health_writer_loop, name="pg-journal-health", daemon=True
            )
            _health_writer.start()


def _unregister_for_health(journal: PostgresJournal) -> None:
    with _health_lock:
        _health_journals.discard(journal)
