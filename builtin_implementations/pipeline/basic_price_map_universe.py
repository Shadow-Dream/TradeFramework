from strategy_devkit.module_sdk import UniverseModule


class BasicPriceMapUniverse(UniverseModule):
    def update(self, price):
        if type(price) is not dict:
            raise ValueError("Basic Workflow price must be an object map.")
        decision_period = self.config.get("decisionPeriod")
        if type(decision_period) is not str or not decision_period:
            raise ValueError("Basic Workflow decisionPeriod must be a non-empty string.")
        prices = price.get(decision_period)
        if type(prices) is not dict:
            raise ValueError("Basic Workflow decisionPeriod is absent from price.")
        return {"selection": {instrument: True for instrument in sorted(prices)}}
