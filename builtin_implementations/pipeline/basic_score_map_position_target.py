import math

from strategy_devkit.module_sdk import TargetModule


def _finite(value, label):
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"Basic Workflow {label} must be a finite number.")
    return float(value)


class BasicScoreMapPositionTarget(TargetModule):
    def update(self, selection, scores):
        if type(selection) is not dict or any(value is not True for value in selection.values()):
            raise ValueError("Basic Workflow selection must map instruments to true.")
        if type(scores) is not dict:
            raise ValueError("Basic Workflow scores must be an object map.")
        maximum = _finite(
            self.config.get("maximumAbsolutePosition", 1.0),
            "maximumAbsolutePosition",
        )
        if maximum < 0:
            raise ValueError("Basic Workflow maximumAbsolutePosition cannot be negative.")
        intent = {}
        for instrument in sorted(selection):
            score = scores.get(instrument)
            if score is None:
                continue
            score = _finite(score, f"scores.{instrument}")
            if score < -1.0 or score > 1.0:
                raise ValueError("Basic Workflow scores must be within [-1, 1].")
            intent[instrument] = score * maximum
        return {"intent": intent}
