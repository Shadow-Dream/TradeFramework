from application_components.basic_workflow.brokerage import proportional_fill
from strategy_devkit.environment_module_sdk import EnvironmentModule


class ProportionalFillModel(EnvironmentModule):
    def update(self, approvedQuantity, fillValue):
        return proportional_fill(
            approvedQuantity,
            fillValue,
            self.config.get("fillRatio", 1.0),
        )
