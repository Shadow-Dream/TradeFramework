from builtin_implementations.pipeline.common import number
from strategy_devkit.module_sdk import SignalModule


class CrossOverGate(SignalModule):
    def update(self, fast, slow):
        fast, slow = number(fast), number(slow)
        if fast is None or slow is None:
            return {"direction": None}
        current = 1 if fast > slow else -1 if fast < slow else 0
        previous = int(self.state.get("previous") or 0)
        direction = "rise" if current > 0 and previous <= 0 else "fall" if current < 0 and previous >= 0 else "flat"
        if current:
            self.state["previous"] = current
        return {"direction": direction}
