from builtin_implementations.pipeline.common import number, period, push_window
from strategy_devkit.module_sdk import SignalModule


class StochasticIndicator(SignalModule):
    def update(self, high, low, close):
        high, low, close = number(high), number(low), number(close)
        if high is None or low is None or close is None:
            return {"k": None, "d": None}
        if high < low:
            raise ValueError("Stochastic received a high below low.")
        size = period(self.config, default=14)
        smooth_k = max(1, int(self.config.get("smoothKPeriod") or 3))
        d_period = max(1, int(self.config.get("dPeriod") or 3))
        highs = push_window(self.state, "highs", high, size)
        lows = push_window(self.state, "lows", low, size)
        if len(highs) < size:
            return {"k": None, "d": None}
        highest, lowest = max(highs), min(lows)
        k_value = (
            100.0 * (close - lowest) / (highest - lowest)
            if highest != lowest
            else 0.0
        )
        raw_values = push_window(self.state, "rawK", k_value, smooth_k)
        if len(raw_values) < smooth_k:
            return {"k": None, "d": None}
        smooth_value = sum(raw_values) / smooth_k
        k_values = push_window(self.state, "kValues", smooth_value, d_period)
        return {
            "k": smooth_value,
            "d": sum(k_values) / d_period if len(k_values) == d_period else None,
        }
