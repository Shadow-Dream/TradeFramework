#!/usr/bin/env python3
"""Independently verify the SPX golden-cross acceptance backtest."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path


def close_enough(actual, expected):
    return math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-9)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def audit(result):
    require(result.get("schemaVersion") == 8, "Result schemaVersion 8 is required.")
    cycles = result["cycles"]
    require(cycles, "Result has no cycles.")
    closes = []
    previous_long = None
    previous_position = 0.0
    previous_cash = 100000.0
    cross_events = []
    filled_orders = 0
    no_change_orders = 0
    no_intent_orders = 0
    sample_fields = set()

    for index, cycle in enumerate(cycles):
        require(cycle.get("schemaVersion") == 3, f"cycle {index} schema is invalid")
        data = cycle["data"]
        parse_time(cycle["decisionTime"])
        price = data["price"]
        sample_fields.update(f"price.{name}" for name in price)
        close = float(price["close"])
        closes.append(close)
        policy = data["policy"]["golden_cross"]
        outputs = data["audit"]
        order = outputs["order"]
        account = outputs["account"]

        require(close_enough(policy["observed_close"], close), f"cycle {index} module close differs from Sampler close")
        require(policy["module_cycle"] == index + 1, f"cycle {index} module state sequence mismatch")

        expected_fast = None if len(closes) < 20 else sum(closes[-20:]) / 20
        expected_slow = None if len(closes) < 50 else sum(closes[-50:]) / 50
        require(
            (policy["fast_sma"] is None and expected_fast is None)
            or (policy["fast_sma"] is not None and close_enough(policy["fast_sma"], expected_fast)),
            f"cycle {index} fast SMA mismatch",
        )
        require(
            (policy["slow_sma"] is None and expected_slow is None)
            or (policy["slow_sma"] is not None and close_enough(policy["slow_sma"], expected_slow)),
            f"cycle {index} slow SMA mismatch",
        )
        if expected_slow is None:
            expected_target = None
            expected_cross = "warming"
        else:
            current_long = expected_fast > expected_slow
            expected_target = 1.0 if current_long else 0.0
            if previous_long is None:
                expected_cross = "golden_cross" if current_long else "hold_flat"
            elif current_long and not previous_long:
                expected_cross = "golden_cross"
            elif not current_long and previous_long:
                expected_cross = "death_cross"
            else:
                expected_cross = "hold_long" if current_long else "hold_flat"
            previous_long = current_long
        require(policy["cross"] == expected_cross, f"cycle {index} cross label mismatch")
        require(
            (policy["target_position"] is None and expected_target is None)
            or close_enough(policy["target_position"], expected_target),
            f"cycle {index} target mismatch",
        )
        if expected_cross in {"golden_cross", "death_cross"}:
            cross_events.append({
                "cycle": index,
                "eventTime": data["market"]["event_time"],
                "decisionTime": cycle["decisionTime"],
                "cross": expected_cross,
                "close": close,
                "target": expected_target,
            })

        require(close_enough(order["sampleValue"], close), f"cycle {index} execution did not use current sample")
        previous_target = None if index == 0 else cycles[index - 1]["data"]["policy"]["golden_cross"]["target_position"]
        if previous_target is None:
            no_intent_orders += 1
            require(order["status"] == "no-intent", f"cycle {index} warm-up should have no intent")
            require(order["fillValue"] is None, f"cycle {index} no-intent has a fill value")
            expected_position = previous_position
            expected_cash = previous_cash
            expected_trace = ["order-submit"]
        else:
            require(close_enough(order["requestedTarget"], previous_target), f"cycle {index} did not consume previous target")
            requested_quantity = float(previous_target) - previous_position
            require(close_enough(order["requestedQuantity"], requested_quantity), f"cycle {index} quantity mismatch")
            if close_enough(requested_quantity, 0.0):
                no_change_orders += 1
                require(order["status"] == "no-change", f"cycle {index} zero order is not no-change")
                require(order["fillValue"] is None, f"cycle {index} zero order has a fill value")
                expected_position = previous_position
                expected_cash = previous_cash
                expected_trace = ["order-submit", "order-execution"]
            else:
                filled_orders += 1
                require(order["status"] == "filled", f"cycle {index} nonzero order did not fill")
                require(close_enough(order["filledQuantity"], requested_quantity), f"cycle {index} fill quantity mismatch")
                require(close_enough(order["fillValue"], close), f"cycle {index} fill did not use current close")
                expected_position = float(previous_target)
                expected_cash = previous_cash - requested_quantity * close
                expected_trace = ["order-submit", "order-execution", "fill", "settlement"]
        require(outputs["module_trace"] == expected_trace, f"cycle {index} module trace mismatch")
        require(close_enough(account["position"], expected_position), f"cycle {index} account position mismatch")
        require(close_enough(account["cash"], expected_cash), f"cycle {index} account cash mismatch")
        previous_position = expected_position
        previous_cash = expected_cash

    chain = result["executionChain"]
    pipeline = chain["pipeline"]
    require(chain["dataset"]["datasetId"] == "spx-cfd-spreadex-1d-20260628", "wrong Dataset")
    require(chain["sampler"]["samplerId"] == "nested-row", "wrong Sampler")
    require(chain["environment"]["dataInterface"] == "declared-datakey-contracts", "Environment DataKey contract is missing")
    require(pipeline["dataInterface"] == "declared-datakey-contracts", "Pipeline DataKey contract is missing")
    transport = pipeline["moduleTransports"]["golden-cross-policy"]
    require(
        transport["runtimeMode"] == "in-process-python",
        "standard Python Module did not use the direct Runtime",
    )
    require("protocolVersion" not in transport, "in-process Module exposed a transport protocol")
    require(
        any(
            path == "price" or path.startswith("price.")
            for path in pipeline["observationInput"]["whitelist"]
        ),
        "Pipeline Observation projection omitted price",
    )
    require(transport["invocationCount"] == len(cycles), "Signal invocation count mismatch")
    require(result["metrics"]["cycleCount"] == len(cycles), "cycle count mismatch")
    require(
        result["sampleFrameContract"]["causalityRule"]
        == "Sampler owns decisionTime and as-of visibility",
        "Sampler causality evidence is missing",
    )
    require(
        cycles[-1]["data"]["backtest"]["analysis"]["completedCycleCount"] == len(cycles) - 1,
        "analysis module completed cycle count mismatch",
    )
    require(
        {"price.open", "price.high", "price.low", "price.close", "price.volume"} <= sample_fields,
        "Sampler output omitted ordinary current-cycle fields",
    )
    return {
        "status": "PASS",
        "cycleCount": len(cycles),
        "completedExecutionCount": len(cycles) - 1,
        "sdkInvocations": transport["invocationCount"],
        "observationInput": pipeline["observationInput"],
        "crossEventCount": len(cross_events),
        "goldenCrossCount": sum(item["cross"] == "golden_cross" for item in cross_events),
        "deathCrossCount": sum(item["cross"] == "death_cross" for item in cross_events),
        "filledOrderCount": filled_orders,
        "noChangeCount": no_change_orders,
        "noIntentWarmupCount": no_intent_orders,
        "firstEvents": cross_events[:6],
        "lastEvents": cross_events[-3:],
        "finalAccount": cycles[-1]["data"]["audit"]["account"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path, help="Path to one immutable result.json")
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    print(json.dumps(audit(result), indent=2))


if __name__ == "__main__":
    main()
