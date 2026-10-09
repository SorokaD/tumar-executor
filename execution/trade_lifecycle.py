"""In-memory tracker for one trade cycle: signal → entry attempts → exit → close."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal


Leg = Literal["entry", "exit"]


@dataclass(slots=True)
class TradeLifecycleTracker:
    """Metrics for a single round-trip trade; strategy_id/run_id live on ExecutorStore."""

    signal_id: str | None = None
    entry_signal_id: str | None = None
    position_id: str | None = None
    entry_exchange_ord_id: str | None = None
    entry_cl_ord_id: str | None = None
    exit_exchange_ord_id: str | None = None
    exit_cl_ord_id: str | None = None
    entry_order_type: str = "post_only"
    exit_order_type: str = "post_only"
    tick_size: Decimal | None = None

    entry_order_count: int = 0
    entry_reprice_count: int = 0
    entry_cancel_count: int = 0
    entry_started_at: datetime | None = None
    entry_filled_at: datetime | None = None
    entry_first_px: float | None = None
    entry_last_px: float | None = None
    entry_touch_px_at_first_submit: float | None = None
    entry_filled_px: float | None = None

    exit_order_count: int = 0
    exit_reprice_count: int = 0
    exit_cancel_count: int = 0
    exit_started_at: datetime | None = None
    exit_filled_at: datetime | None = None
    exit_first_px: float | None = None
    exit_last_px: float | None = None
    exit_touch_px_at_first_submit: float | None = None
    exit_filled_px: float | None = None
    exit_maker_attempts: int = 0
    exit_market_fallback_used: bool = False
    exit_market_fallback_reason: str | None = None
    exit_trigger_reason: str | None = None
    timeout_triggered: bool = False
    close_source: str | None = None
    entry_exchange_ord_ids: list[str] = field(default_factory=list)
    exit_exchange_ord_ids: list[str] = field(default_factory=list)
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def track_order(self, leg: Leg, exchange_ord_id: str | None) -> None:
        """Каждый ордер ноги (включая перевыставленные) — для сбора всех fills/fees."""
        if not exchange_ord_id:
            return
        ids = self.entry_exchange_ord_ids if leg == "entry" else self.exit_exchange_ord_ids
        if exchange_ord_id not in ids:
            ids.append(exchange_ord_id)

    def begin(self, signal_id: str, *, tick_size: Decimal | None = None) -> None:
        self.signal_id = signal_id
        self.entry_signal_id = signal_id
        if tick_size is not None:
            self.tick_size = tick_size

    def bind_position(self, position_id: str) -> None:
        self.position_id = position_id

    def on_entry_submit(self, px: float, *, touch_px: float | None, ts: datetime) -> None:
        self.entry_order_count += 1
        if self.entry_started_at is None:
            self.entry_started_at = ts
            self.entry_first_px = px
            self.entry_touch_px_at_first_submit = touch_px
        self.entry_last_px = px

    def on_reprice(self, leg: Leg, new_px: float, ts: datetime) -> None:
        if leg == "entry":
            self.entry_reprice_count += 1
            self.entry_cancel_count += 1
            self.entry_order_count += 1
            self.entry_last_px = new_px
        else:
            self.exit_reprice_count += 1
            self.exit_cancel_count += 1
            self.exit_order_count += 1
            self.exit_last_px = new_px
        _ = ts

    def on_timeout_cancel(self, leg: Leg) -> None:
        if leg == "entry":
            self.entry_cancel_count += 1
        else:
            self.exit_cancel_count += 1

    def on_entry_fill(
        self,
        px: float,
        *,
        exchange_ord_id: str | None,
        cl_ord_id: str | None,
        ts: datetime,
    ) -> None:
        self.entry_filled_px = px
        self.entry_filled_at = ts
        self.entry_exchange_ord_id = exchange_ord_id
        self.entry_cl_ord_id = cl_ord_id
        self.track_order("entry", exchange_ord_id)

    def on_exit_trigger(self, reason: str) -> None:
        self.exit_trigger_reason = reason
        self.timeout_triggered = reason == "timeout"

    def on_exit_submit(
        self,
        px: float,
        *,
        order_type: str,
        market_fallback: bool,
        ts: datetime,
    ) -> None:
        self.exit_order_type = order_type
        self.exit_order_count += 1
        if self.exit_started_at is None:
            self.exit_started_at = ts
            self.exit_first_px = px
            self.exit_touch_px_at_first_submit = px
        self.exit_last_px = px
        if market_fallback:
            self.exit_market_fallback_used = True
            self.exit_market_fallback_reason = self.exit_trigger_reason

    def on_exit_maker_attempt_failed(self) -> None:
        self.exit_maker_attempts += 1

    def on_exit_fill(
        self,
        px: float,
        *,
        exchange_ord_id: str | None,
        cl_ord_id: str | None,
        order_type: str,
        ts: datetime,
        close_source: str,
    ) -> None:
        self.exit_filled_px = px
        self.exit_filled_at = ts
        self.exit_exchange_ord_id = exchange_ord_id
        self.exit_cl_ord_id = cl_ord_id
        self.track_order("exit", exchange_ord_id)
        self.exit_order_type = order_type
        self.close_source = close_source

    def entry_wait_sec(self) -> float | None:
        if self.entry_started_at is None or self.entry_filled_at is None:
            return None
        return (self.entry_filled_at - self.entry_started_at).total_seconds()

    def exit_wait_sec(self) -> float | None:
        if self.exit_started_at is None or self.exit_filled_at is None:
            return None
        return (self.exit_filled_at - self.exit_started_at).total_seconds()

    def _slippage_ticks(self, *, filled_px: float | None, touch_px: float | None, leg: Leg, side: str) -> float | None:
        if filled_px is None or touch_px is None or self.tick_size is None or self.tick_size == 0:
            return None
        tick = float(self.tick_size)
        if leg == "entry":
            if side == "long":
                return (filled_px - touch_px) / tick
            return (touch_px - filled_px) / tick
        if side == "long":
            return (touch_px - filled_px) / tick
        return (filled_px - touch_px) / tick

    def to_metrics_dict(self, *, position_side: str) -> dict[str, Any]:
        final_exit_reason = self.exit_trigger_reason or "unknown"
        return {
            "signal_id": self.signal_id,
            "entry_order_count": self.entry_order_count,
            "entry_reprice_count": self.entry_reprice_count,
            "entry_cancel_count": self.entry_cancel_count,
            "entry_wait_sec": self.entry_wait_sec(),
            "entry_filled_px": self.entry_filled_px,
            "entry_first_px": self.entry_first_px,
            "entry_last_px": self.entry_last_px,
            "entry_slippage_ticks_from_touch": self._slippage_ticks(
                filled_px=self.entry_filled_px,
                touch_px=self.entry_touch_px_at_first_submit,
                leg="entry",
                side=position_side,
            ),
            "exit_order_count": self.exit_order_count,
            "exit_reprice_count": self.exit_reprice_count,
            "exit_cancel_count": self.exit_cancel_count,
            "exit_wait_sec": self.exit_wait_sec(),
            "exit_filled_px": self.exit_filled_px,
            "exit_first_px": self.exit_first_px,
            "exit_last_px": self.exit_last_px,
            "exit_slippage_ticks_from_touch": self._slippage_ticks(
                filled_px=self.exit_filled_px,
                touch_px=self.exit_touch_px_at_first_submit,
                leg="exit",
                side=position_side,
            ),
            "exit_market_fallback_used": self.exit_market_fallback_used,
            "exit_market_fallback_reason": self.exit_market_fallback_reason,
            "exit_maker_attempts": self.exit_maker_attempts,
            "timeout_triggered": self.timeout_triggered,
            "final_exit_reason": final_exit_reason,
            "close_source": self.close_source,
            "entry_exchange_ord_id": self.entry_exchange_ord_id,
            "exit_exchange_ord_id": self.exit_exchange_ord_id,
            "entry_exchange_ord_ids": list(self.entry_exchange_ord_ids),
            "exit_exchange_ord_ids": list(self.exit_exchange_ord_ids),
        }
