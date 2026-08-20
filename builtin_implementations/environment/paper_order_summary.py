from builtin_implementations.environment.common import finite
from strategy_devkit.environment_module_sdk import EnvironmentModule


class PaperOrderSummary(EnvironmentModule):
    def update(
        self,
        approvedTarget,
        requestedQuantity,
        filledQuantity,
        executionValue,
        fillValue,
        notional,
        status,
        requestedTarget=None,
        fee=0.0,
    ):
        if requestedTarget is None:
            requestedTarget = approvedTarget
        return {"order": {
            "status": str(status),
            "requestedTarget": finite(requestedTarget, "requestedTarget"),
            "approvedTarget": finite(approvedTarget, "approvedTarget"),
            "requestedQuantity": finite(requestedQuantity, "requestedQuantity"),
            "filledQuantity": finite(filledQuantity, "filledQuantity"),
            "sampleValue": finite(executionValue, "executionValue"),
            "fillValue": None if fillValue is None else finite(fillValue, "fillValue"),
            "notional": finite(notional, "notional"),
            "fee": finite(fee, "fee"),
        }}
