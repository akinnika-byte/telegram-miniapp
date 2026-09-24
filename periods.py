"""Определение отчётного периода по дате путевого листа.

Основной источник — таблица periods в БД (границы задаются вручную,
могут быть нестандартными по приказу). Если дата не попала ни в один
период, используется резервное правило «с 21-го по 20-е».
"""

from __future__ import annotations

from datetime import date

MONTH_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    m = month + delta
    y = year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return y, m


def default_period_for(d: date) -> dict:
    """Резервное правило: период с 21-го по 20-е число.

    Если день >= 21 — период следующего месяца, иначе — текущего.
    """
    if d.day >= 21:
        y, m = _shift_month(d.year, d.month, 1)
        start = date(d.year, d.month, 21)
        end = date(y, m, 20)
    else:
        sy, sm = _shift_month(d.year, d.month, -1)
        y, m = d.year, d.month
        start = date(sy, sm, 21)
        end = date(y, m, 20)
    return {
        "name": f"{MONTH_RU[m]} {str(y)[2:]}",
        "year": y,
        "month": m,
        "start_date": start,
        "end_date": end,
    }


def period_for_date(conn, d: date) -> dict | None:
    """Ищет период в БД, в диапазон которого попадает дата."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select id, name, year, month, start_date, end_date, is_open
            from periods
            where start_date <= %s and end_date >= %s
            order by start_date desc
            limit 1
            """,
            (d, d),
        )
        return cur.fetchone()


def resolve_period(conn, d: date, *, create_fallback: bool = False) -> dict | None:
    """Возвращает период для даты: сначала из БД, иначе резервное правило.

    create_fallback=True — если периода нет, создаёт его по резервному правилу.
    """
    period = period_for_date(conn, d)
    if period is not None:
        return period
    if not create_fallback:
        return None

    fallback = default_period_for(d)
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into periods (name, year, month, start_date, end_date, is_open)
            values (%(name)s, %(year)s, %(month)s, %(start_date)s, %(end_date)s, true)
            on conflict (name) do nothing
            returning id, name, year, month, start_date, end_date, is_open
            """,
            fallback,
        )
        row = cur.fetchone()
    conn.commit()
    if row is not None:
        return row
    # если гонка — перечитываем
    return period_for_date(conn, d)
