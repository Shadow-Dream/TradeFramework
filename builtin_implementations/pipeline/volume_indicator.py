from builtin_implementations.pipeline.common import number
from strategy_devkit.module_sdk import SignalModule


class VolumeIndicator(SignalModule):
    def update(self, volume):
        volume = number(volume)
        if volume is not None and volume < 0:
            raise ValueError("Volume indicator received negative volume.")
        return {"volume": volume}
