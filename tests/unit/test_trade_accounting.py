"""Tests for gross/net PnL and fee estimation."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from accounting.fee_engine import estimate_fees, fees_from_okx_fills
from accounting.pnl_engine import calc_gross_pnl, calc_net_pnl
from app.position_state import ActivePosition
from exchange.okx.models import OkxFill
from execution.trade_finalize import (
    build_trade_result,
    normalize_exit_reason,
    resolve_fee_breakdown,
)
from execution.trade_lifecycle import TradeLifecycleTracker

BTC_CT_VAL = Decimal("0.01")


def _fill(
    *,
    fill_id: str,
    ord_id: str,
    side: str,
    px: str,
    sz: str,
    fee: str,
    exec_type: str = "M",
) -> OkxFill:
    return OkxFill(
        fill_id=fill_id,
        ord_id=ord_id,
        cl_ord_id=f"cl-{ord_id}",
        inst_id="BTC-USDT-SWAP",
        side=side,
        fill_px=Decimal(px),
        fill_sz=Decimal(sz),
        fee=Decimal(fee),
        fee_ccy="USDT",
        exec_type=exec_type,
    )


def test_gross_pnl_long() -> None:
    gross = calc_gross_pnl(
        side="long",
        entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        size=Decimal("2"),
        contract_value=Decimal("1"),
    )
    assert gross == Decimal("20")


def test_gross_pnl_short() -> None:
    gross = calc_gross_pnl(
        side="short",
        entry_price=Decimal("100"),
        exit_price=Decimal("90"),
        size=Decimal("1"),
        contract_value=Decimal("1"),
    )
    assert gross == Decimal("10")


def test_gross_pnl_btc_swap_uses_contract_value() -> None:
    # 0.01 контракта BTC-USDT-SWAP = 0.0001 BTC; движение 700 тиков (70 USDT) -> 0.007 USDT.
    gross = calc_gross_pnl(
        side="long",
        entry_price=Decimal("60000"),
        exit_price=Decimal("60070"),
        size=Decimal("0.01"),
        contract_value=BTC_CT_VAL,
    )
    assert gross == Decimal("0.007")


def test_estimate_fees_btc_swap_uses_contract_value() -> None:
    fees = estimate_fees(
        entry_px=Decimal("60000"),
        exit_px=Decimal("60000"),
        size=Decimal("0.01"),
        contract_value=BTC_CT_VAL,
        entry_order_type="post_only",
        exit_order_type="market",
        fee_rate_maker=Decimal("0.0002"),
        fee_rate_taker=Decimal("0.0005"),
    )
    # notional = 60000 * 0.0001 = 6 USDT
    assert fees.entry_fee == Decimal("0.0012")
    assert fees.exit_fee == Decimal("0.0030")
    assert fees.total_fee == Decimal("0.0042")


def test_net_pnl_after_fees() -> None:
    fees = estimate_fees(
        entry_px=Decimal("100"),
        exit_px=Decimal("110"),
        size=Decimal("1"),
        contract_value=Decimal("1"),
        entry_order_type="post_only",
        exit_order_type="post_only",
        fee_rate_maker=Decimal("0.0002"),
        fee_rate_taker=Decimal("0.0005"),
    )
    gross = Decimal("10")
    net = calc_net_pnl(gross_pnl=gross, total_fee=fees.total_fee)
    assert net < gross
    assert fees.entry_fee > 0
    assert fees.exit_fee > 0
    assert fees.fee_source == "estimated_config"


def test_fees_from_okx_fills() -> None:
    entry = _fill(fill_id="f1", ord_id="o1", side="buy", px="100", sz="1", fee="-0.02")
    exit_fill = _fill(
        fill_id="f2", ord_id="o2", side="sell", px="101", sz="1", fee="-0.0202", exec_type="T"
    )
    breakdown = fees_from_okx_fills(entry_fills=[entry], exit_fills=[exit_fill])
    assert breakdown.fee_source == "okx_fill"
    assert breakdown.entry_liquidity == "maker"
    assert breakdown.exit_liquidity == "taker"
    assert breakdown.total_fee == Decimal("0.0402")


def test_fees_from_okx_fills_rebate_reduces_cost() -> None:
    # OKX: положительный fee — ребейт мейкеру, он уменьшает издержки, а не увеличивает.
    entry = _fill(fill_id="f1", ord_id="o1", side="buy", px="100", sz="1", fee="0.005")
    exit_fill = _fill(
        fill_id="f2", ord_id="o2", side="sell", px="101", sz="1", fee="-0.0202", exec_type="T"
    )
    breakdown = fees_from_okx_fills(entry_fills=[entry], exit_fills=[exit_fill])
    assert breakdown.entry_fee == Decimal("-0.005")
    assert breakdown.total_fee == Decimal("0.0152")


def test_trade_result_market_fallback_metrics() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    pos = ActivePosition(
        position_id="pos-1",
        strategy_name="random_baseline_v1",
        side="long",
        entry_price=Decimal("100"),
        entry_ts=now,
        size=Decimal("1"),
        tp_price=Decimal("110"),
        sl_price=Decimal("90"),
        timeout_at=now,
    )
    lc = TradeLifecycleTracker()
    lc.begin("sig-abc", tick_size=Decimal("0.1"))
    lc.on_exit_trigger("timeout")
    lc.on_exit_submit(99.0, order_type="market", market_fallback=True, ts=now)
    lc.on_exit_fill(99.0, exchange_ord_id="ex-2", cl_ord_id="exit-mkt-1", order_type="market", ts=now, close_source="executor_market_fallback")
    fees = estimate_fees(
        entry_px=Decimal("100"),
        exit_px=Decimal("99"),
        size=Decimal("1"),
        contract_value=Decimal("1"),
        entry_order_type="post_only",
        exit_order_type="market",
        fee_rate_maker=Decimal("0.0002"),
        fee_rate_taker=Decimal("0.0005"),
    )
    trade = build_trade_result(
        position=pos,
        lifecycle=lc,
        exit_price=Decimal("99"),
        closed_at=now,
        inst_id="BTC-USDT-SWAP",
        fees=fees,
        exit_reason="timeout",
        close_source="executor_market_fallback",
        contract_value=Decimal("1"),
    )
    assert trade.exit_reason == "timeout"
    assert trade.close_source == "executor_market_fallback"
    assert trade.signal_id == "sig-abc"
    assert trade.gross_pnl == -1.0
    metrics = trade.execution_metrics or {}
    assert metrics.get("exit_market_fallback_used") is True
    assert metrics.get("timeout_triggered") is True


def test_signal_id_preserved_through_reprices() -> None:
    lc = TradeLifecycleTracker()
    lc.begin("rb-original-signal", tick_size=Decimal("0.1"))
    lc.on_entry_submit(100.0, touch_px=100.0, ts=datetime.now(timezone.utc))
    lc.on_reprice("entry", 100.1, datetime.now(timezone.utc))
    lc.on_reprice("entry", 100.2, datetime.now(timezone.utc))
    assert lc.entry_signal_id == "rb-original-signal"
    assert lc.entry_reprice_count == 2
    assert lc.entry_order_count == 3


def test_lifecycle_tracks_all_order_ids_without_duplicates() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    lc = TradeLifecycleTracker()
    lc.track_order("entry", "e1")
    lc.track_order("entry", "e2")
    lc.track_order("entry", None)
    lc.on_entry_fill(100.0, exchange_ord_id="e2", cl_ord_id="c2", ts=now)
    lc.track_order("exit", "x1")
    lc.on_exit_fill(
        101.0, exchange_ord_id="x2", cl_ord_id="cx2", order_type="post_only", ts=now,
        close_source="executor_maker",
    )
    assert lc.entry_exchange_ord_ids == ["e1", "e2"]
    assert lc.exit_exchange_ord_ids == ["x1", "x2"]


def test_normalize_exit_reason() -> None:
    assert normalize_exit_reason("sync_lost") == "reconcile"
    assert normalize_exit_reason("tp") == "tp"
    assert normalize_exit_reason("maker_exit") == "unknown"


@dataclass
class _FakeFillsExchange:
    fills_by_ord: dict[str, list[OkxFill]]
    calls: list[str | None] = field(default_factory=list)

    async def get_order_fills(
        self, *, inst_id: str, ord_id: str | None = None, cl_ord_id: str | None = None
    ) -> list[OkxFill]:
        _ = inst_id, cl_ord_id
        self.calls.append(ord_id)
        return list(self.fills_by_ord.get(ord_id or "", []))


@dataclass
class _FakeCtx:
    exchange: Any


def _resolve(exchange: _FakeFillsExchange, lc: TradeLifecycleTracker) -> Any:
    return asyncio.run(
        resolve_fee_breakdown(
            _FakeCtx(exchange=exchange),  # type: ignore[arg-type]
            inst_id="BTC-USDT-SWAP",
            lifecycle=lc,
            entry_px=Decimal("100"),
            exit_px=Decimal("101"),
            size=Decimal("2"),
            contract_value=Decimal("1"),
            fee_rate_maker=Decimal("0.0002"),
            fee_rate_taker=Decimal("0.0005"),
        )
    )


def test_resolve_fees_sums_fills_across_repriced_orders() -> None:
    exchange = _FakeFillsExchange(
        fills_by_ord={
            "e1": [_fill(fill_id="f1", ord_id="e1", side="buy", px="100", sz="1", fee="-0.02")],
            "e2": [_fill(fill_id="f2", ord_id="e2", side="buy", px="100", sz="1", fee="-0.02")],
            "x1": [_fill(fill_id="f3", ord_id="x1", side="sell", px="101", sz="2", fee="-0.04")],
        }
    )
    lc = TradeLifecycleTracker()
    lc.track_order("entry", "e1")
    lc.track_order("entry", "e2")
    lc.track_order("exit", "x1")
    fees = _resolve(exchange, lc)
    assert fees.fee_source == "okx_fill"
    assert fees.entry_fee == Decimal("0.04")
    assert fees.exit_fee == Decimal("0.04")
    assert sorted(c for c in exchange.calls if c) == ["e1", "e2", "x1"]


def test_resolve_fees_estimates_when_leg_fills_missing() -> None:
    exchange = _FakeFillsExchange(
        fills_by_ord={
            "e1": [_fill(fill_id="f1", ord_id="e1", side="buy", px="100", sz="2", fee="-0.04")],
        }
    )
    lc = TradeLifecycleTracker()
    lc.track_order("entry", "e1")
    fees = _resolve(exchange, lc)
    assert fees.fee_source == "estimated_config"
    assert None not in exchange.calls
