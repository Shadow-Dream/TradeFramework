from builtin_implementations.pipeline.common import number, period
from strategy_devkit.module_sdk import SignalModule


class RocIndicator(SignalModule):
    def update(self, price):
        price = number(price)
        if price is None:
            return {"roc": None}
        size = period(self.config, default=12)
        values = self.state.setdefault("values", [])
        values.append(price)
        if len(values) <= size:
            return {"roc": None}
        previous = values.pop(0)
        return {"roc": price / previous - 1.0 if previous else None}
