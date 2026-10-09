from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from domain.enums.side import Side
from domain.models.signal import Signal
from domain.value_objects.instrument_id import InstrumentId


@dataclass(slots=True)
class BaselineSignal:
    signal_id: str
    strategy_name: str
    side: Literal["long", "short"]
    created_at: datetime
    take_profit_ticks: int
    stop_loss_ticks: int
    timeout_sec: int
    decision_meta: dict[str, Any] = field(default_factory=dict)

    def to_domain_signal(self, instrument: InstrumentId) -> Signal:
        """Преобразование в общий `domain.models.Signal` для execution flow."""
        domain_side = Side.BUY if self.side == "long" else Side.SELL
        return Signal(
            signal_id=self.signal_id,
            instrument=instrument,
            side=domain_side,
            created_at=self.created_at,
            strategy_id=self.strategy_name,
            meta={
                "take_profit_ticks": str(self.take_profit_ticks),
                "stop_loss_ticks": str(self.stop_loss_ticks),
                "timeout_sec": str(self.timeout_sec),
            },
        )
