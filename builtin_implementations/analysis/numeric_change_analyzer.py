from strategy_devkit.analysis_module_sdk import AnalyzerModule


class NumericChangeAnalyzer(AnalyzerModule):
    def update(self, current, previous=None):
        current = float(current)
        if previous is None:
            return {"change": None, "return": None}
        previous = float(previous)
        return {
            "change": current - previous,
            "return": None if previous == 0 else current / previous - 1.0,
        }
