from builtin_implementations.pipeline.common import number
from strategy_devkit.module_sdk import SignalModule


class ParabolicSarIndicator(SignalModule):
    def on_initialize(self):
        start = float(self.config.get("start", 0.02))
        increment = float(self.config.get("increment", 0.02))
        maximum = float(self.config.get("maximum", 0.2))
        if maximum < start or maximum < increment:
            raise ValueError("maximum must be at least start and increment")

    def update(self, high, low, close):
        high, low, close = map(number, (high, low, close))
        if any(value is None for value in (high, low, close)):
            return {"sar": None, "direction": None}
        if high < low:
            raise ValueError("Parabolic SAR received a high below low.")
        state = self.state
        start = float(self.config.get("start", 0.02))
        increment = float(self.config.get("increment", 0.02))
        maximum = float(self.config.get("maximum", 0.2))

        if "firstClose" not in state:
            state.update(firstClose=close, previousHigh=high, previousLow=low)
            return {"sar": None, "direction": None}
        if "sar" not in state:
            direction = "up" if close > state["firstClose"] else "down"
            sar = state["previousLow"] if direction == "up" else state["previousHigh"]
            extreme = (
                max(state["previousHigh"], high)
                if direction == "up"
                else min(state["previousLow"], low)
            )
            state.update(
                sar=sar,
                extreme=extreme,
                acceleration=start,
                direction=direction,
                secondPreviousHigh=state["previousHigh"],
                secondPreviousLow=state["previousLow"],
                previousHigh=high,
                previousLow=low,
            )
            return {"sar": sar, "direction": direction}

        sar = state["sar"] + state["acceleration"] * (
            state["extreme"] - state["sar"]
        )
        direction = state["direction"]
        if direction == "up" and low < sar:
            direction = "down"
            sar = state["extreme"]
            state["extreme"] = low
            state["acceleration"] = start
        elif direction == "down" and high > sar:
            direction = "up"
            sar = state["extreme"]
            state["extreme"] = high
            state["acceleration"] = start
        else:
            if direction == "up":
                sar = min(
                    sar,
                    state["previousLow"],
                    state["secondPreviousLow"],
                )
                if high > state["extreme"]:
                    state["extreme"] = high
                    state["acceleration"] = min(
                        maximum,
                        state["acceleration"] + increment,
                    )
            else:
                sar = max(
                    sar,
                    state["previousHigh"],
                    state["secondPreviousHigh"],
                )
                if low < state["extreme"]:
                    state["extreme"] = low
                    state["acceleration"] = min(
                        maximum,
                        state["acceleration"] + increment,
                    )
        state.update(
            sar=sar,
            direction=direction,
            secondPreviousHigh=state["previousHigh"],
            secondPreviousLow=state["previousLow"],
            previousHigh=high,
            previousLow=low,
        )
        return {"sar": sar, "direction": direction}
