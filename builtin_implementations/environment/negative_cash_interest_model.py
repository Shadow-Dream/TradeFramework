from application_components.basic_workflow.brokerage import negative_cash_interest
from strategy_devkit.environment_module_sdk import EnvironmentModule


class NegativeCashInterestModel(EnvironmentModule):
    def update(self, accountCash):
        return {
            "marginInterest": negative_cash_interest(
                accountCash,
                self.config.get("marginInterestPerCycle", 0.0),
            )
        }
