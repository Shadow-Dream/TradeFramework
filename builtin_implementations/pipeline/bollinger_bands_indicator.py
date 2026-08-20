import math

from builtin_implementations.pipeline.common import number, period, push_window
from strategy_devkit.module_sdk import SignalModule


class BollingerBandsIndicator(SignalModule):
    def update(self, price):
        price = number(price)
        empty = {"middle": None, "upper": None, "lower": None, "bandwidth": None, "percentB": None}
        if price is None:
            return empty
        size = period(self.config)
        multiplier = float(self.config.get("k", 2))
        window = push_window(self.state, "values", price, size)
        if len(window) < size:
            return empty
        middle = sum(window) / size
        deviation = math.sqrt(sum((item - middle) ** 2 for item in window) / size)
        upper = middle + multiplier * deviation
        lower = middle - multiplier * deviation
        return {
            "middle": middle,
            "upper": upper,
            "lower": lower,
            "bandwidth": (upper - lower) / middle if middle else None,
            "percentB": (price - lower) / (upper - lower) if upper != lower else None,
        }
