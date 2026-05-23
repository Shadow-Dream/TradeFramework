#!/usr/bin/env python3
from strategy_devkit import LeanStrategy, equity, insight, order, run, target_percent


class Strategy(LeanStrategy):
    spy = equity("SPY")
    qqq = equity("QQQ")

    def inputs(self, context):
        return [
            {"symbol": self.spy, "resolution": "Daily"},
            {"symbol": self.qqq, "resolution": "Daily"},
        ]

    def universe(self, context):
        return [self.spy, self.qqq]

    def signals(self, context):
        day = context["time"].strftime("%Y-%m-%d") if context["time"] else ""
        if day in {"2014-06-06", "2014-06-16"}:
            return [
                insight(self.spy, "Up", source_model="FullDemo"),
                insight(self.qqq, "Up", source_model="FullDemo"),
            ]
        if day in {"2014-06-10", "2014-06-17"}:
            return [
                insight(self.spy, "Flat", source_model="FullDemo"),
                insight(self.qqq, "Flat", source_model="FullDemo"),
            ]
        return []

    def targets(self, context, insights):
        targets = []
        for item in insights:
            direction = item.get("direction")
            percent = 0.35 if direction == "Up" else 0
            targets.append(target_percent(item["symbol"], percent, "FULL_DEMO_TARGET"))
        return targets

    def risk(self, context, targets):
        result = []
        for item in targets:
            result.append({
                "symbol": item["symbol"],
                "quantity": item.get("quantity", 0) * 0.75,
                "tag": (item.get("tag") or "") + "|FULL_DEMO_RISK",
            })
        return result

    def execute(self, context, targets):
        return [
            order(item["symbol"], item.get("quantity", 0), (item.get("tag") or "") + "|FULL_DEMO_EXEC")
            for item in targets
        ]

    def market_rule(self, context, command, payload):
        return {
            "allowed": True,
            "leverage": 2,
            "fee": 0.001,
            "slippage": 0.0001,
            "marker": f"FULL_DEMO_MARKET:{command}",
            "message": "",
        }

    def analyze(self, observations):
        return {
            "marker": "FULL_DEMO_ANALYZER",
            "values": {
                "status": observations.get("state.status", ""),
                "orders": observations.get("orders.count", 0),
            },
            "requiredObservations": ["state.status", "orders.count"],
        }


if __name__ == "__main__":
    run(Strategy())
