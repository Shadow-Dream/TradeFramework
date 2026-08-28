from builtin_implementations.pipeline.common import number, period
from strategy_devkit.module_sdk import SignalModule


class SupertrendIndicator(SignalModule):
    def update(self, high, low, close):
        high, low, close = number(high), number(low), number(close)
        if high is None or low is None or close is None:
            return {"supertrend": None, "direction": None}
        if high < low:
            raise ValueError("Supertrend received a high below low.")
        size = period(self.config, "atrPeriod", 10)
        multiplier = float(self.config.get("multiplier", 3.0))
        previous_close = self.state.get("close")
        true_range = (
            high - low
            if previous_close is None
            else max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )
        self.state["close"] = close
        ranges = self.state.setdefault("ranges", [])
        ranges.append(true_range)
        if len(ranges) > size:
            ranges.pop(0)
        if len(ranges) < size:
            return {"supertrend": None, "direction": None}
        if "atr" not in self.state:
            self.state["atr"] = sum(ranges) / size
        else:
            self.state["atr"] = (
                self.state["atr"] * (size - 1) + true_range
            ) / size

        midpoint = (high + low) / 2.0
        basic_upper = midpoint + multiplier * self.state["atr"]
        basic_lower = midpoint - multiplier * self.state["atr"]
        previous_upper = self.state.get("upper")
        previous_lower = self.state.get("lower")
        if previous_upper is None:
            upper, lower = basic_upper, basic_lower
            # TradingView specifies downtrend until ATR first becomes defined.
            direction = "down"
        else:
            upper = (
                basic_upper
                if basic_upper < previous_upper or previous_close > previous_upper
                else previous_upper
            )
            lower = (
                basic_lower
                if basic_lower > previous_lower or previous_close < previous_lower
                else previous_lower
            )
            direction = self.state["direction"]
            if direction == "down" and close > upper:
                direction = "up"
            elif direction == "up" and close < lower:
                direction = "down"
        self.state.update(upper=upper, lower=lower, direction=direction)
        return {"supertrend": lower if direction == "up" else upper, "direction": direction}
