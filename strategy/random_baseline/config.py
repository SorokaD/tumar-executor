from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


@dataclass(slots=True)
class RandomBaselineConfig:
    """Параметры baseline v1 для простого случайного входа."""

    decision_step_sec: int = 30
    cooldown_sec: int = 20
    take_profit_ticks: int = 700
    stop_loss_ticks: int = 350
    timeout_sec: int = 300
    # Выход: после неудачных maker-попыток или grace после timeout — market reduce-only.
    exit_market_fallback_enabled: bool = True
    exit_maker_max_attempts: int = 5
    exit_market_grace_sec: int = 30
    # Быстрее гоняем exit-maker за стаканом, чем entry.
    exit_maker_reprice_sec: int = 1
    exit_maker_max_wait_sec: int = 8
    # Если exit-ордер отстал от touch больше чем на N тиков — reprice сразу.
    exit_stale_reprice_ticks: int = 3
    # Если entry-maker отстал от touch больше чем на N тиков — reprice сразу.
    entry_stale_reprice_ticks: int = 3
    # При остановке процесса (docker stop / редеплой): ждать закрытия позиции, сек.
    shutdown_drain_sec: float = 25.0
    # Seed генератора сторон. None — случайный seed на старте (пишется в executor_runs и сигналы).
    random_seed: int | None = None


def config_from_params(params: dict[str, Any]) -> RandomBaselineConfig:
    """Собирает RandomBaselineConfig из YAML-блока стратегии."""
    valid = {f.name for f in fields(RandomBaselineConfig)}
    return RandomBaselineConfig(**{k: v for k, v in params.items() if k in valid})
