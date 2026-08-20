"""Pure account calculations for stateful Environment Module adapters."""

from __future__ import annotations

from .numbers import finite


def single_asset_account(
    previous_account,
    settlement,
    execution_value,
    margin_interest,
    *,
    initial_cash,
    initial_position,
):
    previous = previous_account or {}
    cash = finite(previous.get("cash", initial_cash), "account.cash")
    position = finite(previous.get("position", initial_position), "account.position")
    if settlement:
        cash += finite(settlement["cashDelta"], "settlement.cashDelta")
        position += finite(settlement["positionDelta"], "settlement.positionDelta")
    interest = finite(margin_interest, "marginInterest")
    cash -= interest
    marked = position * finite(execution_value, "executionValue")
    return {
        "cash": cash,
        "position": position,
        "markedValue": marked,
        "equity": cash + marked,
        "marginInterest": interest,
    }


def marked_position_account(cash, positions, prices):
    cash_value = finite(cash, "account.cash")
    normalized_positions = {
        str(key): finite(value, f"positions.{key}")
        for key, value in positions.items()
    }
    equity = cash_value
    for key, position in normalized_positions.items():
        equity += position * finite(prices[key], f"prices.{key}")
    return {
        "cash": cash_value,
        "positions": normalized_positions,
        "equity": equity,
    }


def long_only_mark_to_market_account(cash, positions_by_key, market_price, point_value=1.0):
    """Mark independent long positions without assuming one position per instrument."""

    mark = finite(market_price, "marketPrice")
    point = finite(point_value, "pointValue")
    position_lots = {}
    unrealized = 0.0
    for key, position in positions_by_key.items():
        lots = finite(position.get("openLots", 0.0), f"positions.{key}.openLots")
        average = finite(
            position.get("averageFillPrice", 0.0),
            f"positions.{key}.averageFillPrice",
        )
        position_lots[str(key)] = lots
        unrealized += (mark - average) * lots * point
    cash_value = finite(cash, "account.cash")
    return {
        "cash": cash_value,
        "positions": position_lots,
        "equity": cash_value + unrealized,
    }


def long_only_aggregate_account(
    cash,
    instrument_id,
    open_lots,
    cost_basis,
    market_price,
    point_value=1.0,
):
    """Mark a leveraged long instrument from incrementally maintained totals."""

    instrument = str(instrument_id)
    if not instrument or instrument.strip() != instrument:
        raise ValueError("instrumentId must be a non-empty string.")
    cash_value = finite(cash, "account.cash")
    lots = finite(open_lots, "account.openLots")
    basis = finite(cost_basis, "account.costBasis")
    mark = finite(market_price, "marketPrice")
    point = finite(point_value, "pointValue")
    return {
        "cash": cash_value,
        "positions": {instrument: lots},
        "equity": cash_value + (mark * lots - basis) * point,
    }


__all__ = (
    "long_only_aggregate_account",
    "long_only_mark_to_market_account",
    "marked_position_account",
    "single_asset_account",
)
