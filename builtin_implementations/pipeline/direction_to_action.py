from strategy_devkit.module_sdk import SignalModule


class DirectionToAction(SignalModule):
    def update(self, direction):
        if direction not in {"rise", "fall", "flat", None}:
            raise ValueError("direction-to-action requires rise, fall, flat, or null.")
        previous = int(self.state.get("previous") or 0)
        current = 1 if direction == "rise" else -1 if direction == "fall" else 0
        action_type = "hold"
        if current > 0 and previous <= 0:
            action_type = "enter"
        elif current < 0 and previous >= 0:
            action_type = "exit"
        if current:
            self.state["previous"] = current
        return {"action": {"type": action_type, "direction": direction or "flat", "reason": "direction_to_action"}}
