from builtin_implementations.pipeline.common import number, period, push_window
from strategy_devkit.module_sdk import SignalModule


class SmaIndicator(SignalModule):
    def update(self, value):
        value = number(value)
        if value is None:
            return {"sma": None}
        size = period(self.config)
        window = push_window(self.state, "values", value, size)
        return {"sma": sum(window) / len(window) if len(window) == size else None}
