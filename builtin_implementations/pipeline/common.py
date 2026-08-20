from __future__ import annotations

import math


def number(value):
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"Numeric module input received '{type(value).__name__}'.")
    return float(value)


def period(config, name="period", default=20):
    return max(1, int(config.get(name) or default))


def push_window(state, name, value, size):
    window = state.setdefault(name, [])
    window.append(value)
    while len(window) > size:
        window.pop(0)
    return window
