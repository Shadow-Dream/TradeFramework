from builtin_implementations.pipeline.common import number, period
from strategy_devkit.module_sdk import SignalModule


class MfiIndicator(SignalModule):
    def update(self, high, low, close, volume):
        high, low, close, volume = map(number, (high, low, close, volume))
        if any(value is None for value in (high, low, close, volume)):
            return {"mfi": None}
        if high < low:
            raise ValueError("MFI received a high below low.")
        if volume < 0:
            raise ValueError("MFI received negative volume.")
        size = period(self.config, default=14)
        typical = (high + low + close) / 3
        previous = self.state.get("typical")
        self.state["typical"] = typical
        positive = typical * volume if previous is not None and typical > previous else 0.0
        negative = typical * volume if previous is not None and typical < previous else 0.0
        for key, value in (("positive", positive), ("negative", negative)):
            values = self.state.setdefault(key, [])
            values.append(value)
            if len(values) > size:
                values.pop(0)
        if len(self.state["positive"]) < size:
            return {"mfi": None}
        positive_sum = sum(self.state["positive"])
        negative_sum = sum(self.state["negative"])
        if negative_sum == 0:
            return {"mfi": 100.0 if positive_sum > 0 else 0.0}
        ratio = positive_sum / negative_sum
        return {"mfi": 100.0 - 100.0 / (1.0 + ratio)}
