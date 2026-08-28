from builtin_implementations.pipeline.common import number
from strategy_devkit.module_sdk import SignalModule


class BasicPriceCloseSelector(SignalModule):
    def update(self, price):
        if type(price) is not dict:
            raise ValueError("Basic Workflow price must be an object map.")
        decision_period = self.config.get("decisionPeriod")
        instrument_id = self.config.get("instrumentId")
        if type(decision_period) is not str or not decision_period:
            raise ValueError("Basic Workflow decisionPeriod must be a non-empty string.")
        if type(instrument_id) is not str or not instrument_id:
            raise ValueError("Basic Workflow instrumentId must be a non-empty string.")
        period_prices = price.get(decision_period)
        if type(period_prices) is not dict:
            raise ValueError("Basic Workflow decisionPeriod is absent from price.")
        bar = period_prices.get(instrument_id)
        if type(bar) is not dict:
            raise ValueError("Basic Workflow instrument is absent from price.")
        close = number(bar.get("close"))
        if close is None:
            raise ValueError("Basic Workflow close is absent from price.")
        return {"close": close}
