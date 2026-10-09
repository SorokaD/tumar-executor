"""Торговый цикл на фейковой бирже: частичные исполнения entry/exit."""
from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.bootstrap import ExecutorContext
from app.orchestrator import run_baseline_loop_with_limits
from config.settings import Settings
from config.strategy_config import StrategyDeploymentConfig, StrategyExecutionConfig
from exchange.okx.models import OkxFill, OkxOrder, OkxPosition, OkxPriceLimits, OkxTicker
from services.clock import SystemClock

INST = "BTC-USDT-SWAP"


@dataclass
class _Placed:
    ord_id: str
    side: str
    size: str
    reduce_only: bool


class _FakeExchange:
    """Цена неподвижна: bid 59999.9 / ask 60000.1. Исполнения задаются сценарием."""

    def __init__(
        self,
        *,
        fill_on_place: set[int] | None = None,
        fill_on_cancel: dict[int, Decimal] | None = None,
    ) -> None:
        self.price = Decimal("60000")
        self.fill_on_place = fill_on_place or set()
        self.fill_on_cancel = fill_on_cancel or {}
        self.placed: list[_Placed] = []
        self.orders: dict[str, OkxOrder] = {}
        self._index_by_cl: dict[str, int] = {}

    def _add_order(
        self, *, side: str, size: str, px: Decimal, cl_ord_id: str, reduce_only: bool
    ) -> str:
        idx = len(self.placed)
        ord_id = f"o{idx}"
        filled = idx in self.fill_on_place
        self.orders[ord_id] = OkxOrder(
            ord_id=ord_id,
            cl_ord_id=cl_ord_id,
            state="filled" if filled else "live",
            side=side,
            px=px,
            avg_px=px if filled else None,
            sz=Decimal(size),
            fill_sz=Decimal(size) if filled else Decimal("0"),
        )
        self.placed.append(_Placed(ord_id=ord_id, side=side, size=size, reduce_only=reduce_only))
        self._index_by_cl[cl_ord_id] = idx
        return ord_id

    async def place_limit_post_only(
        self,
        *,
        side: str,
        size: str,
        price: Decimal,
        cl_ord_id: str,
        reduce_only: bool = False,
        inst_id: str | None = None,
        td_mode: str | None = None,
    ) -> str:
        _ = inst_id, td_mode
        return self._add_order(
            side=side, size=size, px=price, cl_ord_id=cl_ord_id, reduce_only=reduce_only
        )

    async def place_market_order(
        self,
        *,
        side: str,
        size: str,
        cl_ord_id: str,
        reduce_only: bool = False,
        inst_id: str | None = None,
        td_mode: str | None = None,
    ) -> str:
        _ = inst_id, td_mode
        self.fill_on_place.add(len(self.placed))
        return self._add_order(
            side=side, size=size, px=self.price, cl_ord_id=cl_ord_id, reduce_only=reduce_only
        )

    async def cancel_order_by_client_id(self, *, inst_id: str, cl_ord_id: str) -> None:
        _ = inst_id
        idx = self._index_by_cl[cl_ord_id]
        order = self.orders[f"o{idx}"]
        if order.state != "live":
            raise RuntimeError("OKX error sCode=51400 order already final")
        fill = self.fill_on_cancel.get(idx, Decimal("0"))
        self.orders[order.ord_id] = OkxOrder(
            ord_id=order.ord_id,
            cl_ord_id=order.cl_ord_id,
            state="canceled",
            side=order.side,
            px=order.px,
            avg_px=order.px if fill > 0 else None,
            sz=order.sz,
            fill_sz=fill,
        )

    async def get_order(
        self, *, inst_id: str, ord_id: str | None = None, cl_ord_id: str | None = None
    ) -> OkxOrder | None:
        _ = inst_id, cl_ord_id
        return self.orders.get(ord_id or "")

    async def get_order_fills(
        self, *, inst_id: str, ord_id: str | None = None, cl_ord_id: str | None = None
    ) -> list[OkxFill]:
        _ = inst_id, ord_id, cl_ord_id
        return []

    async def get_open_orders(self, *, inst_id: str) -> list[OkxOrder]:
        _ = inst_id
        return []

    async def get_positions(self, *, inst_id: str) -> list[OkxPosition]:
        _ = inst_id
        return []

    async def get_account_snapshot(self) -> dict[str, object]:
        return {}

    async def get_ticker_last(self, *, inst_id: str) -> OkxTicker:
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return OkxTicker(inst_id=inst_id, last=self.price, ts_ms=ts_ms)

    async def get_tick_size(self, *, inst_id: str) -> Decimal:
        _ = inst_id
        return Decimal("0.1")

    async def get_contract_value(self, *, inst_id: str) -> Decimal:
        _ = inst_id
        return Decimal("0.01")

    async def get_best_bid_ask(self, *, inst_id: str) -> tuple[Decimal, Decimal]:
        _ = inst_id
        return self.price - Decimal("0.1"), self.price + Decimal("0.1")

    async def get_price_limits(self, *, inst_id: str) -> OkxPriceLimits:
        _ = inst_id
        return OkxPriceLimits(buy_lmt=self.price * 2, sell_lmt=self.price / 2)


