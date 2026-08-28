from builtin_implementations.pipeline.common import number
from strategy_devkit.module_sdk import SignalModule
from datetime import datetime, timezone


class BasicPriceBarSelector(SignalModule):
    """Emit exact OHLCV fields and calendar resets from the bar event time."""
    def update(self, price):
        period = self.config.get("decisionPeriod")
        instrument = self.config.get("instrumentId")
        if not isinstance(period, str) or not isinstance(instrument, str):
            raise ValueError("Basic Workflow bar selector requires decisionPeriod and instrumentId.")
        bar = (price.get(period) if isinstance(price, dict) else None) or {}
        bar = bar.get(instrument)
        if not isinstance(bar, dict):
            raise ValueError("Basic Workflow instrument is absent from price.")
        event = bar.get("eventTime")
        if not isinstance(event, str) or not event:
            raise ValueError("Basic Workflow bar eventTime is required.")
        text = event.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("Basic Workflow bar eventTime is invalid.") from exc
        if dt.tzinfo is None:
            raise ValueError("Basic Workflow bar eventTime must be absolute.")
        dt = dt.astimezone(timezone.utc)
        values = {key: number(bar.get(key)) for key in ("open", "high", "low", "close", "volume")}
        if any(value is None for value in values.values()) or values["volume"] < 0:
            raise ValueError("Basic Workflow OHLCV bar is invalid.")
        if (
            values["low"] > min(values["open"], values["close"])
            or max(values["open"], values["close"]) > values["high"]
        ):
            raise ValueError("Basic Workflow OHLCV bar violates OHLC bounds.")
        previous = self.state.get("eventTime")
        if previous is not None:
            previous_dt = datetime.fromisoformat(previous.replace("Z", "+00:00"))
            if dt <= previous_dt.astimezone(timezone.utc):
                raise ValueError("Basic Workflow bar eventTime must be strictly increasing.")
        reset_dataset = previous is None
        week = dt.isocalendar()[:2]
        month = (dt.year, dt.month)
        reset_week = reset_dataset or week != self.state.get("week")
        reset_month = reset_dataset or month != self.state.get("month")
        reset_year = reset_dataset or dt.year != self.state.get("year")
        self.state.update(eventTime=event, week=week, month=month, year=dt.year)
        return {**values, "eventTime": event, "hlc3": (values["high"] + values["low"] + values["close"]) / 3.0,
                "resetDataset": reset_dataset, "resetWeek": reset_week, "resetMonth": reset_month, "resetYear": reset_year}
