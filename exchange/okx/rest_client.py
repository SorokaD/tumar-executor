"""Минимальный рабочий OKX v5 REST-клиент для demo MVP."""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config.settings import Settings
from config.strategy_config import StrategyDeploymentConfig
from domain.models.order import Order
from exchange.okx.auth import make_timestamp, sign_okx_request
from exchange.okx.models import OkxFill, OkxOrder, OkxPosition, OkxTicker

log = logging.getLogger(__name__)


def _to_decimal(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(value)


def _parse_ms_timestamp(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


class OkxRestClient:
    """Небольшой async-friendly клиент через asyncio.to_thread + urllib."""

    def __init__(
        self,
        settings: Settings,
        *,
        deployment: StrategyDeploymentConfig | None = None,
    ) -> None:
        self._settings = settings
        self._deployment = deployment
        self._base_url = settings.okx_base_url.rstrip("/")
        self._user_agent = "okx-hft-executor/0.1 (+demo-baseline)"
        if (
            not settings.okx_api_key
            or not settings.okx_api_secret
            or not settings.okx_api_passphrase
        ):
            raise ValueError("OKX credentials are required for real demo trading.")
        log.info(
            "OKX client init: base_url=%s demo_flag=%s runtime_mode=%s user_agent=%s",
            self._base_url,
            settings.okx_flag_demo,
            settings.runtime_mode.value,
            self._user_agent,
        )

    def _default_inst_id(self) -> str:
        if self._deployment is not None:
            return self._deployment.inst_id
        raise ValueError("inst_id is not configured; pass deployment to OkxRestClient")

    def _default_td_mode(self) -> str:
        if self._deployment is not None:
            return self._deployment.execution.td_mode
        return "isolated"

    async def place_order(self, order: Order) -> str:
        side = "buy" if order.side.value == "buy" else "sell"
        return await self.place_market_order(
            side=side,
            size=str(order.quantity),
            cl_ord_id=order.client_order_id,
        )

    async def cancel_order(self, client_order_id: str) -> None:
        await self.cancel_order_by_client_id(
            inst_id=self._default_inst_id(),
            cl_ord_id=client_order_id,
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
        payload: dict[str, Any] = {
            "instId": inst_id or self._default_inst_id(),
            "tdMode": td_mode or self._default_td_mode(),
            "side": side,
            "ordType": "market",
            "sz": size,
            "clOrdId": self._normalize_cl_ord_id(cl_ord_id),
        }
        if reduce_only:
            payload["reduceOnly"] = True

        item = await self._request(
            "POST",
            "/api/v5/trade/order",
            body=payload,
            auth=True,
        )
        return str(item.get("ordId", ""))

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
        payload: dict[str, Any] = {
            "instId": inst_id or self._default_inst_id(),
            "tdMode": td_mode or self._default_td_mode(),
            "side": side,
            "ordType": "post_only",
            "sz": size,
            "px": str(price),
            "clOrdId": self._normalize_cl_ord_id(cl_ord_id),
        }
        if reduce_only:
            payload["reduceOnly"] = True
        item = await self._request(
            "POST",
            "/api/v5/trade/order",
            body=payload,
            auth=True,
        )
        return str(item.get("ordId", ""))

    async def close_position_market(self, *, side: str, size: str, cl_ord_id: str) -> str:
        """Упрощённый market close для smoke-проверок."""
        return await self.place_market_order(
            side=side,
            size=size,
            cl_ord_id=cl_ord_id,
            reduce_only=True,
        )

    async def cancel_order_by_client_id(
        self,
        *,
        inst_id: str,
        cl_ord_id: str,
    ) -> None:
        normalized = self._normalize_cl_ord_id(cl_ord_id)
        await self._request(
            "POST",
            "/api/v5/trade/cancel-order",
            body={"instId": inst_id, "clOrdId": normalized},
            auth=True,
        )

    async def get_order(
        self,
        *,
        inst_id: str,
        ord_id: str | None = None,
        cl_ord_id: str | None = None,
    ) -> OkxOrder | None:
        params: dict[str, str] = {"instId": inst_id}
        if ord_id:
            params["ordId"] = ord_id
        if cl_ord_id:
            params["clOrdId"] = cl_ord_id
        item = await self._request(
            "GET",
            "/api/v5/trade/order",
            params=params,
            auth=True,
            allow_empty=True,
        )
        if not item:
            return None
        return self._parse_order(item)

    async def get_order_fills(
        self,
        *,
        inst_id: str,
        ord_id: str | None = None,
        cl_ord_id: str | None = None,
    ) -> list[OkxFill]:
        params: dict[str, str] = {"instId": inst_id}
        if ord_id:
            params["ordId"] = ord_id
        if cl_ord_id:
            params["clOrdId"] = cl_ord_id
        data = await self._request(
            "GET",
            "/api/v5/trade/fills",
            params=params,
            auth=True,
            expect_list=True,
            allow_empty=True,
        )
        return [self._parse_fill(item) for item in data]

    async def get_open_orders(self, *, inst_id: str) -> list[OkxOrder]:
        data = await self._request(
            "GET",
            "/api/v5/trade/orders-pending",
            params={"instId": inst_id},
            auth=True,
            expect_list=True,
        )
        return [self._parse_order(item) for item in data]

    async def get_positions(self, *, inst_id: str) -> list[OkxPosition]:
        data = await self._request(
            "GET",
            "/api/v5/account/positions",
            params={"instId": inst_id},
            auth=True,
            expect_list=True,
        )
        result: list[OkxPosition] = []
        for item in data:
            pos = _to_decimal(item.get("pos"))
            if pos is None or pos == 0:
                continue
            raw_pos_id = item.get("posId") or item.get("posID")
            pos_id = str(raw_pos_id).strip() if raw_pos_id not in (None, "") else None
            if pos_id == "":
                pos_id = None
            result.append(
                OkxPosition(
                    inst_id=item.get("instId", inst_id),
                    pos=pos,
                    avg_px=_to_decimal(item.get("avgPx")),
                    pos_id=pos_id,
                    c_time_ms=_parse_ms_timestamp(item.get("cTime")),
                )
            )
        return result

    async def get_account_snapshot(self) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/api/v5/account/balance",
            auth=True,
            expect_list=True,
        )
        return data[0] if data else {}

    async def get_ticker_last(self, *, inst_id: str) -> OkxTicker:
        data = await self._request(
            "GET",
            "/api/v5/market/ticker",
            params={"instId": inst_id},
            auth=False,
            expect_list=True,
        )
        if not data:
            raise RuntimeError("Empty ticker data.")
        item = data[0]
        return OkxTicker(
            inst_id=item.get("instId", inst_id),
            last=Decimal(item["last"]),
            ts_ms=int(item.get("ts", "0")),
        )

    async def _get_swap_instrument(self, *, inst_id: str) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/api/v5/public/instruments",
            params={"instType": "SWAP", "instId": inst_id},
            auth=False,
            expect_list=True,
        )
        if not data:
            raise RuntimeError(f"Instrument metadata not found for {inst_id}.")
        target = next((item for item in data if item.get("instId") == inst_id), None)
        if not target:
            raise RuntimeError(f"Instrument {inst_id} not found in SWAP list.")
        return target

    async def get_tick_size(self, *, inst_id: str) -> Decimal:
        target = await self._get_swap_instrument(inst_id=inst_id)
        tick = target.get("tickSz")
        if not tick:
            raise RuntimeError("tickSz not present in instrument metadata.")
        return Decimal(tick)

    async def get_contract_value(self, *, inst_id: str) -> Decimal:
        """Размер одного контракта в базовой валюте: ctVal * ctMult (BTC-USDT-SWAP: 0.01)."""
        target = await self._get_swap_instrument(inst_id=inst_id)
        ct_val = target.get("ctVal")
        if not ct_val:
            raise RuntimeError(f"ctVal not present in instrument metadata for {inst_id}.")
        ct_mult = target.get("ctMult") or "1"
        value = Decimal(str(ct_val)) * Decimal(str(ct_mult))
        if value <= 0:
            raise RuntimeError(f"Invalid contract value for {inst_id}: ctVal={ct_val} ctMult={ct_mult}")
        return value

    async def get_best_bid_ask(self, *, inst_id: str) -> tuple[Decimal, Decimal]:
        data = await self._request(
            "GET",
            "/api/v5/market/books",
            params={"instId": inst_id, "sz": "1"},
            auth=False,
            expect_list=True,
        )
        if not data:
            raise RuntimeError("Order book data is empty.")
        first = data[0]
        bids = first.get("bids") or []
        asks = first.get("asks") or []
        if not bids or not asks:
            raise RuntimeError("Order book has no bids/asks.")
        best_bid = Decimal(str(bids[0][0]))
        best_ask = Decimal(str(asks[0][0]))
        return best_bid, best_ask

    async def get_price_limits(self, *, inst_id: str) -> OkxPriceLimits:
        from exchange.okx.models import OkxPriceLimits

        data = await self._request(
            "GET",
            "/api/v5/public/price-limit",
            params={"instId": inst_id},
            auth=False,
            expect_list=True,
        )
        if not data:
            raise RuntimeError(f"Price limit data is empty for {inst_id}.")
        item = data[0]
        buy_lmt = item.get("buyLmt")
        sell_lmt = item.get("sellLmt")
        if not buy_lmt or not sell_lmt:
            raise RuntimeError(f"buyLmt/sellLmt missing for {inst_id}.")
        return OkxPriceLimits(
            buy_lmt=Decimal(str(buy_lmt)),
            sell_lmt=Decimal(str(sell_lmt)),
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool,
        expect_list: bool = False,
        allow_empty: bool = False,
    ) -> Any:
        return await asyncio.to_thread(
            self._request_sync,
            method,
            path,
            params or {},
            body or {},
            auth,
            expect_list,
            allow_empty,
        )

    def _request_sync(
        self,
        method: str,
        path: str,
        params: dict[str, str],
        body: dict[str, Any],
        auth: bool,
        expect_list: bool,
        allow_empty: bool,
    ) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        request_path = f"{path}{query}"
        url = f"{self._base_url}{request_path}"
        body_str = json.dumps(body, separators=(",", ":")) if body else ""

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": self._user_agent,
        }
        if auth:
            timestamp = make_timestamp()
            sign = sign_okx_request(
                secret_key=self._settings.okx_api_secret.get_secret_value(),
                timestamp=timestamp,
                method=method,
                request_path=request_path,
                body=body_str,
            )
            headers.update(
                {
                    "OK-ACCESS-KEY": self._settings.okx_api_key.get_secret_value(),
                    "OK-ACCESS-SIGN": sign,
                    "OK-ACCESS-TIMESTAMP": timestamp,
                    "OK-ACCESS-PASSPHRASE": (
                        self._settings.okx_api_passphrase.get_secret_value()
                    ),
                    "x-simulated-trading": "1" if self._settings.okx_flag_demo else "0",
                }
            )

        req = Request(
            url=url,
            method=method,
            headers=headers,
            data=body_str.encode("utf-8") if body_str else None,
        )
        timeout_sec = float(self._settings.okx_http_timeout_sec)
        try:
            with urlopen(req, timeout=timeout_sec) as resp:
                raw = resp.read().decode("utf-8")
        except TimeoutError as exc:
            raise RuntimeError(
                f"OKX request timeout on {method} {request_path}: {exc}"
            ) from exc
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            self._log_http_failure(
                method=method,
                url=url,
                params=params,
                status=exc.code,
                headers=dict(exc.headers.items()) if exc.headers else {},
                body=body,
            )
            raise RuntimeError(
                f"OKX HTTP {exc.code} on {method} {request_path}: "
                f"{body[:200] if body else 'no body'}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"OKX connection error on {method} {request_path}: {exc}") from exc

        payload = json.loads(raw)
        if payload.get("code") != "0":
            details = self._extract_sub_error(payload)
            raise RuntimeError(
                f"OKX API error: code={payload.get('code')} msg={payload.get('msg')}"
                f"{details}"
            )

        data = payload.get("data", [])
        if expect_list:
            return data
        if not data:
            if allow_empty:
                return None
            raise RuntimeError(
                f"OKX API returned empty data for {method} {path}"
            )
        return data[0]

    @staticmethod
    def _extract_sub_error(payload: dict[str, Any]) -> str:
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return ""
        first = data[0]
        if not isinstance(first, dict):
            return ""
        s_code = first.get("sCode")
        s_msg = first.get("sMsg")
        if s_code or s_msg:
            return f" sCode={s_code} sMsg={s_msg}"
        return ""

    @staticmethod
    def _normalize_cl_ord_id(cl_ord_id: str) -> str:
        # OKX требует короткий client id из допустимых символов.
        normalized = "".join(ch for ch in cl_ord_id if ch.isalnum())
        if not normalized:
            normalized = "ord"
        return normalized[:32]

    def _log_http_failure(
        self,
        *,
        method: str,
        url: str,
        params: dict[str, str],
        status: int,
        headers: dict[str, str],
        body: str,
    ) -> None:
        body_short = body[:1000] if body else ""
        log.error(
            "OKX request failed method=%s url=%s params=%s status=%s "
            "base_url=%s demo_flag=%s user_agent=%s response_headers=%s response_body=%s",
            method,
            url,
            params,
            status,
            self._base_url,
            "1" if self._settings.okx_flag_demo else "0",
            self._user_agent,
            headers,
            body_short,
        )
        if status == 403:
            log.warning(
                "Possible cause: regional OKX domain mismatch. "
                "Try eea.okx.com or us.okx.com if account region requires it."
            )

    def _parse_order(self, item: dict[str, Any]) -> OkxOrder:
        return OkxOrder(
            ord_id=str(item.get("ordId", "")),
            cl_ord_id=str(item.get("clOrdId", "")),
            state=str(item.get("state", "")),
            side=str(item.get("side", "")),
            px=_to_decimal(item.get("px")),
            avg_px=_to_decimal(item.get("avgPx")),
            sz=_to_decimal(item.get("sz")) or Decimal("0"),
            fill_sz=_to_decimal(item.get("accFillSz")) or Decimal("0"),
        )

    def _parse_fill(self, item: dict[str, Any]) -> OkxFill:
        fee_raw = _to_decimal(item.get("fee")) or Decimal("0")
        return OkxFill(
            fill_id=str(item.get("tradeId", item.get("billId", ""))),
            ord_id=str(item.get("ordId", "")),
            cl_ord_id=str(item.get("clOrdId", "")),
            inst_id=str(item.get("instId", "")),
            side=str(item.get("side", "")),
            fill_px=_to_decimal(item.get("fillPx")) or Decimal("0"),
            fill_sz=_to_decimal(item.get("fillSz")) or Decimal("0"),
            fee=fee_raw,
            fee_ccy=str(item.get("feeCcy", "USDT")),
            exec_type=str(item.get("execType")) if item.get("execType") else None,
            ts_ms=int(item["ts"]) if item.get("ts") else None,
        )