@pytest.fixture
def sqlite_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "loop.sqlite3"
    monkeypatch.setenv("OKX_SQLITE_PATH", str(path))
    monkeypatch.setenv("OKX_HFT_POSTGRES_ENABLED", "0")
    monkeypatch.setenv("OKX_LOOP_SLEEP_SEC", "0")
    monkeypatch.setenv("OKX_HFT_RUNTIME_MODE", "replay")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_LINK", raising=False)
    return path


def _run(exchange: _FakeExchange, deployment: StrategyDeploymentConfig, max_loops: int) -> None:
    ctx = ExecutorContext(
        settings=Settings(),
        clock=SystemClock(),
        exchange=exchange,  # type: ignore[arg-type]
        deployment=deployment,
    )
    asyncio.run(run_baseline_loop_with_limits(ctx, deployment=deployment, max_loops=max_loops))


def _rows(path: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(path) as conn:
        return list(conn.execute(sql).fetchall())


def test_entry_partial_fill_before_cancel_opens_position_with_filled_size(
    sqlite_path: Path,
) -> None:
    deployment = StrategyDeploymentConfig(
        strategy_name="random_baseline_v1",
        inst_id=INST,
        execution=StrategyExecutionConfig(order_size="0.03", maker_reprice_sec=0),
        params={"random_seed": 7, "timeout_sec": 3600},
    )
    exchange = _FakeExchange(fill_on_cancel={0: Decimal("0.01")})

    _run(exchange, deployment, max_loops=3)

    # Без повторного входа на остаток и без второго ордера после частичного fill.
    assert len(exchange.placed) == 1
    assert _rows(sqlite_path, "SELECT size FROM positions") == [(0.01,)]


def test_exit_partial_fill_then_remainder_closes_with_vwap(sqlite_path: Path) -> None:
    deployment = StrategyDeploymentConfig(
        strategy_name="random_baseline_v1",
        inst_id=INST,
        execution=StrategyExecutionConfig(order_size="0.02"),
        params={
            "random_seed": 7,
            "timeout_sec": 0,
            "exit_maker_reprice_sec": 0,
            "exit_market_fallback_enabled": False,
        },
    )
    exchange = _FakeExchange(fill_on_place={0, 2}, fill_on_cancel={1: Decimal("0.01")})

    _run(exchange, deployment, max_loops=5)

    assert [(p.size, p.reduce_only) for p in exchange.placed] == [
        ("0.02", False),
        ("0.02", True),
        ("0.01", True),
    ]
    rows = _rows(sqlite_path, "SELECT gross_pnl, size FROM trade_results")
    assert len(rows) == 1
    gross_pnl, size = rows[0]
    # Вход и оба выхода — мейкером по touch: спред 0.2 * 0.02 контракта * ctVal 0.01.
    assert size == pytest.approx(0.02)
    assert gross_pnl == pytest.approx(0.00004)
