from builtin_implementations.pipeline.common import number, period, push_window
from strategy_devkit.module_sdk import SignalModule


class AtrIndicator(SignalModule):
    def update(self, high, low, close):
        high, low, close = number(high), number(low), number(close)
        if high is None or low is None or close is None:
            return {"atr": None}
        previous = self.state.get("previousClose")
        true_range = high - low if previous is None else max(high - low, abs(high - previous), abs(low - previous))
        self.state["previousClose"] = close
        size = period(self.config, default=14)
        window = push_window(self.state, "ranges", true_range, size)
        return {"atr": sum(window) / size if len(window) == size else None}
