from builtin_implementations.pipeline.common import number, period, push_window
from strategy_devkit.module_sdk import SignalModule


class CciIndicator(SignalModule):
    """Commodity Channel Index over one explicitly bound source series.

    The caller chooses the source (for example close or HLC3) in graph wiring.
    The implementation follows TradingView's documented 0.015 scaling formula
    and returns ``None`` until the complete lookback window is available.
    """

    def update(self, source):
        source = number(source)
        if source is None:
            return {"cci": None}

        size = period(self.config, default=20)
        window = push_window(self.state, "sources", source, size)
        if len(window) < size:
            return {"cci": None}

        average = sum(window) / size
        mean_deviation = sum(abs(item - average) for item in window) / size
        if mean_deviation == 0:
            return {"cci": None}
        return {"cci": (source - average) / (0.015 * mean_deviation)}
