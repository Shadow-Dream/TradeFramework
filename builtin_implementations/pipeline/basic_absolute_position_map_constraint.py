import math

from strategy_devkit.module_sdk import ConstraintModule


def _finite(value, label):
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"Basic Workflow {label} must be a finite number.")
    return float(value)


class BasicAbsolutePositionMapConstraint(ConstraintModule):
    def update(self, intent):
        if type(intent) is not dict:
            raise ValueError("Basic Workflow intent must be an object map.")
        maximum = _finite(
            self.config.get("maximumAbsolutePosition", 1.0),
            "maximumAbsolutePosition",
        )
        if maximum < 0:
            raise ValueError("Basic Workflow maximumAbsolutePosition cannot be negative.")
        approved = {}
        for instrument, position in sorted(intent.items()):
            position = _finite(position, f"intent.{instrument}")
            if abs(position) <= maximum:
                approved[instrument] = position
        return {"approved": approved}
