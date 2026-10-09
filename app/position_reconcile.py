"""Restore and reconcile position state after restart or exchange drift."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from app.bootstrap import ExecutorContext
from app.position_state import (
    ActiveOrder,
    ActivePosition,
    build_active_position_from_okx,
    is_probable_exit_order,
)
from exchange.okx.models import OkxOrder, OkxPosition
from persistence.executor_store import ExecutorStore
from strategy.contracts import StrategyPlugin

log = logging.getLogger(__name__)


async def bootstrap_position_on_startup(
    *,
    ctx: ExecutorContext,
    store: ExecutorStore,
    strategy: StrategyPlugin,
    strategy_name: str,
    inst_id: str,
    tick_size: Decimal,
    now: datetime,
) -> tuple[ActivePosition | None, ActiveOrder | None]:
    """
    Подхватывает открытую позицию и висящий exit-ордер после рестарта/редеплоя.

    Приоритет: snapshot биржи (exchange as source of truth).
    """
    active_position: ActivePosition | None = None
    active_order: ActiveOrder | None = None

    try:
        exchange_positions = await ctx.exchange.get_positions(inst_id=inst_id)
        open_orders = await ctx.exchange.get_open_orders(inst_id=inst_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("startup reconcile skipped: exchange snapshot failed: %s", exc)
        store.save_service_event(
            strategy_name=strategy_name,
            event_type="startup_reconcile_failed",
            message="failed to fetch exchange snapshot on startup",
            payload={"error": str(exc)},
            level="WARNING",
        )
        return None, None

    okx_inst_positions = _filter_positions(exchange_positions, inst_id)
    sqlite_open = store.list_open_positions(strategy_name=strategy_name)

    if okx_inst_positions:
        okx_pos = okx_inst_positions[0]
        sqlite_id = sqlite_open[0]["position_id"] if sqlite_open else None
        active_position = build_active_position_from_okx(
            okx_pos=okx_pos,
            strategy_name=strategy_name,
            tick_size=tick_size,
            strategy=strategy,
            now=now,
            position_id=sqlite_id,
        )
        if active_position is None:
            log.warning(
                "startup reconcile: exchange reports position but avgPx missing inst_id=%s",
                inst_id,
            )
            return None, None

        log.warning(
            "startup reconcile: restored position id=%s side=%s entry=%s size=%s entry_ts=%s",
            active_position.position_id,
            active_position.side,
            active_position.entry_price,
            active_position.size,
            active_position.entry_ts.isoformat(),
        )
        store.save_position_open(
            position_id=active_position.position_id,
            strategy_name=active_position.strategy_name,
            side=active_position.side,
            entry_price=float(active_position.entry_price),
            entry_ts=active_position.entry_ts.isoformat(),
            size=float(active_position.size),
        )
        store.save_service_event(
            strategy_name=strategy_name,
            event_type="position_reconciled_startup",
            message="restored position from exchange on startup",
            payload={
                "position_id": active_position.position_id,
                "side": active_position.side,
                "entry_price": str(active_position.entry_price),
                "pos_id": okx_pos.pos_id,
            },
            level="WARNING",
        )
        active_order = _adopt_open_exit_order(
            open_orders=open_orders,
            position=active_position,
            strategy_name=strategy_name,
            now=now,
        )
        await _cancel_stale_open_orders(
            ctx=ctx,
            inst_id=inst_id,
            open_orders=open_orders,
            position=active_position,
            adopted_order=active_order,
        )
        return active_position, active_order

    for row in sqlite_open:
        store.close_open_position_reconciled(
            position_id=str(row["position_id"]),
            exit_ts=now.isoformat(),
            exit_reason="reconcile_no_exchange_position",
        )
        store.save_service_event(
            strategy_name=strategy_name,
            event_type="position_closed_reconcile",
            message="closed stale sqlite position: exchange reports flat",
            payload={"position_id": row["position_id"]},
            level="WARNING",
        )

    await _cancel_stale_open_orders(
        ctx=ctx,
        inst_id=inst_id,
        open_orders=open_orders,
        position=None,
        adopted_order=None,
    )
    return None, None


def reconcile_exchange_position_if_needed(
    *,
    active_position: ActivePosition | None,
    okx_inst_positions: list[OkxPosition],
    strategy_name: str,
    tick_size: Decimal,
    strategy: StrategyPlugin,
    now: datetime,
) -> ActivePosition | None:
    """Восстанавливает in-memory позицию из биржи, если локально её нет."""
    if active_position is not None or not okx_inst_positions:
        return active_position
    okx_pos = okx_inst_positions[0]
    restored = build_active_position_from_okx(
        okx_pos=okx_pos,
        strategy_name=strategy_name,
        tick_size=tick_size,
        strategy=strategy,
        now=now,
    )
    if restored is None:
        return None
    log.warning(
        "reconciled active_position from exchange: id=%s side=%s entry=%s size=%s",
        restored.position_id,
        restored.side,
        restored.entry_price,
        restored.size,
    )
    return restored


def _filter_positions(positions: list[OkxPosition], inst_id: str) -> list[OkxPosition]:
    return [p for p in positions if p.inst_id == inst_id]


def _adopt_open_exit_order(
    *,
    open_orders: list[OkxOrder],
    position: ActivePosition,
    strategy_name: str,
    now: datetime,
) -> ActiveOrder | None:
    for order in open_orders:
        if not is_probable_exit_order(position=position, order_side=order.side):
            continue
        log.info(
            "startup reconcile: adopted open exit order ord_id=%s side=%s",
            order.ord_id,
            order.side,
        )
        return ActiveOrder(
            order_id=order.ord_id,
            client_id=order.cl_ord_id,
            strategy_name=strategy_name,
            side=order.side,  # type: ignore[arg-type]
            purpose="exit",
            created_at=now,
            last_reprice_at=now,
            reduce_only=True,
            size=str(position.size),
        )
    return None


async def _cancel_stale_open_orders(
    *,
    ctx: ExecutorContext,
    inst_id: str,
    open_orders: list[OkxOrder],
    position: ActivePosition | None,
    adopted_order: ActiveOrder | None,
) -> None:
    adopted_id = adopted_order.order_id if adopted_order else None
    for order in open_orders:
        if order.ord_id == adopted_id:
            continue
        if position is not None and is_probable_exit_order(
            position=position, order_side=order.side
        ):
            continue
        log.warning(
            "startup reconcile: cancel stale open order ord_id=%s side=%s",
            order.ord_id,
            order.side,
        )
        try:
            await ctx.exchange.cancel_order_by_client_id(
                inst_id=inst_id,
                cl_ord_id=order.cl_ord_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "startup reconcile: failed to cancel stale order ord_id=%s: %s",
                order.ord_id,
                exc,
            )
