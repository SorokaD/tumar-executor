"""Расчёт торговых комиссий: фактические OKX fills или оценка по конфигу."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from exchange.okx.models import OkxFill


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    entry_fee: Decimal
    exit_fee: Decimal
    total_fee: Decimal
    fee_ccy: str | None
    entry_liquidity: str | None
    exit_liquidity: str | None
    entry_avg_px: Decimal | None
    exit_avg_px: Decimal | None
    fee_source: str
    fee_status: str


def _liquidity_label(exec_type: str | None) -> str | None:
    if exec_type in {"M", "maker"}:
        return "maker"
    if exec_type in {"T", "taker"}:
        return "taker"
    return None


def _sum_fills(fills: list[OkxFill]) -> tuple[Decimal, str | None, str | None, Decimal | None]:
    if not fills:
        return Decimal("0"), None, None, None
    total_fee = Decimal("0")
    fee_ccy: str | None = None
    liquidity_counts: dict[str, int] = {}
    notional = Decimal("0")
    size = Decimal("0")
    for fill in fills:
        # OKX: отрицательный fee — списание, положительный — ребейт.
        total_fee -= fill.fee
        fee_ccy = fee_ccy or fill.fee_ccy
        label = _liquidity_label(fill.exec_type)
        if label:
            liquidity_counts[label] = liquidity_counts.get(label, 0) + 1
        notional += fill.fill_px * fill.fill_sz
        size += fill.fill_sz
    liquidity: str | None = None
    if liquidity_counts:
        liquidity = max(liquidity_counts, key=liquidity_counts.get)
    avg_px = (notional / size) if size > 0 else None
    return total_fee, fee_ccy, liquidity, avg_px


def fees_from_okx_fills(
    *,
    entry_fills: list[OkxFill],
    exit_fills: list[OkxFill],
) -> FeeBreakdown:
    entry_fee, fee_ccy, entry_liq, entry_avg = _sum_fills(entry_fills)
    exit_fee, exit_ccy, exit_liq, exit_avg = _sum_fills(exit_fills)
    return FeeBreakdown(
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        total_fee=entry_fee + exit_fee,
        fee_ccy=fee_ccy or exit_ccy,
        entry_liquidity=entry_liq,
        exit_liquidity=exit_liq,
        entry_avg_px=entry_avg,
        exit_avg_px=exit_avg,
        fee_source="okx_fill",
        fee_status="ok",
    )


def estimate_fees(
    *,
    entry_px: Decimal,
    exit_px: Decimal,
    size: Decimal,
    contract_value: Decimal,
    entry_order_type: str,
    exit_order_type: str,
    fee_rate_maker: Decimal,
    fee_rate_taker: Decimal,
    fee_ccy: str = "USDT",
) -> FeeBreakdown:
    entry_rate = fee_rate_taker if entry_order_type == "market" else fee_rate_maker
    exit_rate = fee_rate_taker if exit_order_type == "market" else fee_rate_maker
    qty = size * contract_value
    entry_notional = entry_px * qty
    exit_notional = exit_px * qty
    entry_fee = entry_notional * entry_rate
    exit_fee = exit_notional * exit_rate
    entry_liq = "taker" if entry_order_type == "market" else "maker"
    exit_liq = "taker" if exit_order_type == "market" else "maker"
    return FeeBreakdown(
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        total_fee=entry_fee + exit_fee,
        fee_ccy=fee_ccy,
        entry_liquidity=entry_liq,
        exit_liquidity=exit_liq,
        entry_avg_px=entry_px,
        exit_avg_px=exit_px,
        fee_source="estimated_config",
        fee_status="ok",
    )


def missing_fees() -> FeeBreakdown:
    return FeeBreakdown(
        entry_fee=Decimal("0"),
        exit_fee=Decimal("0"),
        total_fee=Decimal("0"),
        fee_ccy=None,
        entry_liquidity=None,
        exit_liquidity=None,
        entry_avg_px=None,
        exit_avg_px=None,
        fee_source="missing",
        fee_status="pending",
    )
