from builtin_implementations.environment.common import finite
from strategy_devkit.environment_module_sdk import EnvironmentModule


class DataKeyBenchmarkProvider(EnvironmentModule):
    def update(self, value):
        value = finite(value, "benchmark value")
        self.state.setdefault("initialValue", value)
        initial = self.state["initialValue"]
        return {"benchmark": {"value": value, "return": 0.0 if initial == 0 else value / initial - 1}}
