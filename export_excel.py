"""Заполнение «Расшифровки» (форма № 63) по шаблону.

Берётся настоящий бланк (хранится в БД, таблица report_templates), в нём
заменяются только нужные ячейки — как в desktop-версии:

  • строки машин находятся по первым цифрам номера (колонка C);
  • заполняются D (пробег), E (остаток на начало), F (получено),
    G (израсходовано), I (=G), L (=E+F−G);
  • машины, которых нет в шаблоне, добавляются строками в свой раздел
    (дизель / бензин) с сохранением оформления;
  • строки шаблона, которых нет в базе, остаются как «не ездили»:
    обороты обнуляются, остаток переносится.

Оформление, объединения, подписи и печать берутся из шаблона без изменений.
"""

from __future__ import annotations

import os
import re
from copy import copy
from io import BytesIO

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

LAST_COL = 12


def _digits(value) -> str | None:
    m = re.match(r"\s*(\d+)", str(value or ""))
    return m.group(1) if m else None


def _find_row(ws, prefix: str, start: int = 1) -> int | None:
    prefix = prefix.strip().lower()
    for row in range(start, ws.max_row + 1):
        value = ws.cell(row=row, column=1).value
        if value and str(value).strip().lower().startswith(prefix):
            return row
    return None


def _insert_rows(ws, index: int, amount: int) -> None:
    """Вставка строк с сохранением объединений, высот и стилей."""
    if amount <= 0:
        return
    heights = {
        row: dim.height
        for row, dim in ws.row_dimensions.items()
        if dim.height is not None
    }
    merges = [
        (m.min_col, m.min_row, m.max_col, m.max_row)
        for m in ws.merged_cells.ranges
    ]
    for m in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(m))

    ws.insert_rows(index, amount)

    for col_min, row_min, col_max, row_max in merges:
        shift = amount if row_min >= index else 0
        ws.merge_cells(
            start_row=row_min + shift, end_row=row_max + shift,
            start_column=col_min, end_column=col_max,
        )

    for row in list(ws.row_dimensions.keys()):
        ws.row_dimensions[row].height = None
    for row, height in heights.items():
        ws.row_dimensions[row + (amount if row >= index else 0)].height = height


def _delete_rows(ws, index: int, amount: int) -> None:
    """Удаление строк с сохранением объединений и высот."""
    if amount <= 0:
        return
    heights = {
        row: dim.height
        for row, dim in ws.row_dimensions.items()
        if dim.height is not None
    }
    merges = [
        (m.min_col, m.min_row, m.max_col, m.max_row)
        for m in ws.merged_cells.ranges
    ]
    for m in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(m))

    ws.delete_rows(index, amount)

    for col_min, row_min, col_max, row_max in merges:
        if row_min >= index + amount:
            shift = -amount
        elif row_min >= index:
            continue  # объединение попало в удаляемый диапазон
        else:
            shift = 0
        ws.merge_cells(
            start_row=row_min + shift, end_row=row_max + shift,
            start_column=col_min, end_column=col_max,
        )

    for row in list(ws.row_dimensions.keys()):
        ws.row_dimensions[row].height = None
    for row, height in heights.items():
        if index <= row < index + amount:
            continue
        ws.row_dimensions[row - (amount if row >= index + amount else 0)].height = height


def _copy_style(ws, src_row: int, dst_row: int) -> None:
    for col in range(1, LAST_COL + 1):
        src = ws.cell(row=src_row, column=col)
        dst = ws.cell(row=dst_row, column=col)
        dst._style = copy(src._style)


def _write_vehicle(ws, row: int, item: dict, *, write_label: bool = True) -> None:
    if write_label:
        ws.cell(row=row, column=2).value = item.get("model") or ""
        ws.cell(row=row, column=3).value = item.get("plate") or ""
    ws.cell(row=row, column=4).value = round(float(item.get("km") or 0), 2)
    if item.get("tank_start") is not None:
        ws.cell(row=row, column=5).value = round(float(item["tank_start"]), 2)
    elif write_label:
        ws.cell(row=row, column=5).value = 0
    ws.cell(row=row, column=6).value = round(float(item.get("fuel_in") or 0), 2)
    ws.cell(row=row, column=7).value = round(float(item.get("fuel_spent") or 0), 2)
    ws.cell(row=row, column=8).value = "-"
    ws.cell(row=row, column=9).value = f"=G{row}"
    ws.cell(row=row, column=10).value = "-"
    ws.cell(row=row, column=11).value = "-"
    ws.cell(row=row, column=12).value = f"=E{row}+F{row}-G{row}"


