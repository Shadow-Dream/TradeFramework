from builtin_implementations.pipeline.common import number, period, push_window
from strategy_devkit.module_sdk import SignalModule


class WilliamsRIndicator(SignalModule):
    """Williams Percent Range using the current close and lookback extremes."""

    def update(self, high, low, close):
        high, low, close = number(high), number(low), number(close)
        if high is None or low is None or close is None:
            return {"williamsR": None}
        if high < low:
            raise ValueError("Williams %R received a high below low.")

        size = period(self.config, default=14)
        highs = push_window(self.state, "highs", high, size)
        lows = push_window(self.state, "lows", low, size)
        if len(highs) < size:
            return {"williamsR": None}

        highest, lowest = max(highs), min(lows)
        price_range = highest - lowest
        if price_range == 0:
            return {"williamsR": None}
        return {"williamsR": -100.0 * (highest - close) / price_range}
