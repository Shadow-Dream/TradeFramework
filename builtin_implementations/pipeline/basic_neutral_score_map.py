from strategy_devkit.module_sdk import SignalModule


class BasicNeutralScoreMap(SignalModule):
    def update(self, selection):
        if type(selection) is not dict or any(value is not True for value in selection.values()):
            raise ValueError("Basic Workflow selection must map instruments to true.")
        return {"scores": {instrument: None for instrument in sorted(selection)}}
