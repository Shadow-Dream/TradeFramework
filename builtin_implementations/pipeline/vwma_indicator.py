from builtin_implementations.pipeline.common import number, period, push_window
from strategy_devkit.module_sdk import SignalModule


class VwmaIndicator(SignalModule):
    def update(self, price, volume):
        price = number(price)
        volume = number(volume)
        if price is None or volume is None:
            return {"vwma": None}
        size = period(self.config)
        price_volume = push_window(self.state, "priceVolume", price * volume, size)
        volumes = push_window(self.state, "volume", volume, size)
        return {"vwma": sum(price_volume) / sum(volumes) if len(volumes) == size and sum(volumes) else None}
