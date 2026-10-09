"""Простой рабочий baseline loop: strategy -> order -> position -> exit."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from app.bootstrap import ExecutorContext
from config.strategy_config import StrategyDeploymentConfig, get_strategies_config
from app.position_reconcile import bootstrap_position_on_startup, reconcile_exchange_position_if_needed
from app.position_state import (
    ActiveOrder,
    ActivePosition,
    build_active_position,
    check_exit_reason,
    clamp_maker_price_to_limits,
    is_entry_order_price_stale,
    is_exit_order_price_stale,
    maker_price_for_side,
    order_fill_price,
    should_use_market_exit,
)
from domain.value_objects.instrument_id import InstrumentId
from exchange.okx.models import OkxOrder
from execution.trade_finalize import finalize_closed_trade, finalize_reconciled_close
from execution.trade_lifecycle import TradeLifecycleTracker
from persistence.executor_store import ExecutorStore
from services.id_generation import new_client_order_id
from strategy.contracts import StrategyPlugin
from strategy.registry import create_strategy

_ORDER_CANCELED_STATES = frozenset({"canceled", "rejected", "mmp_canceled"})


@dataclass(slots=True)
class StrategyLoopControl:
    """Управление жизненным циклом strategy loop (enable/disable drain/force)."""

    stop_mode: Literal["none", "drain", "force"] = "none"
    allow_new_entries: bool = True

    def request_drain_stop(self) -> None:
        self.stop_mode = "drain"
        self.allow_new_entries = False

    def request_force_stop(self) -> None:
        self.stop_mode = "force"
        self.allow_new_entries = False


async def run_baseline_loop(
    ctx: ExecutorContext,
    *,
    deployment: StrategyDeploymentConfig | None = None,
    control: StrategyLoopControl | None = None,
    run_seconds: int | None = None,
    max_loops: int | None = None,
) -> dict[str, int]:
    """Цикл baseline MVP, опционально с лимитами smoke-run."""
    return await run_baseline_loop_with_limits(
        ctx,
        deployment=deployment,
        control=control,
        run_seconds=run_seconds,
        max_loops=max_loops,
    )


async def run_baseline_loop_with_limits(
    ctx: ExecutorContext,
    *,
    deployment: StrategyDeploymentConfig | None = None,
    control: StrategyLoopControl | None = None,
    run_seconds: int | None = None,
    max_loops: int | None = None,
) -> dict[str, int]:
    """Цикл baseline MVP с optional авто-остановкой для smoke-run."""
    log = logging.getLogger(__name__)
    settings = ctx.settings
    dep = deployment or ctx.deployment
    if dep is None:
        dep = get_strategies_config(settings).get_default_deployment()
    strategy_name = dep.strategy_name
    inst_id = dep.inst_id
    exec_cfg = dep.execution
    fee_rate_maker = Decimal(exec_cfg.fee_rate_maker)
    fee_rate_taker = Decimal(exec_cfg.fee_rate_taker)
    strategy = create_strategy(strategy_name, params=dep.params)
    store = ExecutorStore.create(
        settings,
        strategy_name=strategy_name,
        inst_id=inst_id,
        td_mode=exec_cfg.td_mode,
    )
    store.set_strategy_params(
        take_profit_ticks=strategy.config.take_profit_ticks,
        stop_loss_ticks=strategy.config.stop_loss_ticks,
        timeout_sec=strategy.config.timeout_sec,
    )
    try:
        tick_size = await ctx.exchange.get_tick_size(inst_id=inst_id)
    except Exception as exc:  # noqa: BLE001
        if inst_id == "BTC-USDT-SWAP":
            tick_size = Decimal("0.1")
            log.warning(
                "Failed to fetch tick size from OKX, fallback tick_size=0.1 for BTC-USDT-SWAP: %s",
                exc,
            )
        else:
            raise
    try:
        contract_value = await ctx.exchange.get_contract_value(inst_id=inst_id)
    except Exception as exc:  # noqa: BLE001
        if inst_id == "BTC-USDT-SWAP":
            contract_value = Decimal("0.01")
            log.warning(
                "Failed to fetch contract value from OKX, fallback ctVal=0.01 for BTC-USDT-SWAP: %s",
                exc,
            )
        else:
            raise
    rng_seed = getattr(strategy, "rng_seed", None)
    await store.open(
        extra_json={
            "take_profit_ticks": strategy.config.take_profit_ticks,
            "stop_loss_ticks": strategy.config.stop_loss_ticks,
            "timeout_sec": strategy.config.timeout_sec,
            "okx_account_label": settings.okx_account_label,
            "okx_flag_demo": settings.okx_flag_demo,
            "order_size": exec_cfg.order_size,
            "contract_value": str(contract_value),
            "tick_size": str(tick_size),
            "rng_seed": rng_seed,
        }
    )
    started_at = ctx.clock.now_utc()
    loop_count = 0
    log.info("starting baseline executor")
    log.info(
        "runtime_mode=%s safe_mode=%s demo_flag=%s account_label=%s",
        settings.runtime_mode.value,
        settings.safe_mode,
        settings.okx_flag_demo,
        settings.okx_account_label,
    )
    if not settings.okx_account_label:
        log.warning(
            "OKX_ACCOUNT_LABEL is not set: runs from different subaccounts will be "
            "indistinguishable in executor_runs"
        )
    log.info(
        "inst_id=%s sqlite_path=%s postgres_run_id=%s loop_sleep=%s tick_size=%s "
        "contract_value=%s rng_seed=%s",
        inst_id,
        settings.sqlite_path,
        store.run_id,
        settings.loop_sleep_sec,
        tick_size,
        contract_value,
        rng_seed,
    )
    log.info(
        "strategy_config decision_step=%s cooldown=%s tp_ticks=%s sl_ticks=%s timeout=%s",
        strategy.config.decision_step_sec,
        strategy.config.cooldown_sec,
        strategy.config.take_profit_ticks,
        strategy.config.stop_loss_ticks,
        strategy.config.timeout_sec,
    )
    if run_seconds is not None or max_loops is not None:
        log.info("smoke limits: run_seconds=%s max_loops=%s", run_seconds, max_loops)

    active_position: ActivePosition | None = None
    active_order: ActiveOrder | None = None

    active_position, active_order = await bootstrap_position_on_startup(
        ctx=ctx,
        store=store,
        strategy=strategy,
        strategy_name=strategy_name,
        inst_id=inst_id,
        tick_size=tick_size,
        now=started_at,
    )

    try:
        while True:
            now = ctx.clock.now_utc()
            loop_count += 1
            if max_loops is not None and loop_count > max_loops:
                log.info("stop reason: max_loops reached (%s)", max_loops)
                break
            if run_seconds is not None:
                elapsed = (now - started_at).total_seconds()
                if elapsed >= run_seconds:
                    log.info("stop reason: run_seconds reached (%s)", run_seconds)
                    break
            if control is not None and control.stop_mode == "force":
                log.info("stop reason: force stop requested for strategy=%s", strategy_name)
                break
            try:
                ticker = await ctx.exchange.get_ticker_last(inst_id=inst_id)
                account = await ctx.exchange.get_account_snapshot()
                _ = account
                executor_healthy = True
                market_data_fresh = _is_market_data_fresh(ticker.ts_ms, now)
            except Exception as exc:  # noqa: BLE001
                executor_healthy = False
                market_data_fresh = False
                log.exception("executor unhealthy: %s", exc)
                store.save_service_event(
                    strategy_name="system",
                    event_type="error",
                    message="executor unhealthy",
                    payload={"error": str(exc)},
                    level="ERROR",
                )
                await asyncio.sleep(settings.loop_sleep_sec)
                continue

            try:
                open_orders = await ctx.exchange.get_open_orders(inst_id=inst_id)
                has_active_order = len(open_orders) > 0 or active_order is not None
                positions = await ctx.exchange.get_positions(inst_id=inst_id)
                has_open_position = len(positions) > 0 or active_position is not None

                okx_inst_positions = [p for p in positions if p.inst_id == inst_id]
                if active_order is None and active_position is None:
                    await _sweep_untracked_executor_orders(
                        ctx=ctx,
                        inst_id=inst_id,
                        open_orders=open_orders,
                        log=log,
                    )
                restored = reconcile_exchange_position_if_needed(
                    active_position=active_position,
                    okx_inst_positions=okx_inst_positions,
                    strategy_name=strategy_name,
                    tick_size=tick_size,
                    strategy=strategy,
                    now=now,
                )
                if restored is not None and active_position is None:
                    active_position = restored
                    has_open_position = True
                    store.save_position_open(
                        position_id=active_position.position_id,
                        strategy_name=active_position.strategy_name,
                        side=active_position.side,
                        entry_price=float(active_position.entry_price),
                        entry_ts=active_position.entry_ts.isoformat(),
                        size=float(active_position.size),
                    )
                    store.save_service_event(
                        strategy_name=active_position.strategy_name,
                        event_type="position_reconciled",
                        message="restored in-memory position from exchange snapshot",
                        payload={
                            "position_id": active_position.position_id,
                            "side": active_position.side,
                            "entry_price": str(active_position.entry_price),
                        },
                        level="WARNING",
                    )

                if active_order is not None:
                    local_order_id = active_order.client_id
                    order = await ctx.exchange.get_order(inst_id=inst_id, ord_id=active_order.order_id)
                    if order and order.state == "filled":
                        fill_price = order_fill_price(order) or ticker.last
                        fill_sz = order.fill_sz if order.fill_sz > 0 else Decimal(active_order.size)
                        if active_order.purpose == "entry":
                            active_position = _open_position_from_entry_fill(
                                store=store,
                                strategy=strategy,
                                active_order=active_order,
                                order=order,
                                size=fill_sz,
                                fill_price=fill_price,
                                now=now,
                                tick_size=tick_size,
                                log=log,
                            )
                            has_open_position = True
                        elif active_position is not None:
                            active_position.add_exit_fill(fill_sz=fill_sz, fill_px=fill_price)
                            await _close_position_from_exit_fill(
                                ctx=ctx,
                                store=store,
                                strategy=strategy,
                                position=active_position,
                                active_order=active_order,
                                order=order,
                                exit_price=active_position.exit_vwap() or fill_price,
                                now=now,
                                inst_id=inst_id,
                                fee_rate_maker=fee_rate_maker,
                                fee_rate_taker=fee_rate_taker,
                                contract_value=contract_value,
                                log=log,
                            )
                            active_position = None
                            has_open_position = False
                        store.save_order(
                            local_order_id=local_order_id,
                            strategy_name=active_order.strategy_name,
                            exchange_order_id=order.ord_id,
                            side=order.side,
                            order_type=active_order.order_type,
                            price=float(fill_price),
                            size=float(fill_sz),
                            status=order.state,
                            created_at=now.isoformat(),
                            filled_at=now.isoformat(),
                        )
                        active_order = None
                        has_active_order = False
                    elif order and order.state in _ORDER_CANCELED_STATES:
                        partial_px = order_fill_price(order) or ticker.last
                        if active_order.purpose == "exit" and active_position is not None:
                            if order.fill_sz > 0:
                                active_position.add_exit_fill(
                                    fill_sz=order.fill_sz,
                                    fill_px=partial_px,
                                )
                                log.warning(
                                    "exit order partially filled before cancel: ord_id=%s "
                                    "filled=%s px=%s remaining=%s",
                                    order.ord_id,
                                    order.fill_sz,
                                    partial_px,
                                    active_position.remaining_size(),
                                )
                            if active_position.remaining_size() <= 0:
                                await _close_position_from_exit_fill(
                                    ctx=ctx,
                                    store=store,
                                    strategy=strategy,
                                    position=active_position,
                                    active_order=active_order,
                                    order=order,
                                    exit_price=active_position.exit_vwap() or partial_px,
                                    now=now,
                                    inst_id=inst_id,
                                    fee_rate_maker=fee_rate_maker,
                                    fee_rate_taker=fee_rate_taker,
                                    contract_value=contract_value,
                                    log=log,
                                )
                                active_position = None
                                has_open_position = False
                            else:
                                lc = store.get_trade_lifecycle()
                                if lc is not None:
                                    lc.on_exit_maker_attempt_failed()
                                active_position.exit_maker_attempts += 1
                                log.warning(
                                    "exit order not filled: ord_id=%s state=%s attempts=%s",
                                    order.ord_id,
                                    order.state,
                                    active_position.exit_maker_attempts,
                                )
                                store.save_service_event(
                                    strategy_name=active_order.strategy_name,
                                    event_type="exit_not_filled",
                                    message="exit order canceled/rejected",
                                    payload={
                                        "ord_id": order.ord_id,
                                        "state": order.state,
                                        "attempts": active_position.exit_maker_attempts,
                                        "filled": str(order.fill_sz),
                                        "remaining": str(active_position.remaining_size()),
                                    },
                                    level="WARNING",
                                )
                        elif active_order.purpose == "entry" and order.fill_sz > 0:
                            log.warning(
                                "entry order partially filled before cancel: ord_id=%s filled=%s of %s",
                                order.ord_id,
                                order.fill_sz,
                                active_order.size,
                            )
                            active_position = _open_position_from_entry_fill(
                                store=store,
                                strategy=strategy,
                                active_order=active_order,
                                order=order,
                                size=order.fill_sz,
                                fill_price=partial_px,
                                now=now,
                                tick_size=tick_size,
                                log=log,
                            )
                            has_open_position = True
                        else:
                            log.warning(
                                "entry order not filled: ord_id=%s state=%s",
                                order.ord_id,
                                order.state,
                            )
                            store.clear_trade_lifecycle()
                            store.save_service_event(
                                strategy_name=active_order.strategy_name,
                                event_type="entry_not_filled",
                                message="entry order canceled/rejected",
                                payload={"ord_id": order.ord_id, "state": order.state},
                                level="WARNING",
                            )
                        store.save_order(
                            local_order_id=local_order_id,
                            strategy_name=active_order.strategy_name,
                            exchange_order_id=order.ord_id,
                            side=order.side,
                            order_type=active_order.order_type,
                            price=float(order.avg_px or order.px) if (order.avg_px or order.px) else None,
                            size=float(order.sz),
                            status=order.state,
                            created_at=now.isoformat(),
                        )
                        active_order = None
                    elif order and order.state in {"live", "partially_filled"}:
                        reprice_elapsed = (now - active_order.last_reprice_at).total_seconds()
                        max_wait_elapsed = (now - active_order.created_at).total_seconds()
                        is_exit = active_order.purpose == "exit"
                        reprice_sec = (
                            strategy.config.exit_maker_reprice_sec
                            if is_exit
                            else exec_cfg.maker_reprice_sec
                        )
                        max_wait_sec = (
                            strategy.config.exit_maker_max_wait_sec
                            if is_exit
                            else exec_cfg.maker_max_wait_sec
                        )
                        order_px = order.px or order.avg_px
                        stale_reprice = False
                        if order_px is not None:
                            best_bid, best_ask = await ctx.exchange.get_best_bid_ask(
                                inst_id=inst_id
                            )
                            if is_exit and active_position is not None:
                                stale_reprice = is_exit_order_price_stale(
                                    position=active_position,
                                    order_side=active_order.side,
                                    order_price=order_px,
                                    best_bid=best_bid,
                                    best_ask=best_ask,
                                    stale_ticks=strategy.config.exit_stale_reprice_ticks,
                                    tick_size=tick_size,
                                )
                            elif not is_exit:
                                stale_reprice = is_entry_order_price_stale(
                                    order_side=active_order.side,
                                    order_price=order_px,
                                    best_bid=best_bid,
                                    best_ask=best_ask,
                                    stale_ticks=strategy.config.entry_stale_reprice_ticks,
                                    tick_size=tick_size,
                                )
                        if (
                            (reprice_elapsed >= reprice_sec or stale_reprice)
                            and max_wait_elapsed <= max_wait_sec
                        ):
                            if stale_reprice:
                                log.warning(
                                    "%s order stale vs touch: ord_id=%s px=%s, reprice now",
                                    active_order.purpose,
                                    active_order.order_id,
                                    order_px,
                                )
                            try:
                                await _cancel_order_best_effort(
                                    ctx=ctx,
                                    inst_id=inst_id,
                                    store=store,
                                    active_order=active_order,
                                )
                            except RuntimeError as exc:
                                if _is_okx_cancel_already_done_error(exc):
                                    log.info(
                                        "cancel skipped during reprice (already final): clOrdId=%s",
                                        active_order.client_id,
                                    )
                                    continue
                                raise
                            # Новый ордер — только после подтверждённой отмены без исполнения:
                            # иначе возможны двойной объём или потерянный частичный fill.
                            after_cancel = await ctx.exchange.get_order(
                                inst_id=inst_id, ord_id=active_order.order_id
                            )
                            if after_cancel is None or after_cancel.state not in _ORDER_CANCELED_STATES:
                                log.warning(
                                    "reprice deferred: cancel not confirmed ord_id=%s state=%s",
                                    active_order.order_id,
                                    after_cancel.state if after_cancel else None,
                                )
                                await asyncio.sleep(settings.loop_sleep_sec)
                                continue
                            if after_cancel.fill_sz > 0:
                                log.warning(
                                    "reprice skipped: ord_id=%s partially filled %s before cancel",
                                    active_order.order_id,
                                    after_cancel.fill_sz,
                                )
                                continue
                            best_bid, best_ask = await ctx.exchange.get_best_bid_ask(inst_id=inst_id)
                            reprice = await _resolve_maker_price(
                                ctx,
                                inst_id=inst_id,
                                side=active_order.side,
                                best_bid=best_bid,
                                best_ask=best_ask,
                                tick_size=tick_size,
                            )
                            new_client_id = new_client_order_id(
                                prefix="entry" if active_order.purpose == "entry" else "exit"
                            )
                            try:
                                new_order_id = await ctx.exchange.place_limit_post_only(
                                    side=active_order.side,
                                    size=active_order.size,
                                    price=reprice,
                                    cl_ord_id=new_client_id,
                                    reduce_only=active_order.reduce_only,
                                )
                            except RuntimeError as exc:
                                if _is_okx_price_limit_error(exc):
                                    log.warning(
                                        "reprice skipped: OKX price limit reject ord_id=%s: %s",
                                        active_order.order_id,
                                        exc,
                                    )
                                    active_order = None
                                    has_active_order = False
                                    continue
                                # OKX: 51169 no position to reduce; 51170 reduce-only wrong side vs exchange book.
                                if active_order.purpose == "exit" and _is_okx_reduce_sync_error(exc):
                                    log.warning(
                                        "exit reprice skipped: exchange rejected reduce-only (ord_id=%s): %s",
                                        active_order.order_id,
                                        exc,
                                    )
                                    store.save_service_event(
                                        strategy_name=active_order.strategy_name,
                                        event_type="exit_sync_lost",
                                        message="exit reprice rejected: reduce-only sync error",
                                        payload={
                                            "ord_id": active_order.order_id,
                                            "error": str(exc),
                                        },
                                        level="WARNING",
                                    )
                                    active_order = None
                                    if active_position is not None:
                                        try:
                                            exchange_positions = await ctx.exchange.get_positions(
                                                inst_id=inst_id
                                            )
                                        except Exception as sync_exc:  # noqa: BLE001
                                            log.warning(
                                                "exit sync check failed after reprice reject: %s",
                                                sync_exc,
                                            )
                                            has_active_order = False
                                            continue
                                        if exchange_positions:
                                            log.warning(
                                                "exit sync mismatch: exchange still reports %s open position(s), keep local position",
                                                len(exchange_positions),
                                            )
                                            has_open_position = True
                                            has_active_order = False
                                            continue
                                        await finalize_reconciled_close(
                                            ctx=ctx,
                                            store=store,
                                            position=active_position,
                                            lifecycle=store.get_trade_lifecycle(),
                                            exit_price=active_position.blended_exit_price(ticker.last),
                                            closed_at=now,
                                            inst_id=inst_id,
                                            fee_rate_maker=fee_rate_maker,
                                            fee_rate_taker=fee_rate_taker,
                                            contract_value=contract_value,
                                        )
                                        store.save_service_event(
                                            strategy_name=active_position.strategy_name,
                                            event_type="position_closed_sync",
                                            message="position closed via sync reconcile",
                                            payload={
                                                "position_id": active_position.position_id,
                                                "reason": "exit_reprice_rejected",
                                            },
                                        )
                                        strategy.on_position_closed(now)
                                        active_position = None
                                        has_open_position = False
                                    has_active_order = False
                                    continue
                                raise
                            log.info(
                                "maker reprice: purpose=%s old_ord=%s new_ord=%s px=%s",
                                active_order.purpose,
                                active_order.order_id,
                                new_order_id,
                                reprice,
                            )
                            reprice_purpose = active_order.purpose
                            lc = store.get_trade_lifecycle()
                            if lc is not None:
                                lc.on_reprice(reprice_purpose, float(reprice), now)
                                lc.track_order(reprice_purpose, new_order_id)
                            old_client_id = active_order.client_id
                            old_strategy = active_order.strategy_name
                            old_side = active_order.side
                            old_reduce = active_order.reduce_only
                            old_size = active_order.size
                            active_order = ActiveOrder(
                                order_id=new_order_id,
                                client_id=new_client_id,
                                strategy_name=active_order.strategy_name,
                                side=active_order.side,
                                purpose=active_order.purpose,
                                created_at=active_order.created_at,
                                last_reprice_at=now,
                                reduce_only=active_order.reduce_only,
                                size=active_order.size,
                                order_type="post_only",
                            )
                            store.save_order(
                                local_order_id=new_client_id,
                                strategy_name=old_strategy,
                                exchange_order_id=new_order_id,
                                side=old_side,
                                order_type="post_only",
                                price=float(reprice),
                                size=float(Decimal(old_size)),
                                status="submitted",
                                created_at=now.isoformat(),
                                parent_order_id_local=old_client_id,
                                position_id=active_position.position_id if active_position else None,
                                reduce_only=old_reduce,
                                signal_id=lc.entry_signal_id if lc and reprice_purpose == "entry" else None,
                            )
                        elif max_wait_elapsed > max_wait_sec:
                            log.warning(
                                "maker order timeout: purpose=%s ord_id=%s wait=%ss",
                                active_order.purpose,
                                active_order.order_id,
                                max_wait_sec,
                            )
                            timed_out = active_order
                            await _cancel_order_best_effort(
                                ctx=ctx,
                                inst_id=inst_id,
                                store=store,
                                active_order=timed_out,
                            )
                            await _verify_order_canceled(
                                ctx=ctx,
                                inst_id=inst_id,
                                order_id=timed_out.order_id,
                                client_id=timed_out.client_id,
                                log=log,
                            )
                            if not timed_out.timeout_cancel_requested:
                                timed_out.timeout_cancel_requested = True
                                lc = store.get_trade_lifecycle()
                                if lc is not None:
                                    lc.on_timeout_cancel(timed_out.purpose)
                            # active_order не сбрасываем: итог отмены (включая частичный fill
                            # и счётчик exit-попыток) обработает ветка canceled на следующей итерации.

                if active_order is not None:
                    await asyncio.sleep(settings.loop_sleep_sec)
                    continue

                if active_position is None:
                    if control is not None and not control.allow_new_entries:
                        if not has_active_order:
                            log.info(
                                "stop reason: drain completed for strategy=%s (no position/order)",
                                strategy_name,
                            )
                            break
                        await asyncio.sleep(settings.loop_sleep_sec)
                        continue
                    if strategy.should_decide(
                        now=now,
                        has_open_position=has_open_position,
                        has_active_order=has_active_order,
                        executor_healthy=executor_healthy,
                        market_data_fresh=market_data_fresh,
                    ):
                        signal = strategy.make_decision(now=now)
                        domain_signal = signal.to_domain_signal(InstrumentId(inst_id))
                        decision_meta = dict(getattr(signal, "decision_meta", {}) or {})
                        log.info(
                            "strategy decided %s signal_id=%s meta=%s",
                            signal.side.upper(),
                            signal.signal_id,
                            decision_meta,
                        )
                        market_snapshot = {
                            "last": str(ticker.last),
                            "ticker_ts_ms": ticker.ts_ms,
                            "account_label": settings.okx_account_label,
                            **decision_meta,
                        }
                        store.save_signal(
                            signal_id=signal.signal_id,
                            strategy_name=signal.strategy_name,
                            side=signal.side,
                            created_at=signal.created_at.isoformat(),
                            market_snapshot=market_snapshot,
                        )
                        store.begin_trade_lifecycle(signal.signal_id, tick_size=tick_size)
                        store.save_service_event(
                            strategy_name=signal.strategy_name,
                            event_type="decision",
                            message="strategy made decision",
                            payload={
                                "side": signal.side,
                                "signal_id": signal.signal_id,
                                **market_snapshot,
                            },
                        )

                        order_side: Literal["buy", "sell"] = (
                            "buy" if signal.side == "long" else "sell"
                        )
                        try:
                            maker_px = await _resolve_maker_price(
                                ctx,
                                inst_id=inst_id,
                                side=order_side,
                                tick_size=tick_size,
                            )
                            exchange_order_id = await ctx.exchange.place_limit_post_only(
                                side=order_side,
                                size=exec_cfg.order_size,
                                price=maker_px,
                                cl_ord_id=domain_signal.signal_id,
                            )
                        except RuntimeError as exc:
                            if _is_okx_price_limit_error(exc):
                                log.warning(
                                    "entry submit skipped: OKX price limit reject side=%s: %s",
                                    order_side,
                                    exc,
                                )
                                store.save_service_event(
                                    strategy_name=signal.strategy_name,
                                    event_type="entry_price_limit_reject",
                                    message="entry order rejected by OKX price limit",
                                    payload={
                                        "side": order_side,
                                        "signal_id": signal.signal_id,
                                        "error": str(exc),
                                    },
                                    level="WARNING",
                                )
                                await asyncio.sleep(settings.loop_sleep_sec)
                                continue
                            raise
                        active_order = ActiveOrder(
                            order_id=exchange_order_id,
                            client_id=domain_signal.signal_id,
                            strategy_name=signal.strategy_name,
                            side=order_side,
                            purpose="entry",
                            created_at=now,
                            last_reprice_at=now,
                            reduce_only=False,
                            size=exec_cfg.order_size,
                            order_type="post_only",
                        )
                        log.info(
                            "entry maker order submitted: side=%s ord_id=%s px=%s",
                            order_side,
                            exchange_order_id,
                            maker_px,
                        )
                        lc = store.get_trade_lifecycle()
                        if lc is not None:
                            lc.on_entry_submit(
                                float(maker_px),
                                touch_px=float(maker_px),
                                ts=now,
                            )
                            lc.track_order("entry", exchange_order_id)
                        store.save_service_event(
                            strategy_name=signal.strategy_name,
                            event_type="entry_submitted",
                            message="entry order submitted",
                            payload={"ord_id": exchange_order_id, "side": order_side},
                        )
                        store.save_order(
                            local_order_id=domain_signal.signal_id,
                            strategy_name=signal.strategy_name,
                            exchange_order_id=exchange_order_id,
                            side=order_side,
                            order_type="post_only",
                            price=float(maker_px),
                            size=float(Decimal(exec_cfg.order_size)),
                            status="submitted",
                            created_at=now.isoformat(),
                            signal_id=domain_signal.signal_id,
                        )
                    else:
                        if int(now.timestamp()) % strategy.config.decision_step_sec == 0:
                            store.save_service_event(
                                strategy_name=strategy_name,
                                event_type="decision_skipped",
                                message="decision skipped",
                                payload={
                                    "has_open_position": has_open_position,
                                    "has_active_order": has_active_order,
                                    "executor_healthy": executor_healthy,
                                    "market_data_fresh": market_data_fresh,
                                },
                            )
                else:
                    reason = check_exit_reason(active_position, ticker.last, now)
                    if reason:
                        lc = store.get_trade_lifecycle()
                        if lc is not None:
                            lc.on_exit_trigger(reason)
                        exit_side: Literal["buy", "sell"] = (
                            "sell" if active_position.side == "long" else "buy"
                        )
                        use_market = should_use_market_exit(
                            position=active_position,
                            now=now,
                            config=strategy.config,
                        )
                        exit_client_id = new_client_order_id(
                            prefix="exit-mkt" if use_market else "exit"
                        )
                        exit_size = str(active_position.remaining_size())
                        try:
                            if use_market:
                                exit_order_id = await ctx.exchange.place_market_order(
                                    side=exit_side,
                                    size=exit_size,
                                    cl_ord_id=exit_client_id,
                                    reduce_only=True,
                                )
                                exit_px = ticker.last
                                order_type = "market"
                                log.warning(
                                    "%s triggered, exit market submitted ord_id=%s attempts=%s",
                                    reason,
                                    exit_order_id,
                                    active_position.exit_maker_attempts,
                                )
                            else:
                                exit_px = await _resolve_maker_price(
                                    ctx,
                                    inst_id=inst_id,
                                    side=exit_side,
                                    tick_size=tick_size,
                                )
                                exit_order_id = await ctx.exchange.place_limit_post_only(
                                    side=exit_side,
                                    size=exit_size,
                                    price=exit_px,
                                    cl_ord_id=exit_client_id,
                                    reduce_only=True,
                                )
                                order_type = "post_only"
                                log.info(
                                    "%s triggered, exit maker submitted ord_id=%s px=%s",
                                    reason,
                                    exit_order_id,
                                    exit_px,
                                )
                        except RuntimeError as exc:
                            if _is_okx_reduce_sync_error(exc):
                                log.warning(
                                    "exit submit skipped: exchange rejected reduce-only for %s: %s",
                                    active_position.position_id,
                                    exc,
                                )
                                store.save_service_event(
                                    strategy_name=active_position.strategy_name,
                                    event_type="exit_sync_lost",
                                    message="exit submit rejected: reduce-only sync error",
                                    payload={
                                        "position_id": active_position.position_id,
                                        "reason": reason,
                                        "error": str(exc),
                                    },
                                    level="WARNING",
                                )
                                try:
                                    exchange_positions = await ctx.exchange.get_positions(
                                        inst_id=inst_id
                                    )
                                except Exception as sync_exc:  # noqa: BLE001
                                    log.warning(
                                        "exit sync check failed after submit reject: %s",
                                        sync_exc,
                                    )
                                    continue
                                if exchange_positions:
                                    log.warning(
                                        "exit sync mismatch: exchange still reports %s open position(s), keep local position",
                                        len(exchange_positions),
                                    )
                                    has_open_position = True
                                    continue
                                await finalize_reconciled_close(
                                    ctx=ctx,
                                    store=store,
                                    position=active_position,
                                    lifecycle=store.get_trade_lifecycle(),
                                    exit_price=active_position.blended_exit_price(ticker.last),
                                    closed_at=now,
                                    inst_id=inst_id,
                                    fee_rate_maker=fee_rate_maker,
                                    fee_rate_taker=fee_rate_taker,
                                    contract_value=contract_value,
                                )
                                store.save_service_event(
                                    strategy_name=active_position.strategy_name,
                                    event_type="position_closed_sync",
                                    message="position closed via sync reconcile",
                                    payload={
                                        "position_id": active_position.position_id,
                                        "reason": reason,
                                    },
                                )
                                strategy.on_position_closed(now)
                                active_position = None
                                has_open_position = False
                                continue
                            raise
                        active_order = ActiveOrder(
                            order_id=exit_order_id,
                            client_id=exit_client_id,
                            strategy_name=active_position.strategy_name,
                            side=exit_side,
                            purpose="exit",
                            created_at=now,
                            last_reprice_at=now,
                            reduce_only=True,
                            size=exit_size,
                            order_type=order_type,
                        )
                        if lc is not None:
                            lc.on_exit_submit(
                                float(exit_px),
                                order_type=order_type,
                                market_fallback=use_market,
                                ts=now,
                            )
                            lc.track_order("exit", exit_order_id)
                            lc.exit_maker_attempts = active_position.exit_maker_attempts
                        exit_event = f"{reason.lower()}_exit_market" if use_market else f"{reason.lower()}_exit"
                        store.save_service_event(
                            strategy_name=active_position.strategy_name,
                            event_type=exit_event,
                            message=f"{reason} exit submitted ({order_type})",
                            payload={
                                "ord_id": exit_order_id,
                                "position_id": active_position.position_id,
                                "order_type": order_type,
                                "market_fallback": use_market,
                            },
                        )
                        store.save_order(
                            local_order_id=exit_client_id,
                            strategy_name=active_position.strategy_name,
                            exchange_order_id=exit_order_id,
                            side=exit_side,
                            order_type=order_type,
                            price=float(exit_px),
                            size=float(exit_size),
                            status="submitted",
                            created_at=now.isoformat(),
                            position_id=active_position.position_id,
                            reduce_only=True,
                        )

            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "baseline iteration failed (retrying): %s: %s",
                    type(exc).__name__,
                    exc,
                    exc_info=not _is_okx_price_limit_error(exc),
                )
                store.save_service_event(
                    strategy_name=active_position.strategy_name if active_position else "system",
                    event_type="loop_iteration_error",
                    message="baseline iteration failed",
                    payload={"error": str(exc), "error_type": type(exc).__name__},
                    level="ERROR",
                )
                if _is_okx_price_limit_error(exc):
                    await asyncio.sleep(settings.loop_sleep_sec)
                else:
                    _backoff = min(30.0, max(settings.loop_sleep_sec * 5.0, 5.0))
                    await asyncio.sleep(_backoff)
                continue

            await asyncio.sleep(settings.loop_sleep_sec)
    finally:
        summary = store.get_counts_summary()
        log.info("Run finished")
        log.info("signals: %s", summary["signals"])
        log.info("orders: %s", summary["orders"])
        log.info("positions: %s", summary["positions"])
        log.info("trade_results: %s", summary["trade_results"])
        log.info("service_events: %s", summary["service_events"])
        stop_reason = None
        if control is not None and control.stop_mode != "none":
            stop_reason = control.stop_mode
        await store.aclose(status="stopped", stop_reason=stop_reason)
    return summary


def _open_position_from_entry_fill(
    *,
    store: ExecutorStore,
    strategy: StrategyPlugin,
    active_order: ActiveOrder,
    order: OkxOrder,
    size: Decimal,
    fill_price: Decimal,
    now: datetime,
    tick_size: Decimal,
    log: logging.Logger,
) -> ActivePosition:
    """Позиция из исполненного (полностью или частично) entry-ордера."""
    position_side: Literal["long", "short"] = "long" if order.side == "buy" else "short"
    position = build_active_position(
        position_id=new_client_order_id(prefix="pos"),
        strategy_name=active_order.strategy_name,
        side=position_side,
        entry_price=fill_price,
        entry_ts=now,
        size=size,
        tick_size=tick_size,
        strategy=strategy,
    )
    partial = size < Decimal(active_order.size)
    log.info(
        "position opened: id=%s side=%s entry=%s size=%s partial=%s tp=%s sl=%s",
        position.position_id,
        position.side,
        position.entry_price,
        position.size,
        partial,
        position.tp_price,
        position.sl_price,
    )
    store.save_service_event(
        strategy_name=position.strategy_name,
        event_type="entry_filled",
        message="entry order partially filled" if partial else "entry order filled",
        payload={
            "position_id": position.position_id,
            "ord_id": order.ord_id,
            "size": str(size),
            "requested_size": active_order.size,
            "partial": partial,
        },
    )
    lc = store.get_trade_lifecycle()
    store.save_position_open(
        position_id=position.position_id,
        strategy_name=position.strategy_name,
        side=position.side,
        entry_price=float(position.entry_price),
        entry_ts=position.entry_ts.isoformat(),
        size=float(position.size),
        entry_signal_id=lc.entry_signal_id if lc else active_order.client_id,
        entry_order_id_local=active_order.client_id,
    )
    if lc is not None:
        lc.on_entry_fill(
            float(fill_price),
            exchange_ord_id=order.ord_id,
            cl_ord_id=active_order.client_id,
            ts=now,
        )
        lc.bind_position(position.position_id)
    return position


async def _close_position_from_exit_fill(
    *,
    ctx: ExecutorContext,
    store: ExecutorStore,
    strategy: StrategyPlugin,
    position: ActivePosition,
    active_order: ActiveOrder,
    order: OkxOrder,
    exit_price: Decimal,
    now: datetime,
    inst_id: str,
    fee_rate_maker: Decimal,
    fee_rate_taker: Decimal,
    contract_value: Decimal,
    log: logging.Logger,
) -> None:
    """Финализация сделки после полного выхода; `exit_price` — VWAP всех exit-исполнений."""
    lc = store.get_trade_lifecycle() or TradeLifecycleTracker()
    close_source = (
        "executor_market_fallback" if lc.exit_market_fallback_used else "executor_maker"
    )
    lc.on_exit_fill(
        float(exit_price),
        exchange_ord_id=order.ord_id,
        cl_ord_id=active_order.client_id,
        order_type=lc.exit_order_type,
        ts=now,
        close_source=close_source,
    )
    trade = await finalize_closed_trade(
        ctx=ctx,
        store=store,
        position=position,
        lifecycle=lc,
        exit_price=exit_price,
        closed_at=now,
        inst_id=inst_id,
        fee_rate_maker=fee_rate_maker,
        fee_rate_taker=fee_rate_taker,
        exit_reason_raw=lc.exit_trigger_reason,
        close_source=close_source,
        contract_value=contract_value,
    )
    store.save_service_event(
        strategy_name=position.strategy_name,
        event_type="position_closed",
        message="position closed",
        payload={
            "position_id": position.position_id,
            "gross_pnl": trade.gross_pnl,
            "net_pnl": trade.net_pnl,
            "exit_reason": trade.exit_reason,
            "close_source": trade.close_source,
            "fee_source": trade.fee_source,
        },
    )
    log.info(
        "position closed: id=%s gross_pnl=%s net_pnl=%s fees=%s "
        "exit_reason=%s close_source=%s cooldown=%ss",
        position.position_id,
        trade.gross_pnl,
        trade.net_pnl,
        trade.fees,
        trade.exit_reason,
        trade.close_source,
        strategy.config.cooldown_sec,
    )
    strategy.on_position_closed(now)


def _is_market_data_fresh(ts_ms: int, now: datetime) -> bool:
    tick_ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return now - tick_ts <= timedelta(seconds=10)


async def _resolve_maker_price(
    ctx: ExecutorContext,
    *,
    inst_id: str,
    side: Literal["buy", "sell"],
    tick_size: Decimal,
    best_bid: Decimal | None = None,
    best_ask: Decimal | None = None,
) -> Decimal:
    if best_bid is None or best_ask is None:
        best_bid, best_ask = await ctx.exchange.get_best_bid_ask(inst_id=inst_id)
    price = maker_price_for_side(
        side=side,
        best_bid=best_bid,
        best_ask=best_ask,
        tick_size=tick_size,
    )
    try:
        limits = await ctx.exchange.get_price_limits(inst_id=inst_id)
        price = clamp_maker_price_to_limits(
            side=side,
            price=price,
            buy_lmt=limits.buy_lmt,
            sell_lmt=limits.sell_lmt,
            tick_size=tick_size,
        )
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).debug("price-limit fetch skipped: %s", exc)
    return price


def _is_executor_client_id(cl_ord_id: str) -> bool:
    return cl_ord_id.startswith(("rb-", "exit", "entry", "exit-mkt"))


async def _cancel_order_best_effort(
    *,
    ctx: ExecutorContext,
    inst_id: str,
    store: ExecutorStore,
    active_order: ActiveOrder,
) -> None:
    store.record_cancel_attempt(
        order_id_local=active_order.client_id,
        exchange_order_id=active_order.order_id,
        strategy_name=active_order.strategy_name,
        purpose=active_order.purpose,
    )
    try:
        await ctx.exchange.cancel_order_by_client_id(
            inst_id=inst_id,
            cl_ord_id=active_order.client_id,
        )
    except RuntimeError as exc:
        if not _is_okx_cancel_already_done_error(exc):
            raise


async def _verify_order_canceled(
    *,
    ctx: ExecutorContext,
    inst_id: str,
    order_id: str,
    client_id: str,
    log: logging.Logger,
) -> None:
    order = await ctx.exchange.get_order(inst_id=inst_id, ord_id=order_id)
    if order is None or order.state not in {"live", "partially_filled"}:
        return
    log.warning("order still live after cancel, retry ord_id=%s", order_id)
    try:
        await ctx.exchange.cancel_order_by_client_id(inst_id=inst_id, cl_ord_id=client_id)
    except RuntimeError as exc:
        if not _is_okx_cancel_already_done_error(exc):
            log.warning("retry cancel failed for ord_id=%s: %s", order_id, exc)


async def _sweep_untracked_executor_orders(
    *,
    ctx: ExecutorContext,
    inst_id: str,
    open_orders: list,
    log: logging.Logger,
) -> None:
    for order in open_orders:
        if not _is_executor_client_id(order.cl_ord_id):
            continue
        log.warning(
            "untracked executor order cleanup: ord_id=%s cl_ord_id=%s side=%s",
            order.ord_id,
            order.cl_ord_id,
            order.side,
        )
        try:
            await ctx.exchange.cancel_order_by_client_id(
                inst_id=inst_id,
                cl_ord_id=order.cl_ord_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "failed to cancel untracked order ord_id=%s: %s",
                order.ord_id,
                exc,
            )


def _is_okx_price_limit_error(exc: Exception) -> bool:
    return "sCode=51006" in str(exc)


def _is_okx_reduce_sync_error(exc: Exception) -> bool:
    """OKX reduce-only rejects when local view and exchange position disagree."""
    text = str(exc)
    return "sCode=51169" in text or "sCode=51170" in text


def _is_okx_cancel_already_done_error(exc: Exception) -> bool:
    text = str(exc)
    return "sCode=51400" in text
