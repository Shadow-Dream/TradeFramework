from builtin_implementations.pipeline.common import number, period
from strategy_devkit.module_sdk import SignalModule


class MacdIndicator(SignalModule):
    def update(self, price):
        price = number(price)
        if price is None:
            return {"macd": None, "signal": None, "histogram": None}

        def update_ema(name, value, size):
            alpha = 2.0 / (size + 1.0)
            self.state[name] = value if self.state.get(name) is None else (
                value * alpha + self.state[name] * (1.0 - alpha)
            )
            return self.state[name]

        fast = update_ema("fast", price, period(self.config, "fastPeriod", 12))
        slow = update_ema("slow", price, period(self.config, "slowPeriod", 26))
        value = fast - slow
        signal = update_ema("signal", value, period(self.config, "signalPeriod", 9))
        return {"macd": value, "signal": signal, "histogram": value - signal}
