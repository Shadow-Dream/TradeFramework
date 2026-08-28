from builtin_implementations.pipeline.common import number, period, push_window
from strategy_devkit.module_sdk import SignalModule


class IchimokuIndicator(SignalModule):
    def update(self, high, low, close):
        high, low, close = map(number, (high, low, close))
        if any(value is None for value in (high, low, close)):
            return {
                "conversion": None,
                "base": None,
                "spanA": None,
                "spanB": None,
                "lagging": None,
            }
        if high < low:
            raise ValueError("Ichimoku received a high below low.")
        conversion_size = period(self.config, "conversionPeriod", 9)
        base_size = period(self.config, "basePeriod", 26)
        span_size = period(self.config, "spanBPeriod", 52)
        window_size = max(conversion_size, base_size, span_size)
        highs = push_window(self.state, "highs", high, window_size)
        lows = push_window(self.state, "lows", low, window_size)

        def midpoint(size):
            if len(highs) < size:
                return None
            return (max(highs[-size:]) + min(lows[-size:])) / 2

        conversion = midpoint(conversion_size)
        base = midpoint(base_size)
        span_b = midpoint(span_size)
        span_a = (
            (conversion + base) / 2
            if conversion is not None and base is not None
            else None
        )
        return {"conversion": conversion, "base": base, "spanA": span_a, "spanB": span_b, "lagging": close}
