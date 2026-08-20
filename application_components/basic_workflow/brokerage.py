"""Pure brokerage-rule calculations for Environment Module adapters."""

from __future__ import annotations

from .numbers import finite


def round_target_to_lot(target, lot_size):
    if target is None:
        return None
    lot = finite(lot_size, "lotSize")
    if lot <= 0:
        raise ValueError("lotSize must be positive.")
    return round(finite(target, "target") / lot) * lot


def apply_shortable_rule(target, allow_short):
    if target is None:
        return None
    value = finite(target, "target")
    return value if allow_short else max(0.0, value)


def apply_leverage_limit(target, account_equity, execution_value, maximum_leverage):
    if target is None:
        return None
    price = finite(execution_value, "executionValue")
    if price == 0:
        raise ValueError("executionValue must be non-zero.")
    maximum = (
        max(0.0, finite(account_equity, "accountEquity"))
        * finite(maximum_leverage, "maximumLeverage")
        / price
    )
    value = finite(target, "target")
    return max(-maximum, min(maximum, value))


def apply_cash_buying_power(target, account_equity, execution_value):
    return apply_leverage_limit(target, account_equity, execution_value, 1.0)


def target_delta(target, account_position, maximum_order_quantity):
    position = finite(account_position, "accountPosition")
    if target is None:
        return {
            "requestedQuantity": 0.0,
            "approvedQuantity": 0.0,
            "approvedTarget": position,
            "status": "no-intent",
        }
    requested = finite(target, "target") - position
    maximum = abs(finite(maximum_order_quantity, "maximumOrderQuantity"))
    approved = max(-maximum, min(maximum, requested))
    return {
        "requestedQuantity": requested,
        "approvedQuantity": approved,
        "approvedTarget": position + approved,
        "status": "approved" if approved else "no-change",
    }


def constant_bps_fill_price(approved_quantity, execution_value, slippage_bps):
    quantity = finite(approved_quantity, "approvedQuantity")
    if not quantity:
        return None
    direction = 1.0 if quantity > 0 else -1.0
    return finite(execution_value, "executionValue") * (
        1.0 + direction * finite(slippage_bps, "slippageBps") / 10000.0
    )


def proportional_fill(approved_quantity, fill_value, fill_ratio):
    approved = finite(approved_quantity, "approvedQuantity")
    if not approved or fill_value is None:
        return {
            "filledQuantity": 0.0,
            "notional": 0.0,
            "status": "no-intent" if not approved else "unfilled",
        }
    ratio = max(0.0, min(1.0, finite(fill_ratio, "fillRatio")))
    filled = approved * ratio
    return {
        "filledQuantity": filled,
        "notional": filled * finite(fill_value, "fillValue"),
        "status": "filled" if filled == approved else "partially-filled",
    }


def fixed_plus_bps_fee(filled_quantity, notional, fixed_fee, fee_bps):
    filled = finite(filled_quantity, "filledQuantity")
    fixed = finite(fixed_fee, "fixedFee") if filled else 0.0
    return fixed + abs(finite(notional, "notional")) * finite(
        fee_bps, "feeBps"
    ) / 10000.0


def immediate_settlement(notional, filled_quantity, fee=0.0):
    filled = finite(filled_quantity, "filledQuantity")
    if not filled:
        return None
    return {
        "cashDelta": -(finite(notional, "notional") + finite(fee, "fee")),
        "positionDelta": filled,
    }


def negative_cash_interest(account_cash, rate):
    return max(0.0, -finite(account_cash, "accountCash")) * finite(
        rate, "marginInterestPerCycle"
    )


__all__ = (
    "apply_cash_buying_power",
    "apply_leverage_limit",
    "apply_shortable_rule",
    "constant_bps_fill_price",
    "fixed_plus_bps_fee",
    "immediate_settlement",
    "negative_cash_interest",
    "proportional_fill",
    "round_target_to_lot",
    "target_delta",
)
