from builtin_implementations.pipeline.common import number
from strategy_devkit.module_sdk import SignalModule


class AnchoredVwapIndicator(SignalModule):
    def update(self, source, volume, reset):
        source, volume = number(source), number(volume)
        if not isinstance(reset, bool):
            raise ValueError("VWAP reset must be boolean.")
        if source is None or volume is None:
            return {"vwap": None}
        if volume < 0:
            raise ValueError("VWAP received negative volume.")
        if reset:
            self.state["pv"], self.state["v"] = 0.0, 0.0
        self.state["pv"] = self.state.get("pv", 0.0) + source * volume
        self.state["v"] = self.state.get("v", 0.0) + volume
        return {
            "vwap": self.state["pv"] / self.state["v"]
            if self.state["v"]
            else None
        }
