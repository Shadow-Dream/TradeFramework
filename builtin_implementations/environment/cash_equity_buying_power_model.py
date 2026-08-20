from application_components.basic_workflow.brokerage import apply_cash_buying_power
from strategy_devkit.environment_module_sdk import EnvironmentModule


class CashEquityBuyingPowerModel(EnvironmentModule):
    def update(self, target, accountEquity, executionValue):
        return {
            "target": apply_cash_buying_power(
                target,
                accountEquity,
                executionValue,
            )
        }
