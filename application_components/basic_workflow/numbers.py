"""Strict numeric helpers shared by application components."""

from __future__ import annotations

import math


def finite(value, label):
    if type(value) not in {int, float} or isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number.")
    return result


__all__ = ("finite",)
