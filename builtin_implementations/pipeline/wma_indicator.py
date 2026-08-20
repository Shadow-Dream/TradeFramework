from builtin_implementations.pipeline.common import number, period, push_window
from strategy_devkit.module_sdk import SignalModule


class WmaIndicator(SignalModule):
    def update(self, value):
        value = number(value)
        if value is None:
            return {"wma": None}
        size = period(self.config)
        window = push_window(self.state, "values", value, size)
        if len(window) < size:
            return {"wma": None}
        divisor = sum(range(1, len(window) + 1))
        return {"wma": sum(item * (index + 1) for index, item in enumerate(window)) / divisor}
