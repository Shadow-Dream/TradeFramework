from builtin_implementations.pipeline.common import number, period, push_window
from strategy_devkit.module_sdk import SignalModule


class VwmaIndicator(SignalModule):
    def update(self, price, volume):
        price = number(price)
        volume = number(volume)
        if price is None or volume is None:
            return {"vwma": None}
        if volume < 0:
            raise ValueError("VWMA received negative volume.")
        size = period(self.config)
        price_volume = push_window(self.state, "priceVolume", price * volume, size)
        volumes = push_window(self.state, "volume", volume, size)
        total_volume = sum(volumes)
        return {
            "vwma": sum(price_volume) / total_volume
            if len(volumes) == size and total_volume
            else None
        }
