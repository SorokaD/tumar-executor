"""Движок PnL: gross и net для long/short round-trip."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal


def calc_gross_pnl(
    *,
    side: Literal["long", "short"],
    entry_price: Decimal,
    exit_price: Decimal,
    size: Decimal,
    contract_value: Decimal,
) -> Decimal:
    """
    PnL в валюте котировки. `size` — в контрактах OKX (sz),
    `contract_value` — ctVal * ctMult инструмента (BTC-USDT-SWAP: 0.01 BTC).
    """
    qty = size * contract_value
    if side == "long":
        return (exit_price - entry_price) * qty
    return (entry_price - exit_price) * qty


def calc_net_pnl(*, gross_pnl: Decimal, total_fee: Decimal) -> Decimal:
    return gross_pnl - total_fee
