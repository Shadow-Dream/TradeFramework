from builtin_implementations.pipeline.common import number, period
from strategy_devkit.module_sdk import SignalModule


class EmaIndicator(SignalModule):
    def update(self, value):
        value = number(value)
        if value is None:
            return {"ema": None}
        alpha = 2.0 / (period(self.config) + 1.0)
        self.state["ema"] = value if self.state.get("ema") is None else (
            value * alpha + self.state["ema"] * (1.0 - alpha)
        )
        return {"ema": self.state["ema"]}
