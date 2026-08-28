from builtin_implementations.pipeline.common import number, period, push_window
from strategy_devkit.module_sdk import SignalModule


class RsiIndicator(SignalModule):
    def update(self, price):
        price = number(price)
        if price is None:
            return {"rsi": None}
        size = period(self.config, default=14)
        previous = self.state.get("previous")
        self.state["previous"] = price
        if previous is None:
            return {"rsi": None}
        delta = price - previous
        gains = push_window(self.state, "gains", max(delta, 0.0), size)
        losses = push_window(self.state, "losses", max(-delta, 0.0), size)
        if len(gains) < size:
            return {"rsi": None}
        if "avgGain" not in self.state:
            self.state["avgGain"] = sum(gains) / size
            self.state["avgLoss"] = sum(losses) / size
        else:
            self.state["avgGain"] = (self.state["avgGain"] * (size - 1) + gains[-1]) / size
            self.state["avgLoss"] = (self.state["avgLoss"] * (size - 1) + losses[-1]) / size
        average_gain = self.state["avgGain"]
        average_loss = self.state["avgLoss"]
        if average_loss == 0:
            return {"rsi": 100.0 if average_gain else 0.0}
        relative_strength = average_gain / average_loss
        return {"rsi": 100.0 - 100.0 / (1.0 + relative_strength)}
