from application_components.basic_workflow.brokerage import apply_shortable_rule
from strategy_devkit.environment_module_sdk import EnvironmentModule


class ConfigurableShortableProvider(EnvironmentModule):
    def update(self, target):
        return {
            "target": apply_shortable_rule(
                target,
                self.config.get("allowShort", True),
            )
        }
