from application_components.basic_workflow.brokerage import immediate_settlement
from strategy_devkit.environment_module_sdk import EnvironmentModule


class ImmediateSettlementModel(EnvironmentModule):
    def update(self, notional, filledQuantity, fee=0.0):
        return {
            "settlement": immediate_settlement(
                notional,
                filledQuantity,
                fee,
            )
        }
