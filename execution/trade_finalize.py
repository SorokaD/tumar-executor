"""Сборка trade_result при закрытии позиции (fees + execution metrics)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from accounting.fee_engine import (
    FeeBreakdown,
    estimate_fees,
    fees_from_okx_fills,
)
from accounting.pnl_engine import calc_gross_pnl, calc_net_pnl
from app.position_state import ActivePosition
from exchange.okx.models import OkxFill
from execution.trade_lifecycle import TradeLifecycleTracker
from persistence.sqlite_store import TradeResult

if TYPE_CHECKING:
    from app.bootstrap import ExecutorContext
    from persistence.executor_store import ExecutorStore

log = logging.getLogger(__name__)

_FEE_FETCH_TIMEOUT_SEC = 3.0


def normalize_exit_reason(raw: str | None) -> str:
    if not raw:
        return "unknown"
    mapping = {
        "sync_lost": "reconcile",
        "reconciled": "reconcile",
        "maker_exit": "unknown",
    }
    if raw in mapping:
        return mapping[raw]
    if raw in {"tp", "sl", "timeout", "manual", "shutdown_drain", "reconcile", "external_close", "unknown"}:
        return raw
    return raw


def _leg_order_ids(ids: list[str], last_id: str | None) -> list[str]:
    result = list(ids)
    if last_id and last_id not in result:
        result.append(last_id)
    return result


async def _fetch_leg_fills(exchange: Any, *, inst_id: str, ord_ids: list[str]) -> list[OkxFill]:
    if not ord_ids:
        return []
    batches = await asyncio.gather(
        *(exchange.get_order_fills(inst_id=inst_id, ord_id=oid) for oid in ord_ids)
    )
    return [fill for batch in batches for fill in batch]


async def resolve_fee_breakdown(
    ctx: ExecutorContext,
    *,
    inst_id: str,
    lifecycle: TradeLifecycleTracker,
    entry_px: Decimal,
    exit_px: Decimal,
    size: Decimal,
    contract_value: Decimal,
    fee_rate_maker: Decimal,
    fee_rate_taker: Decimal,
) -> FeeBreakdown:
    """
    Комиссии по фактическим fills всех ордеров сделки (включая перевыставленные).

    Если fills есть не по обеим ногам (рестарт, внешнее закрытие) — оценка по конфигу,
    чтобы не занизить комиссию нулём по недостающей ноге.
    """
    exchange = ctx.exchange

    def _estimate() -> FeeBreakdown:
        return estimate_fees(
            entry_px=entry_px,
            exit_px=exit_px,
            size=size,
            contract_value=contract_value,
            entry_order_type=lifecycle.entry_order_type,
            exit_order_type=lifecycle.exit_order_type,
            fee_rate_maker=fee_rate_maker,
            fee_rate_taker=fee_rate_taker,
        )

    if not hasattr(exchange, "get_order_fills"):
        return _estimate()

    entry_ids = _leg_order_ids(lifecycle.entry_exchange_ord_ids, lifecycle.entry_exchange_ord_id)
    exit_ids = _leg_order_ids(lifecycle.exit_exchange_ord_ids, lifecycle.exit_exchange_ord_id)

    async def _fetch() -> FeeBreakdown:
        entry_fills, exit_fills = await asyncio.gather(
            _fetch_leg_fills(exchange, inst_id=inst_id, ord_ids=entry_ids),
            _fetch_leg_fills(exchange, inst_id=inst_id, ord_ids=exit_ids),
        )
        if entry_fills and exit_fills:
            return fees_from_okx_fills(entry_fills=entry_fills, exit_fills=exit_fills)
        log.info(
            "fills incomplete (entry=%s exit=%s), using estimated_config",
            len(entry_fills),
            len(exit_fills),
        )
        return _estimate()

    try:
        return await asyncio.wait_for(_fetch(), timeout=_FEE_FETCH_TIMEOUT_SEC)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "fee fetch failed, using estimated_config: inst_id=%s entry_ord_ids=%s exit_ord_ids=%s: %s",
            inst_id,
            entry_ids,
            exit_ids,
            exc,
        )
        return _estimate()


def build_trade_result(
    *,
    position: ActivePosition,
    lifecycle: TradeLifecycleTracker,
    exit_price: Decimal,
    closed_at: datetime,
    inst_id: str,
    fees: FeeBreakdown,
    exit_reason: str,
    close_source: str,
    contract_value: Decimal,
) -> TradeResult:
    gross = calc_gross_pnl(
        side=position.side,
        entry_price=position.entry_price,
        exit_price=exit_price,
        size=position.size,
        contract_value=contract_value,
    )
    net = calc_net_pnl(gross_pnl=gross, total_fee=fees.total_fee)
    holding = (closed_at - position.entry_ts).total_seconds()
    metrics = lifecycle.to_metrics_dict(position_side=position.side)
    metrics["close_source"] = close_source
    entry_avg = float(fees.entry_avg_px) if fees.entry_avg_px is not None else float(position.entry_price)
    exit_avg = float(fees.exit_avg_px) if fees.exit_avg_px is not None else float(exit_price)
    return TradeResult(
        position_id=position.position_id,
        strategy_name=position.strategy_name,
        gross_pnl=float(gross),
        fees=float(fees.total_fee),
        net_pnl=float(net),
        holding_seconds=holding,
        entry_fee=float(fees.entry_fee),
        exit_fee=float(fees.exit_fee),
        fee_ccy=fees.fee_ccy,
        entry_liquidity=fees.entry_liquidity,
        exit_liquidity=fees.exit_liquidity,
        entry_avg_px=entry_avg,
        exit_avg_px=exit_avg,
        fee_source=fees.fee_source,
        fee_status=fees.fee_status,
        exit_reason=exit_reason,
        close_source=close_source,
        signal_id=lifecycle.entry_signal_id,
        inst_id=inst_id,
        position_side=position.side,
        size=float(position.size),
        opened_at=position.entry_ts.isoformat(),
        closed_at=closed_at.isoformat(),
        execution_metrics=metrics,
    )


async def finalize_closed_trade(
    *,
    ctx: ExecutorContext,
    store: ExecutorStore,
    position: ActivePosition,
    lifecycle: TradeLifecycleTracker,
    exit_price: Decimal,
    closed_at: datetime,
    inst_id: str,
    fee_rate_maker: Decimal,
    fee_rate_taker: Decimal,
    exit_reason_raw: str | None,
    close_source: str,
    contract_value: Decimal,
) -> TradeResult:
    exit_reason = normalize_exit_reason(lifecycle.exit_trigger_reason or exit_reason_raw)
    fees = await resolve_fee_breakdown(
        ctx,
        inst_id=inst_id,
        lifecycle=lifecycle,
        entry_px=position.entry_price,
        exit_px=exit_price,
        size=position.size,
        contract_value=contract_value,
        fee_rate_maker=fee_rate_maker,
        fee_rate_taker=fee_rate_taker,
    )
    trade = build_trade_result(
        position=position,
        lifecycle=lifecycle,
        exit_price=exit_price,
        closed_at=closed_at,
        inst_id=inst_id,
        fees=fees,
        exit_reason=exit_reason,
        close_source=close_source,
        contract_value=contract_value,
    )
    store.save_position_close(
        position_id=position.position_id,
        exit_price=float(exit_price),
        exit_ts=closed_at.isoformat(),
        exit_reason=exit_reason,
        close_source=close_source,
    )
    store.save_trade_result(trade)
    store.clear_trade_lifecycle()
    return trade


async def finalize_reconciled_close(
    *,
    ctx: ExecutorContext,
    store: ExecutorStore,
    position: ActivePosition,
    lifecycle: TradeLifecycleTracker | None,
    exit_price: Decimal,
    closed_at: datetime,
    inst_id: str,
    fee_rate_maker: Decimal,
    fee_rate_taker: Decimal,
    contract_value: Decimal,
) -> None:
    """Закрытие без fill на бирже (sync_lost / reconcile)."""
    lc = lifecycle or TradeLifecycleTracker()
    lc.close_source = "okx_reconcile"
    lc.exit_trigger_reason = lc.exit_trigger_reason or "reconcile"
    lc.exit_filled_px = float(exit_price)
    lc.exit_filled_at = closed_at
    trade = await finalize_closed_trade(
        ctx=ctx,
        store=store,
        position=position,
        lifecycle=lc,
        exit_price=exit_price,
        closed_at=closed_at,
        inst_id=inst_id,
        fee_rate_maker=fee_rate_maker,
        fee_rate_taker=fee_rate_taker,
        exit_reason_raw="reconcile",
        close_source="okx_reconcile",
        contract_value=contract_value,
    )
    _ = trade
