from __future__ import annotations

import random
import secrets
from datetime import datetime, timedelta

from services.id_generation import new_client_order_id
from strategy.random_baseline.config import RandomBaselineConfig
from strategy.random_baseline.model import BaselineSignal
from strategy.random_baseline.state import RandomBaselineState


class RandomBaselineStrategy:
    """
    Baseline для сравнения: раз в шаг выбирает long/short случайно (50/50).

    Генератор изолирован и сидирован: последовательность сторон воспроизводима
    по `rng_seed`, который пишется в executor_runs и в каждый сигнал.
    """

    def __init__(
        self,
        config: RandomBaselineConfig | None = None,
        state: RandomBaselineState | None = None,
        strategy_name: str = "random_baseline_v1",
    ) -> None:
        self.config = config or RandomBaselineConfig()
        self.state = state or RandomBaselineState()
        self.strategy_name = strategy_name
        self.rng_seed: int = (
            self.config.random_seed
            if self.config.random_seed is not None
            else secrets.randbits(32)
        )
        self._rng = random.Random(self.rng_seed)
        self._draw_index = 0

    def should_decide(
        self,
        now: datetime,
        has_open_position: bool,
        has_active_order: bool,
        executor_healthy: bool,
        market_data_fresh: bool,
    ) -> bool:
        """True только когда можно запускать новый decision step."""
        if not executor_healthy or not market_data_fresh:
            return False
        if has_open_position or has_active_order:
            return False

        if self.state.cooldown_until is not None and now < self.state.cooldown_until:
            return False

        if self.state.last_decision_ts is None:
            return True

        elapsed = (now - self.state.last_decision_ts).total_seconds()
        return elapsed >= self.config.decision_step_sec

    def make_decision(self, now: datetime) -> BaselineSignal:
        """Случайно выбирает сторону и возвращает baseline-сигнал."""
        side = self._rng.choice(["long", "short"])
        self._draw_index += 1
        signal = BaselineSignal(
            signal_id=new_client_order_id(prefix="rb"),
            strategy_name=self.strategy_name,
            side=side,
            created_at=now,
            take_profit_ticks=self.config.take_profit_ticks,
            stop_loss_ticks=self.config.stop_loss_ticks,
            timeout_sec=self.config.timeout_sec,
            decision_meta={
                "rng_seed": self.rng_seed,
                "draw_index": self._draw_index,
            },
        )
        self.state.last_decision_ts = now
        return signal

    def on_position_closed(self, now: datetime) -> None:
        """После закрытия позиции включает cooldown на `cooldown_sec`."""
        self.state.cooldown_until = now + timedelta(seconds=self.config.cooldown_sec)