def _fill_block(ws, first_row: int, totals_row: int, items: list) -> int:
    """Заполняет раздел. Возвращает новую строку итогов."""
    template_rows: dict[str, list[int]] = {}
    for row in range(first_row, totals_row):
        key = _digits(ws.cell(row=row, column=3).value)
        if key:
            template_rows.setdefault(key, []).append(row)

    placement: list[tuple[int, dict]] = []
    pending: list[dict] = []
    for item in items:
        rows = template_rows.get(_digits(item.get("plate")) or "")
        if rows:
            placement.append((rows.pop(0), item))
        else:
            pending.append(item)

    # Строки бланка, под которые нет машины (прицепы, уралы, чужие номера),
    # из расшифровки убираем полностью.
    keep = {row for row, _ in placement}
    unmatched = sorted(set(range(first_row, totals_row)) - keep, reverse=True)
    for row in unmatched:
        _delete_rows(ws, row, 1)
        totals_row -= 1

    deleted = sorted(unmatched)

    def new_index(old_row: int) -> int:
        return old_row - sum(1 for row in deleted if row < old_row)

    for old_row, item in placement:
        _write_vehicle(ws, new_index(old_row), item, write_label=False)

    if pending:
        _insert_rows(ws, totals_row, len(pending))
        for offset, item in enumerate(pending):
            row = totals_row + offset
            _copy_style(ws, totals_row - 1, row)
            ws.row_dimensions[row].height = 15
            _write_vehicle(ws, row, item)
        totals_row += len(pending)

    return totals_row


def _write_totals(ws, totals_row: int, first_row: int, last_row: int, factor: float) -> None:
    for col in range(5, LAST_COL + 1):
        letter = get_column_letter(col)
        ws.cell(row=totals_row, column=col).value = f"=SUM({letter}{first_row}:{letter}{last_row})"
        ws.cell(row=totals_row + 1, column=col).value = f"={letter}{totals_row}*{factor}"


def _renumber(ws, first_row: int, last_row: int, start: int) -> int:
    """Сквозная нумерация всех строк раздела (включая без номера машины)."""
    number = start
    for row in range(first_row, last_row):
        ws.cell(row=row, column=1).value = number
        number += 1
    return number


def build_rasshifrovka(template: bytes, period: dict, vehicles: list) -> bytes:
    """Возвращает заполненный xlsx (байты) на основе шаблона."""
    wb = load_workbook(BytesIO(template))
    ws = wb.active

    unit = (os.getenv("UNIT_NAME") or "").strip()
    subdivision = (os.getenv("SUBDIVISION") or "").strip()
    responsible = (os.getenv("ACCOUNTABLE_PERSON") or "").strip()

    # Шапка: правим только то, что задано настройками
    ws["D6"] = f"                            за {period['name']} г."
    ws["K6"] = period["end_date"]
    if unit:
        ws["B7"] = "_____________________" + unit + "___________________________________"
    if subdivision:
        ws["A9"] = "Структурное подразделение " + subdivision
    if responsible:
        ws["A10"] = (
            "                                        Материально ответственное лицо:  "
            + responsible
        )

    diesel = [v for v in vehicles if (v.get("fuel_type") or "diesel") != "petrol"]
    petrol = [v for v in vehicles if (v.get("fuel_type") or "diesel") == "petrol"]

    # ---------- Дизельное топливо ----------
    diesel_totals = _find_row(ws, "итого дт")
    if diesel_totals is None:
        raise ValueError("в шаблоне не найден раздел «Итого ДТ»")
    diesel_first = _find_row(ws, "дизельное топливо")
    diesel_first = (diesel_first or 14) + 1

    diesel_totals = _fill_block(ws, diesel_first, diesel_totals, diesel)
    diesel_last = diesel_totals - 1
    _write_totals(ws, diesel_totals, diesel_first, diesel_last, 0.856)

    # ---------- Бензин ----------
    petrol_header = _find_row(ws, "автомобильный бензин")
    if petrol_header is None:
        raise ValueError("в шаблоне не найден раздел «Автомобильный бензин»")
    petrol_totals = _find_row(ws, "итого аи-92", petrol_header)
    if petrol_totals is None:
        raise ValueError("в шаблоне не найден раздел «Итого АИ-92»")
    petrol_first = petrol_header + 1

    petrol_totals = _fill_block(ws, petrol_first, petrol_totals, petrol)
    petrol_last = petrol_totals - 1
    _write_totals(ws, petrol_totals, petrol_first, petrol_last, 0.755)

    # ---------- Нумерация строк ----------
    next_no = _renumber(ws, diesel_first, diesel_totals, 1)
    _renumber(ws, petrol_first, petrol_totals, next_no)

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
