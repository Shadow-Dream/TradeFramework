from builtin_implementations.pipeline.common import number
from strategy_devkit.module_sdk import SignalModule


class ObvIndicator(SignalModule):
    def update(self, close, volume):
        close, volume = number(close), number(volume)
        if close is None or volume is None:
            return {"obv": None}
        previous = self.state.get("previousClose")
        self.state["previousClose"] = close
        self.state.setdefault("obv", 0.0)
        if previous is not None and close > previous:
            self.state["obv"] += volume
        elif previous is not None and close < previous:
            self.state["obv"] -= volume
        return {"obv": self.state["obv"]}
