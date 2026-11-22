"""Numeric parsing shared by annotation values, filter thresholds and JSON input."""

import math


def finite_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("expected a finite number") from exc
    if not math.isfinite(number):
        raise ValueError("expected a finite number")
    return number
