"""Deterministic annualization, volatility, Sharpe and drawdown calculations."""

from __future__ import annotations

import math

from .numbers import finite


SECONDS_PER_YEAR = 365.2425 * 24 * 60 * 60


def annualized_return(total_return, elapsed_seconds):
    value = finite(total_return, "totalReturn")
    elapsed = finite(elapsed_seconds, "elapsedSeconds")
    if elapsed <= 0 or 1.0 + value < 0:
        return None
    try:
        return (1.0 + value) ** (SECONDS_PER_YEAR / elapsed) - 1.0
    except OverflowError:
        return None


def sample_return_statistics(
    return_count,
    return_sum,
    return_square_sum,
    periods_per_year,
    risk_free_rate=0.0,
):
    count = int(return_count)
    if count < 2:
        return {"annualizedVolatility": None, "sharpeRatio": None}
    periods = finite(periods_per_year, "periodsPerYear")
    if periods <= 0:
        raise ValueError("periodsPerYear must be positive.")
    total = finite(return_sum, "returnSum")
    squares = finite(return_square_sum, "returnSquareSum")
    mean = total / count
    variance = (squares - total * total / count) / (count - 1)
    deviation = math.sqrt(max(variance, 0.0))
    volatility = deviation * math.sqrt(periods)
    sharpe = None
    if deviation:
        annual_risk_free = finite(risk_free_rate, "riskFreeRate")
        if annual_risk_free <= -1.0:
            raise ValueError("riskFreeRate must be greater than -1.")
        per_period_risk_free = (1.0 + annual_risk_free) ** (1.0 / periods) - 1.0
        sharpe = (mean - per_period_risk_free) / deviation * math.sqrt(periods)
    return {"annualizedVolatility": volatility, "sharpeRatio": sharpe}


def drawdown(peak_equity, equity):
    peak = finite(peak_equity, "peakEquity")
    current = finite(equity, "equity")
    return 0.0 if peak == 0 else current / peak - 1.0


__all__ = (
    "SECONDS_PER_YEAR",
    "annualized_return",
    "drawdown",
    "sample_return_statistics",
)
