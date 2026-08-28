from builtin_implementations.pipeline.common import number, period
from strategy_devkit.module_sdk import SignalModule


class DmiIndicator(SignalModule):
    """Wilder Directional Movement Index with +DI, -DI, and ADX outputs."""

    @staticmethod
    def _empty():
        return {"plusDI": None, "minusDI": None, "adx": None}

    def update(self, high, low, close):
        high, low, close = number(high), number(low), number(close)
        if high is None or low is None or close is None:
            return self._empty()
        if high < low:
            raise ValueError("DMI received a high below low.")

        di_size = period(self.config, "diPeriod", 14)
        adx_size = period(self.config, "adxSmoothing", 14)
        previous_high = self.state.get("previousHigh")
        previous_low = self.state.get("previousLow")
        previous_close = self.state.get("previousClose")

        if previous_high is None:
            positive_movement = 0.0
            negative_movement = 0.0
            true_range = high - low
        else:
            upward = high - previous_high
            downward = previous_low - low
            positive_movement = upward if upward > downward and upward > 0 else 0.0
            negative_movement = downward if downward > upward and downward > 0 else 0.0
            true_range = max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )

        self.state["previousHigh"] = high
        self.state["previousLow"] = low
        self.state["previousClose"] = close
        count = int(self.state.get("movementCount", 0)) + 1
        self.state["movementCount"] = count

        if count <= di_size:
            self.state["trueRangeSum"] = self.state.get("trueRangeSum", 0.0) + true_range
            self.state["positiveMovementSum"] = (
                self.state.get("positiveMovementSum", 0.0) + positive_movement
            )
            self.state["negativeMovementSum"] = (
                self.state.get("negativeMovementSum", 0.0) + negative_movement
            )
            if count < di_size:
                return self._empty()
        else:
            self.state["trueRangeSum"] = (
                self.state["trueRangeSum"]
                - self.state["trueRangeSum"] / di_size
                + true_range
            )
            self.state["positiveMovementSum"] = (
                self.state["positiveMovementSum"]
                - self.state["positiveMovementSum"] / di_size
                + positive_movement
            )
            self.state["negativeMovementSum"] = (
                self.state["negativeMovementSum"]
                - self.state["negativeMovementSum"] / di_size
                + negative_movement
            )

        smoothed_range = self.state["trueRangeSum"]
        if smoothed_range == 0:
            plus_di = minus_di = 0.0
        else:
            plus_di = 100.0 * self.state["positiveMovementSum"] / smoothed_range
            minus_di = 100.0 * self.state["negativeMovementSum"] / smoothed_range

        directional_sum = plus_di + minus_di
        dx = (
            100.0 * abs(plus_di - minus_di) / directional_sum
            if directional_sum
            else 0.0
        )
        if self.state.get("adx") is None:
            dx_values = self.state.setdefault("dxSeed", [])
            dx_values.append(dx)
            if len(dx_values) == adx_size:
                self.state["adx"] = sum(dx_values) / adx_size
        else:
            self.state["adx"] = (
                self.state["adx"] * (adx_size - 1) + dx
            ) / adx_size

        return {
            "plusDI": plus_di,
            "minusDI": minus_di,
            "adx": self.state.get("adx"),
        }
