from application_components.basic_workflow.brokerage import round_target_to_lot
from strategy_devkit.environment_module_sdk import EnvironmentModule


class LotSizeOrderUpdateRule(EnvironmentModule):
    def update(self, target):
        return {
            "target": round_target_to_lot(
                target,
                self.config.get("lotSize", 1.0),
            )
        }
