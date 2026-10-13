"""
Точка входа процесса (console script `okx-hft-executor`).

Загружает настройки, собирает зависимости и запускает baseline loop.
Реальная торговая петля подключается на следующих этапах разработки.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from app.bootstrap import ExecutorContext, build_executor_context
from app.orchestrator import run_baseline_loop
from app.strategy_manager import StrategyManager
from services.id_generation import new_client_order_id
from config.settings import Settings, get_settings
from config.strategy_config import (
    StrategiesConfig,
    StrategyDeploymentConfig,
    get_strategies_config,
)
from persistence.postgres_journal import clear_journal_health_file
from persistence.sqlite_store import SqliteMvpStore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OKX HFT executor (baseline MVP)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только проверить конфигурацию и выйти.",
    )
    parser.add_argument(
        "--run-seconds",
        type=int,
        default=None,
        help="Ограничить длительность запуска в секундах (smoke-run).",
    )
    parser.add_argument(
        "--max-loops",
        type=int,
        default=None,
        help="Ограничить число итераций цикла (smoke-run).",
    )
    parser.add_argument(
        "--check-okx",
        action="store_true",
        help="Проверить доступность OKX API без запуска baseline loop.",
    )
    parser.add_argument(
        "--check-okx-order",
        action="store_true",
        help="Проверить выставление минимального market order и закрытие.",
    )
    parser.add_argument(
        "--single-strategy",
        action="store_true",
        help="Запустить только одну стратегию (legacy baseline loop).",
    )
    parser.add_argument(
        "--strategy-enable",
        type=str,
        default=None,
        help="Положить команду enable в очередь strategy manager.",
    )
    parser.add_argument(
        "--strategy-disable",
        type=str,
        default=None,
        help="Положить команду disable в очередь strategy manager.",
    )
    parser.add_argument(
        "--strategy-disable-mode",
        type=str,
        default="drain",
        choices=["drain", "force"],
        help="Режим disable команды: drain или force.",
    )
    parser.add_argument(
        "--strategy-restart",
        type=str,
        default=None,
        help="Положить команду restart в очередь strategy manager.",
    )
    parser.add_argument(
        "--list-strategies",
        action="store_true",
        help="Показать текущее состояние стратегий из SQLite registry.",
    )
    args = parser.parse_args()

    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    log = logging.getLogger(__name__)
    log.info(
        "Старт baseline okx-hft-executor, режим=%s",
        settings.runtime_mode.value,
    )

    if args.dry_run:
        strategies = get_strategies_config(settings)
        default_dep = strategies.get_default_deployment()
        _ = build_executor_context(settings, deployment=default_dep)
        _log_config_summary(log, settings, strategies)
        log.info("Dry-run: контекст собран, выход.")
        return

    if args.check_okx:
        default_dep = get_strategies_config(settings).get_default_deployment()
        ctx = build_executor_context(settings, deployment=default_dep)
        asyncio.run(run_okx_check(ctx, default_dep))
        return
    if args.check_okx_order:
        default_dep = get_strategies_config(settings).get_default_deployment()
        ctx = build_executor_context(settings, deployment=default_dep)
        asyncio.run(run_okx_order_check(ctx, default_dep))
        return

    if args.strategy_enable or args.strategy_disable or args.strategy_restart or args.list_strategies:
        run_manager_command(
            settings=settings,
            sqlite_path=settings.sqlite_path,
            strategy_enable=args.strategy_enable,
            strategy_disable=args.strategy_disable,
            strategy_disable_mode=args.strategy_disable_mode,
            strategy_restart=args.strategy_restart,
            list_strategies=args.list_strategies,
        )
        return

    clear_journal_health_file()

    if args.single_strategy:
        default_dep = get_strategies_config(settings).get_default_deployment()
        ctx = build_executor_context(settings, deployment=default_dep)
        asyncio.run(
            run_baseline_loop(
                ctx,
                deployment=default_dep,
                run_seconds=args.run_seconds,
                max_loops=args.max_loops,
            )
        )
        return

    run_strategy_manager(settings=settings)


async def run_okx_check(ctx: ExecutorContext, deployment: StrategyDeploymentConfig) -> None:
    settings = ctx.settings
    inst_id = deployment.inst_id
    log = logging.getLogger(__name__)
    log.info("OKX check started")
    log.info("base_url=%s", settings.okx_base_url)
    log.info("demo_flag=%s", 1 if settings.okx_flag_demo else 0)
    log.info("inst_id=%s", inst_id)

    account_ok = False
    ticker_ok = False
    tick_ok = False

    try:
        await ctx.exchange.get_account_snapshot()
        account_ok = True
    except Exception as exc:  # noqa: BLE001
        log.error("account reachable: no (%s)", exc)

    try:
        ticker = await ctx.exchange.get_ticker_last(inst_id=inst_id)
        ticker_ok = True
        log.info("last_price=%s", ticker.last)
    except Exception as exc:  # noqa: BLE001
        log.error("ticker reachable: no (%s)", exc)

    try:
        tick = await ctx.exchange.get_tick_size(inst_id=inst_id)
        tick_ok = True
        log.info("tick_size=%s", tick)
    except Exception as exc:  # noqa: BLE001
        if inst_id == "BTC-USDT-SWAP":
            log.warning(
                "tick_size fetch failed (%s), fallback tick_size=0.1 for BTC-USDT-SWAP",
                exc,
            )
            tick_ok = True
            log.info("tick_size=%s", "0.1")
        else:
            log.error("tick_size reachable: no (%s)", exc)

    log.info("account reachable: %s", "yes" if account_ok else "no")
    log.info("ticker reachable: %s", "yes" if ticker_ok else "no")
    if account_ok and ticker_ok and tick_ok:
        log.info("OKX check success")
    else:
        log.warning("OKX check finished with issues")


async def run_okx_order_check(ctx: ExecutorContext, deployment: StrategyDeploymentConfig) -> None:
    """Короткая проверка: open tiny order -> close tiny order."""
    settings = ctx.settings
    inst_id = deployment.inst_id
    exec_cfg = deployment.execution
    log = logging.getLogger(__name__)
    log.info("OKX order check started")
    log.info(
        "base_url=%s demo_flag=%s inst_id=%s td_mode=%s size=%s",
        settings.okx_base_url,
        1 if settings.okx_flag_demo else 0,
        inst_id,
        exec_cfg.td_mode,
        exec_cfg.order_size,
    )
    try:
        open_cl = new_client_order_id(prefix="chk-open")
        open_ord = await ctx.exchange.place_market_order(
            side="buy",
            size=exec_cfg.order_size,
            cl_ord_id=open_cl,
            inst_id=inst_id,
            td_mode=exec_cfg.td_mode,
        )
        log.info("order open submitted: ord_id=%s cl_ord_id=%s", open_ord, open_cl)

        close_cl = new_client_order_id(prefix="chk-close")
        close_ord = await ctx.exchange.place_market_order(
            side="sell",
            size=exec_cfg.order_size,
            cl_ord_id=close_cl,
            reduce_only=True,
            inst_id=inst_id,
            td_mode=exec_cfg.td_mode,
        )
        log.info("order close submitted: ord_id=%s cl_ord_id=%s", close_ord, close_cl)
        log.info("OKX order check success")
    except Exception as exc:  # noqa: BLE001
        log.error("OKX order check failed: %s", exc)


def run_manager_command(
    *,
    settings: Settings,
    sqlite_path: str,
    strategy_enable: str | None,
    strategy_disable: str | None,
    strategy_disable_mode: str,
    strategy_restart: str | None,
    list_strategies: bool,
) -> None:
    log = logging.getLogger(__name__)
    store = SqliteMvpStore(sqlite_path)
    try:
        existing_rows = store.list_strategies_registry()
        if not existing_rows:
            for cfg in settings.get_strategy_runtime_configs():
                store.upsert_strategy_registry(
                    strategy_name=cfg.strategy_name,
                    inst_id=cfg.inst_id,
                    desired_state="enabled" if cfg.mode.value == "enabled" else "disabled",
                    runtime_state="stopped",
                )
        if strategy_enable:
            store.enqueue_strategy_command(
                strategy_name=strategy_enable,
                command_type="enable",
            )
            log.info("queued strategy command: enable %s", strategy_enable)
        if strategy_disable:
            store.enqueue_strategy_command(
                strategy_name=strategy_disable,
                command_type="disable",
                command_mode=strategy_disable_mode,
            )
            log.info("queued strategy command: disable %s mode=%s", strategy_disable, strategy_disable_mode)
        if strategy_restart:
            store.enqueue_strategy_command(
                strategy_name=strategy_restart,
                command_type="restart",
            )
            log.info("queued strategy command: restart %s", strategy_restart)
        if list_strategies:
            rows = store.list_strategies_registry()
            if not rows:
                log.info("strategies registry is empty")
            for row in rows:
                log.info(
                    "strategy=%s inst_id=%s desired=%s runtime=%s updated_at=%s",
                    row["strategy_name"],
                    row["inst_id"],
                    row["desired_state"],
                    row["runtime_state"],
                    row["updated_at"],
                )
    finally:
        store.close()


def _log_config_summary(
    log: logging.Logger,
    settings: Settings,
    strategies: StrategiesConfig,
) -> None:
    """Краткая сводка конфигурации без секретов (для dry-run и диагностики)."""
    log.info("env=%s runtime_mode=%s safe_mode=%s", settings.env, settings.runtime_mode.value, settings.safe_mode)
    log.info("strategies_config=%s", settings.strategies_config_path)
    for dep in strategies.runtime_configs():
        log.info(
            "strategy=%s inst_id=%s mode=%s",
            dep.strategy_name,
            dep.inst_id,
            dep.mode.value,
        )
    log.info("sqlite_path=%s loop_sleep=%s", settings.sqlite_path, settings.loop_sleep_sec)
    if settings.postgres_is_configured():
        host = settings.postgres_host
        if settings.database_url is not None and host is None:
            host = "(from DATABASE_URL)"
        log.info(
            "postgres enabled schema=%s host=%s db=%s port=%s queue=%s",
            settings.postgres_schema,
            host,
            settings.postgres_db,
            settings.postgres_port,
            settings.postgres_queue_size,
        )
    else:
        log.info(
            "postgres disabled (enabled=%s, dsn configured=%s)",
            settings.postgres_enabled,
            settings.get_database_url() is not None,
        )


def run_strategy_manager(*, settings: Settings) -> None:
    manager = StrategyManager(settings)

    def _stop_handler(_sig: int, _frame: object) -> None:
        manager.stop()

    signal.signal(signal.SIGINT, _stop_handler)
    signal.signal(signal.SIGTERM, _stop_handler)
    asyncio.run(manager.run_forever())


if __name__ == "__main__":
    main()
    sys.exit(0)
