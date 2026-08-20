from application_components.basic_workflow.brokerage import constant_bps_fill_price
from strategy_devkit.environment_module_sdk import EnvironmentModule


class ConstantBpsSlippageModel(EnvironmentModule):
    def update(self, approvedQuantity, executionValue):
        return {
            "fillValue": constant_bps_fill_price(
                approvedQuantity,
                executionValue,
                self.config.get("slippageBps", 0.0),
            )
        }
