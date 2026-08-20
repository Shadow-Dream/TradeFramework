from application_components.basic_workflow.brokerage import apply_leverage_limit
from strategy_devkit.environment_module_sdk import EnvironmentModule


class MaximumLeverageRule(EnvironmentModule):
    def update(self, target, accountEquity, executionValue):
        return {
            "target": apply_leverage_limit(
                target,
                accountEquity,
                executionValue,
                self.config.get("maximumLeverage", 1.0),
            )
        }
