"""Расчёт и проверка путевого листа.

Формулы перенесены 1:1 из desktop-версии (maiin.py), менять их нельзя
без согласования — иначе результаты разойдутся с прежними отчётами.
"""

from __future__ import annotations

import math

# Допуск при сравнении (км / литры)
EPS = 0.5

# Коэффициент для пробега без груза (-15%)
EMPTY_RUN_COEF = 0.85


def round_half_up(x: float) -> int:
    """Округление 0.5 вверх (как в оригинале: int(x + 0.5))."""
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def to_float(value, default: float = 0.0) -> float:
    """Приводит значение к float, пустое/None/строку с запятой — корректно."""
    if value is None or value == "":
        return default
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if value == "":
            return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def compute(
    *,
    km_start,
    km_end,
    km_empty,
    km_loaded,
    tank_start,
    fuel_in,
    tank_end,
    fuel_spent,
    motohours,
    fuel_norm,
    motohour_norm,
) -> dict:
    """Считает все показатели путевого листа.

    Возвращает словарь с введёнными и вычисленными полями.
    """
    km_start = to_float(km_start)
    km_end = to_float(km_end)
    km_empty = to_float(km_empty)
    km_loaded = to_float(km_loaded)
    tank_start = to_float(tank_start)
    fuel_in = to_float(fuel_in)
    tank_end = to_float(tank_end)
    fuel_spent = to_float(fuel_spent)
    motohours = to_float(motohours)
    fuel_norm = to_float(fuel_norm)
    motohour_norm = to_float(motohour_norm)

    # Топливо
    p1 = round_half_up((km_empty * fuel_norm / 100) * EMPTY_RUN_COEF)
    p2 = round_half_up(km_loaded * fuel_norm / 100)
    h = round_half_up(motohours * motohour_norm) if motohour_norm else 0
    fuel_calc = p1 + p2 + h

    # Километраж
    km_total_odo = km_end - km_start
    km_total_input = km_empty + km_loaded

    # Баки
    tank_end_calc = tank_start + fuel_in - fuel_calc

    km_ok = abs(km_total_odo - km_total_input) < EPS
    tank_ok = abs(tank_end_calc - tank_end) < EPS
    fuel_ok = abs(fuel_spent - fuel_calc) < EPS
    is_ok = km_ok and tank_ok and fuel_ok

    return {
        "km_start": km_start,
        "km_end": km_end,
        "km_empty": km_empty,
        "km_loaded": km_loaded,
        "tank_start": tank_start,
        "fuel_in": fuel_in,
        "tank_end": tank_end,
        "fuel_spent": fuel_spent,
        "motohours": motohours,
        "fuel_norm": fuel_norm,
        "motohour_norm": motohour_norm,
        "p1": p1,
        "p2": p2,
        "h": h,
        "fuel_calc": fuel_calc,
        "km_total_odo": km_total_odo,
        "km_total_input": km_total_input,
        "tank_end_calc": tank_end_calc,
        "km_ok": km_ok,
        "tank_ok": tank_ok,
        "fuel_ok": fuel_ok,
        "is_ok": is_ok,
    }
