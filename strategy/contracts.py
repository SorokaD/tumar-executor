from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from domain.models.signal import Signal
from domain.value_objects.instrument_id import InstrumentId


class StrategySignal(Protocol):
    signal_id: str
    strategy_name: str
    side: Literal["long", "short"]
    created_at: datetime
    # Что видела стратегия при решении (seed/draw для random, фичи для моделей).
    decision_meta: dict[str, Any]

    def to_domain_signal(self, instrument: InstrumentId) -> Signal:
        ...


class StrategyPlugin(Protocol):
    strategy_name: str

    def should_decide(
        self,
        now: datetime,
        has_open_position: bool,
        has_active_order: bool,
        executor_healthy: bool,
        market_data_fresh: bool,
    ) -> bool:
        ...

    def make_decision(self, now: datetime) -> StrategySignal:
        ...

    def on_position_closed(self, now: datetime) -> None:
        ...
