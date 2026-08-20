import math
from datetime import datetime, timezone

from strategy_devkit.analysis_module_sdk import AnalyzerModule


class PerformanceMetricsAnalyzer(AnalyzerModule):
    """Online performance statistics annualized from observed elapsed time."""

    SECONDS_PER_YEAR = 365.2425 * 24 * 60 * 60

    @staticmethod
    def _instant(value):
        text = str(value or "").strip()
        if not text:
            raise ValueError("Performance time must be a non-empty ISO-8601 timestamp.")
        try:
            parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        except ValueError as exc:
            raise ValueError("Performance time must be a valid ISO-8601 timestamp.") from exc
        if parsed.tzinfo is None:
            raise ValueError("Performance time must include an explicit timezone offset.")
        return parsed.astimezone(timezone.utc).timestamp(), text

    def _metrics(self):
        observation_count = int(self.state.get("observationCount") or 0)
        return_count = int(self.state.get("returnCount") or 0)
        if not observation_count:
            return {
                "observationCount": 0,
                "returnCount": 0,
                "startEquity": None,
                "endEquity": None,
                "totalReturn": None,
                "annualizedReturn": None,
                "annualizedVolatility": None,
                "sharpeRatio": None,
                "maxDrawdown": None,
                "firstTime": None,
                "lastTime": None,
                "observationsPerYear": None,
            }
        start = self.state["startEquity"]
        end = self.state["endEquity"]
        total_return = None if start == 0 else end / start - 1.0
        elapsed_seconds = float(self.state.get("lastEpoch") or 0.0) - float(
            self.state.get("firstEpoch") or 0.0
        )
        elapsed_years = elapsed_seconds / self.SECONDS_PER_YEAR if elapsed_seconds > 0 else None
        periods = return_count / elapsed_years if elapsed_years and return_count else None
        annualized_return = None
        if total_return is not None and elapsed_years and 1.0 + total_return >= 0:
            try:
                annualized_return = (1.0 + total_return) ** (1.0 / elapsed_years) - 1.0
            except OverflowError:
                annualized_return = None
        volatility = None
        sharpe = None
        if return_count >= 2 and periods:
            mean = float(self.state.get("meanReturn") or 0.0)
            variance = float(self.state.get("returnM2") or 0.0) / (return_count - 1)
            deviation = math.sqrt(max(variance, 0.0))
            volatility = deviation * math.sqrt(periods)
            if deviation > 0:
                annual_risk_free = float(self.config.get("riskFreeRate") or 0.0)
                if annual_risk_free <= -1.0:
                    raise ValueError("Performance riskFreeRate must be greater than -1.")
                per_period_risk_free = (1.0 + annual_risk_free) ** (1.0 / periods) - 1.0
                sharpe = (mean - per_period_risk_free) / deviation * math.sqrt(periods)
        return {
            "observationCount": observation_count,
            "returnCount": return_count,
            "startEquity": start,
            "endEquity": end,
            "totalReturn": total_return,
            "annualizedReturn": annualized_return,
            "annualizedVolatility": volatility,
            "sharpeRatio": sharpe,
            "maxDrawdown": float(self.state.get("maxDrawdown") or 0.0),
            "firstTime": self.state.get("firstTime"),
            "lastTime": self.state.get("lastTime"),
            "observationsPerYear": periods,
        }

    def update(self, time, equity=None):
        if equity is not None:
            epoch, timestamp = self._instant(time)
            equity = float(equity)
            if not math.isfinite(equity):
                raise ValueError("Performance equity must be finite.")
            count = int(self.state.get("observationCount") or 0)
            previous_epoch = self.state.get("lastEpoch")
            if previous_epoch is not None and epoch <= float(previous_epoch):
                raise ValueError("Performance observation times must be strictly increasing.")
            previous = self.state.get("endEquity")
            if count and previous != 0:
                value = equity / previous - 1.0
                return_count = int(self.state.get("returnCount") or 0) + 1
                previous_mean = float(self.state.get("meanReturn") or 0.0)
                delta = value - previous_mean
                mean = previous_mean + delta / return_count
                self.state["returnCount"] = return_count
                self.state["meanReturn"] = mean
                self.state["returnM2"] = float(self.state.get("returnM2") or 0.0) + delta * (value - mean)
            if not count:
                self.state["startEquity"] = equity
                self.state["peakEquity"] = equity
                self.state["maxDrawdown"] = 0.0
                self.state["firstTime"] = timestamp
                self.state["firstEpoch"] = epoch
            peak = max(float(self.state.get("peakEquity", equity)), equity)
            self.state["peakEquity"] = peak
            if peak != 0:
                self.state["maxDrawdown"] = min(
                    float(self.state.get("maxDrawdown") or 0.0),
                    equity / peak - 1.0,
                )
            self.state["observationCount"] = count + 1
            self.state["endEquity"] = equity
            self.state["lastTime"] = timestamp
            self.state["lastEpoch"] = epoch
        return {"performance": self._metrics()}

    def on_finalize(self):
        return self._metrics()
