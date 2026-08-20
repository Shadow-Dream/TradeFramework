from builtin_implementations.environment.common import finite
from strategy_devkit.environment_module_sdk import EnvironmentModule


class NumericTargetSubmitRule(EnvironmentModule):
    def update(self, intent=None):
        return {"target": None if intent is None else finite(intent, "intent")}
