from application_components.basic_workflow.account import single_asset_account
from strategy_devkit.environment_module_sdk import EnvironmentModule


class PaperAccountLedger(EnvironmentModule):
    def update(self, executionValue, previousAccount=None, settlement=None, marginInterest=0.0):
        return {
            "account": single_asset_account(
                previousAccount,
                settlement,
                executionValue,
                marginInterest,
                initial_cash=self.config.get("initialCash", 100000.0),
                initial_position=self.config.get("initialPosition", 0.0),
            )
        }
