#!/usr/bin/env python3
"""Minimal stateful golden/death cross Signal implemented with the Module SDK."""

from strategy_devkit.module_sdk import SignalModule, run_module


class GoldenCrossSignal(SignalModule):
    """Emit a 0/1 target from an explicitly supplied close-price stream."""

    def on_initialize(self) -> None:
        self.fast_period = int(self.config.get("fastPeriod", 20))
        self.slow_period = int(self.config.get("slowPeriod", 50))
        if self.fast_period < 1:
            raise ValueError("fastPeriod must be at least 1.")
        if self.slow_period <= self.fast_period:
            raise ValueError("slowPeriod must be greater than fastPeriod.")
        self.state = {"closes": [], "previousLong": None, "cycle": 0}

    def update(self, close):
        value = float(close)
        self.state["cycle"] += 1
        self.state["closes"].append(value)
        if len(self.state["closes"]) > self.slow_period:
            self.state["closes"].pop(0)

        fast_sma = None
        slow_sma = None
        target_position = None
        cross = "warming"
        if len(self.state["closes"]) >= self.fast_period:
            fast_sma = sum(self.state["closes"][-self.fast_period:]) / self.fast_period
        if len(self.state["closes"]) >= self.slow_period:
            slow_sma = sum(self.state["closes"]) / self.slow_period
            is_long = fast_sma > slow_sma
            target_position = 1.0 if is_long else 0.0
            if self.state["previousLong"] is None:
                cross = "golden_cross" if is_long else "hold_flat"
            elif is_long and not self.state["previousLong"]:
                cross = "golden_cross"
            elif not is_long and self.state["previousLong"]:
                cross = "death_cross"
            else:
                cross = "hold_long" if is_long else "hold_flat"
            self.state["previousLong"] = is_long

        return {
            "observed_close": value,
            "fast_sma": fast_sma,
            "slow_sma": slow_sma,
            "cross": cross,
            "target_position": target_position,
            "module_cycle": self.state["cycle"],
        }


if __name__ == "__main__":
    run_module(GoldenCrossSignal())
