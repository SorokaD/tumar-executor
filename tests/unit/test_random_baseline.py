from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.strategy_manager import find_instrument_conflict
from strategy.random_baseline.config import RandomBaselineConfig, config_from_params
from strategy.random_baseline.service import RandomBaselineStrategy


def _sides(strategy: RandomBaselineStrategy, n: int) -> list[str]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [strategy.make_decision(now=start + timedelta(seconds=i)).side for i in range(n)]


def test_same_seed_gives_same_side_sequence() -> None:
    a = RandomBaselineStrategy(config=RandomBaselineConfig(random_seed=42))
    b = RandomBaselineStrategy(config=RandomBaselineConfig(random_seed=42))
    assert _sides(a, 50) == _sides(b, 50)


def test_sides_are_both_present_and_roughly_balanced() -> None:
    sides = _sides(RandomBaselineStrategy(config=RandomBaselineConfig(random_seed=1)), 1000)
    longs = sides.count("long")
    assert set(sides) == {"long", "short"}
    assert 400 < longs < 600


def test_decision_meta_records_seed_and_draw_index() -> None:
    strategy = RandomBaselineStrategy(config=RandomBaselineConfig(random_seed=5))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = strategy.make_decision(now=now)
    second = strategy.make_decision(now=now + timedelta(seconds=30))
    assert first.decision_meta == {"rng_seed": 5, "draw_index": 1}
    assert second.decision_meta == {"rng_seed": 5, "draw_index": 2}


def test_seed_generated_when_not_configured() -> None:
    strategy = RandomBaselineStrategy()
    assert isinstance(strategy.rng_seed, int)
    replay = RandomBaselineStrategy(config=RandomBaselineConfig(random_seed=strategy.rng_seed))
    assert _sides(strategy, 20) == _sides(replay, 20)


def test_random_seed_from_yaml_params() -> None:
    assert config_from_params({"random_seed": 123, "unknown": 1}).random_seed == 123


def test_instrument_conflict_detected() -> None:
    running = {"random_baseline_v1": "BTC-USDT-SWAP", "mean_reversion_v1": "ETH-USDT-SWAP"}
    assert find_instrument_conflict(running=running, inst_id="BTC-USDT-SWAP") == "random_baseline_v1"
    assert find_instrument_conflict(running=running, inst_id="SOL-USDT-SWAP") is None
