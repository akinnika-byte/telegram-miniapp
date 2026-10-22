"""Формирование «Расшифровки» (форма № 63) в Excel из данных путевых листов.

Повторяет структуру бланка: шапка, таблица по машинам с разделением на
дизельное топливо и бензин, итоги в литрах и килограммах, подписи.

Личные данные (в/ч, ответственное лицо) берутся из переменных окружения,
а не из репозитория.
"""

from __future__ import annotations

import os
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

THIN = Side(style="thin")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
NUM_FMT = "0.##"

WIDTHS = {
    "A": 5, "B": 30, "C": 13, "D": 12, "E": 11, "F": 11,
    "G": 11, "H": 8, "I": 11, "J": 8, "K": 12, "L": 11,
}


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_rasshifrovka(period: dict, records: list) -> bytes:
    """Возвращает xlsx-файл в виде байтов."""
    unit = os.getenv("UNIT_NAME", "в/ч ____________")
    responsible = os.getenv("ACCOUNTABLE_PERSON", "")

    wb = Workbook()
    ws = wb.active
    ws.title = "Расшифровка"

    for col, width in WIDTHS.items():
        ws.column_dimensions[col].width = width

    def put(row: int, col: int, value, *, bold=False, align=CENTER, fmt=None,
            border=True):
        cell = ws.cell(row=row, column=col, value=value)
        cell.font = Font(bold=bold, size=11)
        cell.alignment = align
        if border:
            cell.border = BOX
        if fmt:
            cell.number_format = fmt
        return cell

    # ---------- Шапка ----------
    ws.merge_cells("D1:H1")
    put(1, 4, "РАСШИФРОВКА", bold=True, align=CENTER).font = Font(bold=True, size=14)

    ws.merge_cells("B2:G2")
    put(2, 2, "о расходе горючего и смазочных материалов", align=LEFT)

    put(5, 6, "Форма № 63 по ОКУД")
    put(5, 11, 6002234)
    put(6, 7, "Дата", align=CENTER)
    put(6, 11, period["end_date"], fmt="DD.MM.YYYY")
    put(6, 4, f"за {period['name']} г.", align=CENTER)
    put(7, 10, "по ОКПО")
    ws.merge_cells("B7:G7")
    put(7, 2, unit, align=LEFT)

    put(9, 1, "Структурное подразделение", align=LEFT)
    put(10, 1, "Материально ответственное лицо: " + responsible, align=LEFT)

    # ---------- Шапка таблицы ----------
    header_merges = [
        ("A12", "A13", "№\nп/п"),
        ("B12", "B13", "Марка\nмашины, агрегата"),
        ("C12", "C13", "Номер\nмашины, бортовой номер"),
        ("D12", "D13", "Пробег за месяц, км"),
        ("E12", "E13", "Остаток на начало месяца"),
        ("F12", "F13", "Получено за месяц"),
        ("K12", "K13", "Экономия (+), перерасход (-)"),
        ("L12", "L13", "Остаток на конец месяца"),
    ]
    for top, bottom, text in header_merges:
        ws.merge_cells(f"{top}:{bottom}")
    put(12, 1, "№\nп/п", bold=True)
    put(12, 2, "Марка\nмашины, агрегата", bold=True)
    put(12, 3, "Номер\nмашины, бортовой номер", bold=True)
    put(12, 4, "Пробег за месяц, км", bold=True)
    put(12, 5, "Остаток на начало месяца", bold=True)
    put(12, 6, "Получено за месяц", bold=True)
    put(12, 11, "Экономия (+), перерасход (-)", bold=True)
    put(12, 12, "Остаток на конец месяца", bold=True)

    ws.merge_cells("G12:H12")
    put(12, 7, "Израсходовано", bold=True)
    put(13, 7, "Фактич", bold=True)
    put(13, 8, "Прочие", bold=True)
    ws.merge_cells("I12:J12")
    put(12, 9, "Положено по норме", bold=True)
    put(13, 9, "Фактич", bold=True)
    put(13, 10, "Прочие", bold=True)
    for col in range(1, 13):
        ws.cell(row=13, column=col).border = BOX

    def section(row: int, title: str) -> None:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=12)
        cell = ws.cell(row=row, column=1, value=title)
        cell.font = Font(bold=True)
        cell.alignment = LEFT
        for col in range(1, 13):
            ws.cell(row=row, column=col).border = BOX

    def vehicle_block(start_row: int, items: list, start_index: int) -> int:
        row = start_row
        for index, it in enumerate(items, start=start_index):
            km = round(_num(it.get("km")), 2)
            tank_start = round(_num(it.get("tank_start")), 2)
            fuel_in = round(_num(it.get("fuel_in")), 2)
            fuel_spent = round(_num(it.get("fuel_spent")), 2)
            fuel_calc = round(_num(it.get("fuel_calc")), 2)
            tank_end = round(_num(it.get("tank_end")), 2)
            values = [
                index, it.get("model") or "", it.get("plate") or "",
                km, tank_start, fuel_in, fuel_spent, "-",
                # «Положено по норме» = фактически израсходовано (как в бланке),
                # экономия/перерасход не считаем — прочерк.
                f"=G{row}", "-", "-", tank_end,
            ]
            for col, value in enumerate(values, start=1):
                align = LEFT if col == 2 else CENTER
                fmt = NUM_FMT if isinstance(value, float) else None
                put(row, col, value, align=align, fmt=fmt)
            row += 1
        return row

    def totals(row: int, title: str, first: int, last: int, factor: float | None) -> int:
        put(row, 1, title, bold=True, align=LEFT)
        for col in range(5, 13):
            letter = get_column_letter(col)
            if factor is None:
                formula = f"=SUM({letter}{first}:{letter}{last})"
            else:
                formula = f"={letter}{row - 1}*{factor}"
            put(row, col, formula, bold=True, fmt=NUM_FMT)
        return row + 1

    diesel = [r for r in records if (r.get("fuel_type") or "diesel") != "petrol"]
    petrol = [r for r in records if (r.get("fuel_type") or "diesel") == "petrol"]

    row = 14
    section(row, "Дизельное топливо")
    row += 1
    diesel_first = row
    row = vehicle_block(row, diesel, 1)
    diesel_last = row - 1
    if not diesel:
        put(row, 1, "—", align=CENTER)
        diesel_first = diesel_last = row
        row += 1
    row = totals(row, "Итого ДТ  л.:", diesel_first, diesel_last, None)
    row = totals(row, "Итого ДТ кг.:", diesel_first, diesel_last, 0.856)

    row += 1  # пустая строка
    section(row, "Автомобильный бензин")
    row += 1
    petrol_first = row
    row = vehicle_block(row, petrol, len(diesel) + 1)
    petrol_last = row - 1
    if not petrol:
        put(row, 1, "—", align=CENTER)
        petrol_first = petrol_last = row
        row += 1
    row = totals(row, "Итого АИ-92 л.:", petrol_first, petrol_last, None)
    row = totals(row, "Итого АИ-92 кг.:", petrol_first, petrol_last, 0.755)

    # ---------- Основание и подписи ----------
    row += 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=12)
    cell = ws.cell(row=row, column=1,
                   value="Основание: Приказ МО 2021 г. № 690 согласно нормам расхода")
    cell.alignment = LEFT
    row += 2

    for text in (
        "Пробег (наработку) машин (агрегатов) подтверждаю.",
        "Председатель комиссии: ______________________________",
        "Члены комиссии: ______________________________________",
        "Материально ответственное лицо: ______________________",
        "«___» _________________ 20__ г.",
    ):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=12)
        cell = ws.cell(row=row, column=1, value=text)
        cell.alignment = LEFT
        row += 1

    # ---------- Параметры печати ----------
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
