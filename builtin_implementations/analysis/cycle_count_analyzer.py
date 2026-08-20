from strategy_devkit.analysis_module_sdk import AnalyzerModule


class CycleCountAnalyzer(AnalyzerModule):
    def update(self):
        self.state["count"] = int(self.state.get("count") or 0) + 1
        return {"count": self.state["count"]}
