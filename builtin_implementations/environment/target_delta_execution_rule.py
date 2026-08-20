from application_components.basic_workflow.brokerage import target_delta
from application_components.basic_workflow.numbers import finite
from strategy_devkit.environment_module_sdk import EnvironmentModule


class TargetDeltaExecutionRule(EnvironmentModule):
    def update(self, target=None, accountPosition=None):
        position = finite(
            self.config.get("initialPosition", 0.0) if accountPosition is None else accountPosition,
            "accountPosition",
        )
        return target_delta(
            target,
            position,
            self.config.get("maximumOrderQuantity", 1000000.0),
        )
