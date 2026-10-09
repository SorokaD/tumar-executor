from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.bootstrap import ExecutorContext, build_executor_context
from app.orchestrator import StrategyLoopControl, run_baseline_loop
from config.settings import Settings, StrategyMode
from config.strategy_config import StrategyDeploymentConfig, get_strategies_config
from persistence.sqlite_store import SqliteMvpStore
from strategy.random_baseline.config import config_from_params


class InstrumentConflictError(RuntimeError):
    """Две стратегии на одном инструменте в одном аккаунте делят позицию OKX."""


def find_instrument_conflict(*, running: dict[str, str], inst_id: str) -> str | None:
    """Имя уже запущенной стратегии на том же инструменте, иначе None."""
    for name, running_inst in running.items():
        if running_inst == inst_id:
            return name
    return None


@dataclass(slots=True)
class StrategyRuntime:
    strategy_name: str
    inst_id: str
    task: asyncio.Task[None]
    control: StrategyLoopControl


class StrategyManager:
    """Менеджер стратегий: запускает, останавливает и обрабатывает команды."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._log = logging.getLogger(__name__)
        self._store = SqliteMvpStore(settings.sqlite_path)
        self._strategies = get_strategies_config(settings)
        self._runtimes: dict[str, StrategyRuntime] = {}
        self._stop_event = asyncio.Event()

    async def run_forever(self) -> None:
        self._bootstrap_registry()
        await self._start_enabled_strategies()
        self._log.info("strategy manager started")
        try:
            while not self._stop_event.is_set():
                await self._sync_finished_tasks()
                await self._process_commands()
                await asyncio.sleep(2.0)
        finally:
            await self._shutdown_all()
            self._store.close()

    def stop(self) -> None:
        self._stop_event.set()

    def _bootstrap_registry(self) -> None:
        for cfg in self._settings.get_strategy_runtime_configs():
            desired = "enabled" if cfg.mode == StrategyMode.ENABLED else "disabled"
            runtime = "stopped"
            self._store.upsert_strategy_registry(
                strategy_name=cfg.strategy_name,
                inst_id=cfg.inst_id,
                desired_state=desired,
                runtime_state=runtime,
            )

    async def _start_enabled_strategies(self) -> None:
        for cfg in self._settings.get_strategy_runtime_configs():
            if cfg.mode != StrategyMode.ENABLED:
                continue
            try:
                await self._start_strategy(strategy_name=cfg.strategy_name)
            except InstrumentConflictError as exc:
                self._log.error("strategy not started: %s", exc)
                self._store.set_strategy_runtime_state(
                    strategy_name=cfg.strategy_name, runtime_state="error"
                )
                self._store.save_service_event(
                    strategy_name=cfg.strategy_name,
                    event_type="strategy_start_rejected",
                    message=str(exc),
                    payload={"inst_id": cfg.inst_id},
                    level="ERROR",
                )

    async def _start_strategy(self, *, strategy_name: str) -> None:
        if strategy_name in self._runtimes:
            return
        deployment = self._strategies.get_deployment(strategy_name)
        conflict = find_instrument_conflict(
            running={name: rt.inst_id for name, rt in self._runtimes.items()},
            inst_id=deployment.inst_id,
        )
        if conflict is not None:
            raise InstrumentConflictError(
                f"strategy '{strategy_name}' cannot start on {deployment.inst_id}: "
                f"'{conflict}' already trades it in this process. One account (subaccount) "
                "shares positions per instrument, run the other strategy on a separate subaccount."
            )
        ctx = build_executor_context(self._settings, deployment=deployment)
        control = StrategyLoopControl()
        task = asyncio.create_task(
            self._run_strategy_task(
                deployment=deployment,
                ctx=ctx,
                control=control,
            )
        )
        self._runtimes[strategy_name] = StrategyRuntime(
            strategy_name=strategy_name,
            inst_id=deployment.inst_id,
            task=task,
            control=control,
        )
        self._store.set_strategy_desired_state(strategy_name=strategy_name, desired_state="enabled")
        self._store.set_strategy_runtime_state(strategy_name=strategy_name, runtime_state="running")
        self._log.info("strategy started: %s (%s)", strategy_name, deployment.inst_id)

    async def _run_strategy_task(
        self,
        *,
        deployment: StrategyDeploymentConfig,
        ctx: ExecutorContext,
        control: StrategyLoopControl,
    ) -> None:
        strategy_name = deployment.strategy_name
        try:
            await run_baseline_loop(ctx, deployment=deployment, control=control)
            self._store.set_strategy_runtime_state(strategy_name=strategy_name, runtime_state="stopped")
        except asyncio.CancelledError:
            self._store.set_strategy_runtime_state(strategy_name=strategy_name, runtime_state="stopped")
            raise
        except Exception as exc:  # noqa: BLE001
            self._store.set_strategy_runtime_state(strategy_name=strategy_name, runtime_state="error")
            self._store.save_service_event(
                strategy_name=strategy_name,
                event_type="strategy_crashed",
                message="strategy task crashed",
                payload={"error": str(exc)},
                level="ERROR",
            )

    async def _process_commands(self) -> None:
        commands = self._store.claim_pending_strategy_commands()
        for command in commands:
            cid = int(command["id"])
            strategy_name = str(command["strategy_name"])
            command_type = str(command["command_type"]).lower()
            command_mode = command["command_mode"]
            try:
                if command_type == "enable":
                    await self._start_strategy(strategy_name=strategy_name)
                elif command_type == "disable":
                    await self._disable_strategy(
                        strategy_name=strategy_name,
                        mode=str(command_mode or "drain"),
                    )
                elif command_type == "restart":
                    await self._disable_strategy(strategy_name=strategy_name, mode="force")
                    await self._start_strategy(strategy_name=strategy_name)
                else:
                    raise ValueError(f"Unsupported strategy command: {command_type}")
                self._store.finish_strategy_command(command_id=cid, status="done")
            except Exception as exc:  # noqa: BLE001
                self._store.finish_strategy_command(
                    command_id=cid,
                    status="failed",
                    error_text=str(exc),
                )

    async def _disable_strategy(self, *, strategy_name: str, mode: str) -> None:
        runtime = self._runtimes.get(strategy_name)
        self._store.set_strategy_desired_state(strategy_name=strategy_name, desired_state="disabled")
        if runtime is None:
            self._store.set_strategy_runtime_state(strategy_name=strategy_name, runtime_state="stopped")
            return
        if mode == "force":
            runtime.control.request_force_stop()
            runtime.task.cancel()
            await asyncio.gather(runtime.task, return_exceptions=True)
        else:
            runtime.control.request_drain_stop()
            await runtime.task
        self._runtimes.pop(strategy_name, None)
        self._store.set_strategy_runtime_state(strategy_name=strategy_name, runtime_state="stopped")
        self._log.info("strategy disabled: %s mode=%s", strategy_name, mode)

    async def _sync_finished_tasks(self) -> None:
        dead: list[str] = []
        for name, runtime in self._runtimes.items():
            if runtime.task.done():
                dead.append(name)
        for name in dead:
            runtime = self._runtimes.pop(name)
            await asyncio.gather(runtime.task, return_exceptions=True)

    async def _shutdown_all(self) -> None:
        names = list(self._runtimes.keys())
        if not names:
            return
        for strategy_name in names:
            runtime = self._runtimes.get(strategy_name)
            if runtime is None:
                continue
            runtime.control.request_drain_stop()
            self._log.info("shutdown drain requested for strategy=%s", strategy_name)
        tasks = [runtime.task for runtime in self._runtimes.values()]
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self._shutdown_drain_sec(),
            )
        except TimeoutError:
            self._log.warning(
                "shutdown drain timed out after %ss, force-stopping strategies",
                self._shutdown_drain_sec(),
            )
            for strategy_name in list(self._runtimes.keys()):
                await self._disable_strategy(strategy_name=strategy_name, mode="force")
            return
        self._runtimes.clear()

    def _shutdown_drain_sec(self) -> float:
        try:
            dep = self._strategies.get_default_deployment()
            return config_from_params(dep.params).shutdown_drain_sec
        except Exception:
            return 25.0
