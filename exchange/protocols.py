"""
Порты доступа к бирже. Реализации: live OKX, paper, replay mock.

Доменные типы на границе метода — предпочтительнее «сырых» dict.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from domain.models.order import Order
from exchange.okx.models import OkxOrder, OkxPosition, OkxPriceLimits, OkxTicker, OkxFill


class ExchangeClient(Protocol):
    """Минимальный контракт клиента биржи для каркаса."""

    async def place_order(self, order: Order) -> str:
        """Возвращает exchange order id или пустую строку, если модель async иная — уточнится в реализации."""
        ...

    async def cancel_order(self, client_order_id: str) -> None:
        ...

    async def cancel_order_by_client_id(self, *, inst_id: str, cl_ord_id: str) -> None:
        ...

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
        ...

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
        ...

    async def get_order(
        self, *, inst_id: str, ord_id: str | None = None, cl_ord_id: str | None = None
    ) -> OkxOrder | None:
        ...

    async def get_order_fills(
        self,
        *,
        inst_id: str,
        ord_id: str | None = None,
        cl_ord_id: str | None = None,
    ) -> list[OkxFill]:
        ...

    async def get_open_orders(self, *, inst_id: str) -> list[OkxOrder]:
        ...

    async def get_positions(self, *, inst_id: str) -> list[OkxPosition]:
        ...

    async def get_account_snapshot(self) -> dict[str, object]:
        ...

    async def get_ticker_last(self, *, inst_id: str) -> OkxTicker:
        ...

    async def get_tick_size(self, *, inst_id: str) -> Decimal:
        ...

    async def get_contract_value(self, *, inst_id: str) -> Decimal:
        """ctVal * ctMult: сколько базовой валюты в одном контракте (sz)."""
        ...

    async def get_best_bid_ask(self, *, inst_id: str) -> tuple[Decimal, Decimal]:
        ...

    async def get_price_limits(self, *, inst_id: str) -> OkxPriceLimits:
        ...
