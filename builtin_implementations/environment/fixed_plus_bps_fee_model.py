from application_components.basic_workflow.brokerage import fixed_plus_bps_fee
from strategy_devkit.environment_module_sdk import EnvironmentModule


class FixedPlusBpsFeeModel(EnvironmentModule):
    def update(self, filledQuantity, notional):
        return {
            "fee": fixed_plus_bps_fee(
                filledQuantity,
                notional,
                self.config.get("fixedFee", 0.0),
                self.config.get("feeBps", 0.0),
            )
        }
