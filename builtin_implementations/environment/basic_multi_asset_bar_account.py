import math
import re

from strategy_devkit.environment_module_sdk import EnvironmentModule


_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")


def _finite(value, label):
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"Basic Workflow {label} must be a finite number.")
    return float(value)


class BasicMultiAssetBarAccount(EnvironmentModule):
    def update(self, time, price, previousApprovedIntent=None):
        if type(time) is not str or not time:
            raise ValueError("Basic Workflow time must be a non-empty string.")
        if type(price) is not dict:
            raise ValueError("Basic Workflow price must be an object map.")
        if previousApprovedIntent is not None and type(previousApprovedIntent) is not dict:
            raise ValueError("Basic Workflow previous approved intent must be an object map.")
        execution_period = self.config.get("executionPeriod")
        if type(execution_period) is not str or not _SEGMENT.fullmatch(execution_period):
            raise ValueError("Basic Workflow executionPeriod is invalid.")
        bars = price.get(execution_period)
        if type(bars) is not dict:
            raise ValueError("Basic Workflow executionPeriod is absent from price.")

        initial_cash = _finite(self.config.get("initialCash", 100000.0), "initialCash")
        fixed_fee = _finite(self.config.get("fixedFee", 0.0), "fixedFee")
        fee_bps = _finite(self.config.get("feeBps", 0.0), "feeBps")
        if fixed_fee < 0 or fee_bps < 0:
            raise ValueError("Basic Workflow fees cannot be negative.")

        cash = _finite(self.state.get("cash", initial_cash), "account.cash")
        stored_positions = self.state.get("positions", {})
        if type(stored_positions) is not dict:
            raise ValueError("Basic Workflow account positions state is invalid.")
        positions = {
            instrument: _finite(position, f"positions.{instrument}")
            for instrument, position in stored_positions.items()
        }
        orders = {}
        approved = previousApprovedIntent or {}
        for instrument, target in sorted(approved.items()):
            if type(instrument) is not str or not _SEGMENT.fullmatch(instrument):
                raise ValueError("Basic Workflow approved intent instrument is invalid.")
            target = _finite(target, f"previousApprovedIntent.{instrument}")
            bar = bars.get(instrument)
            if type(bar) is not dict:
                raise ValueError(
                    f"Basic Workflow execution bar is missing for '{instrument}'."
                )
            open_price = _finite(bar.get("open"), f"price.{execution_period}.{instrument}.open")
            if open_price <= 0:
                raise ValueError("Basic Workflow execution price must be positive.")
            current = positions.get(instrument, 0.0)
            quantity = target - current
            if quantity == 0:
                continue
            notional = quantity * open_price
            fee = fixed_fee + abs(notional) * fee_bps / 10000.0
            cash -= notional + fee
            positions[instrument] = target
            orders[instrument] = {
                "side": "buy" if quantity > 0 else "sell",
                "quantity": abs(quantity),
                "price": open_price,
                "fee": fee,
            }

        equity = cash
        for instrument, position in sorted(positions.items()):
            if position == 0:
                continue
            bar = bars.get(instrument)
            if type(bar) is not dict:
                raise ValueError(
                    f"Basic Workflow valuation bar is missing for '{instrument}'."
                )
            close_price = _finite(
                bar.get("close"),
                f"price.{execution_period}.{instrument}.close",
            )
            if close_price <= 0:
                raise ValueError("Basic Workflow valuation price must be positive.")
            equity += position * close_price

        self.state["cash"] = cash
        self.state["positions"] = dict(positions)
        return {
            "account": {
                "cash": cash,
                "positions": dict(positions),
                "equity": equity,
            },
            "orders": orders,
        }
