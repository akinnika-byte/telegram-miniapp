"""Формирование «Расшифровки» (форма № 63) в Excel из данных путевых листов.

Повторяет бланк: шапка, таблица по машинам с разделением на дизельное
топливо и бензин, итоги в литрах и килограммах, подписи.

Личные данные (в/ч, подразделение, ответственное лицо) берутся из переменных
окружения, а не из репозитория.
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
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=False)
LEFT_TOP = Alignment(horizontal="left", vertical="center", wrap_text=True)
NO_WRAP = Alignment(horizontal="center", vertical="center", wrap_text=False)
NUM_FMT = "General"

# Ширины как в бланке — иначе текст «расползается» по буквам.
WIDTHS = {
    "A": 5, "B": 26, "C": 12, "D": 12, "E": 12, "F": 12,
    "G": 12, "H": 8, "I": 12, "J": 8, "K": 12, "L": 12,
}

COLUMNS = "ABCDEFGHIJKL"


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _merge(ws, row: int, first_col: int, last_col: int) -> None:
    if last_col > first_col:
        ws.merge_cells(
            start_row=row, start_column=first_col,
            end_row=row, end_column=last_col,
        )


def _frame(ws, row: int) -> None:
    """Рамка вокруг всей строки таблицы."""
    for col in range(1, len(COLUMNS) + 1):
        ws.cell(row=row, column=col).border = BOX


def build_rasshifrovka(period: dict, records: list) -> bytes:
    """Возвращает xlsx-файл в виде байтов."""
    unit = os.getenv("UNIT_NAME", "в/ч ____________")
    responsible = os.getenv("ACCOUNTABLE_PERSON", "")
    subdivision = os.getenv("SUBDIVISION", "")

    wb = Workbook()
    ws = wb.active
    ws.title = "Расшифровка"
    ws.sheet_view.showGridLines = False

    for col, width in WIDTHS.items():
        ws.column_dimensions[col].width = width

    def put(row: int, col: int, value, *, bold=False, align=LEFT,
            size=11, fmt=None, border=False):
        cell = ws.cell(row=row, column=col, value=value)
        cell.font = Font(bold=bold, size=size)
        cell.alignment = align
        if border:
            cell.border = BOX
        if fmt:
            cell.number_format = fmt
        return cell

    # ---------- Шапка ----------
    ws.row_dimensions[1].height = 24
    ws.merge_cells("D1:H1")
    put(1, 4, "РАСШИФРОВКА", bold=True, align=CENTER, size=14)

    ws.merge_cells("B2:G2")
    put(2, 2, "о расходе горючего и смазочных материалов")

    put(4, 11, "Код", align=CENTER)
    put(5, 11, 6002234, align=CENTER)
    ws.merge_cells("F5:J5")
    put(5, 6, "Форма № 63 по ОКУД", align=NO_WRAP)
    put(6, 7, "Дата", align=CENTER)
    put(6, 11, period["end_date"], align=CENTER, fmt="DD.MM.YYYY")

    ws.merge_cells("D6:E6")
    put(6, 4, f"за {period['name']} г.", align=CENTER)

    ws.merge_cells("B7:G7")
    put(7, 2, unit)
    put(7, 10, "по ОКПО", align=NO_WRAP)

    ws.row_dimensions[9].height = 20
    _merge(ws, 9, 1, 12)
    put(9, 1, "Структурное подразделение" + (f" {subdivision}" if subdivision else ""))
    _frame(ws, 9)

    ws.row_dimensions[10].height = 20
    _merge(ws, 10, 1, 12)
    put(10, 1, "Материально ответственное лицо: " + responsible)
    _frame(ws, 10)

    # ---------- Шапка таблицы ----------
    ws.row_dimensions[12].height = 42
    ws.row_dimensions[13].height = 18

    ws.merge_cells("A12:A13")
    ws.merge_cells("B12:B13")
    ws.merge_cells("C12:C13")
    ws.merge_cells("D12:D13")
    ws.merge_cells("E12:E13")
    ws.merge_cells("F12:F13")
    ws.merge_cells("G12:H12")
    ws.merge_cells("I12:J12")
    ws.merge_cells("K12:K13")
    ws.merge_cells("L12:L13")

    put(12, 1, "№\nп/п", bold=True, align=CENTER)
    put(12, 2, "Марка\nмашины, агрегата", bold=True, align=CENTER)
    put(12, 3, "Номер\nмашины, бортовой номер", bold=True, align=CENTER)
    put(12, 4, "Пробег за месяц, км", bold=True, align=CENTER)
    put(12, 5, "Остаток на начало месяца", bold=True, align=CENTER)
    put(12, 6, "Получено за месяц", bold=True, align=CENTER)
    put(12, 7, "Израсходовано", bold=True, align=CENTER)
    put(13, 7, "Фактич", bold=True, size=10, align=CENTER)
    put(13, 8, "Прочие", bold=True, size=10, align=CENTER)
    put(12, 9, "Положено по норме", bold=True, align=CENTER)
    put(13, 9, "Фактич", bold=True, size=10, align=CENTER)
    put(13, 10, "Прочие", bold=True, size=10, align=CENTER)
    put(12, 11, "Экономия (+),\nперерасход (-)", bold=True, size=10, align=CENTER)
    put(12, 12, "Остаток на конец месяца", bold=True, align=CENTER)
    _frame(ws, 12)
    _frame(ws, 13)

    def section(row: int, title: str) -> None:
        ws.row_dimensions[row].height = 20
        _merge(ws, row, 1, 12)
        put(row, 1, title, bold=True)
        _frame(ws, row)

    def vehicle_block(start_row: int, items: list, start_index: int) -> int:
        row = start_row
        for index, it in enumerate(items, start=start_index):
            km = round(_num(it.get("km")), 2)
            tank_start = round(_num(it.get("tank_start")), 2)
            fuel_in = round(_num(it.get("fuel_in")), 2)
            fuel_spent = round(_num(it.get("fuel_spent")), 2)
            tank_end = round(_num(it.get("tank_end")), 2)

            put(row, 1, index, align=CENTER)
            put(row, 2, it.get("model") or "")
            put(row, 3, it.get("plate") or "", align=CENTER)
            put(row, 4, km, align=CENTER, fmt=NUM_FMT)
            put(row, 5, tank_start, align=CENTER, fmt=NUM_FMT)
            put(row, 6, fuel_in, align=CENTER, fmt=NUM_FMT)
            put(row, 7, fuel_spent, align=CENTER, fmt=NUM_FMT)
            put(row, 8, "-", align=CENTER)
            # Бланк: «Положено по норме» = фактически израсходовано,
            # экономия/перерасход не считаются (прочерк).
            put(row, 9, f"=G{row}", align=CENTER, fmt=NUM_FMT)
            put(row, 10, "-", align=CENTER)
            put(row, 11, "-", align=CENTER)
            put(row, 12, tank_end, align=CENTER, fmt=NUM_FMT)
            ws.row_dimensions[row].height = 16
            _frame(ws, row)
            row += 1
        return row

    def totals(row: int, title: str, first: int, last: int, factor: float | None) -> int:
        _merge(ws, row, 1, 4)
        put(row, 1, title, bold=True)
        for col in range(5, 13):
            letter = get_column_letter(col)
            if factor is None:
                formula = f"=SUM({letter}{first}:{letter}{last})"
            else:
                formula = f"={letter}{row - 1}*{factor}"
            put(row, col, formula, bold=True, align=CENTER, fmt=NUM_FMT)
        ws.row_dimensions[row].height = 18
        _frame(ws, row)
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
        _frame(ws, row)
        diesel_first = diesel_last = row
        row += 1
    row = totals(row, "Итого ДТ  л.:", diesel_first, diesel_last, None)
    row = totals(row, "Итого ДТ кг.:", diesel_first, diesel_last, 0.856)

    row += 1
    section(row, "Автомобильный бензин")
    row += 1
    petrol_first = row
    row = vehicle_block(row, petrol, len(diesel) + 1)
    petrol_last = row - 1
    if not petrol:
        put(row, 1, "—", align=CENTER)
        _frame(ws, row)
        petrol_first = petrol_last = row
        row += 1
    row = totals(row, "Итого АИ-92 л.:", petrol_first, petrol_last, None)
    row = totals(row, "Итого АИ-92 кг.:", petrol_first, petrol_last, 0.755)

    # ---------- Основание и подписи ----------
    row += 1
    _merge(ws, row, 1, 12)
    put(row, 1, "Основание: Приказ МО РФ 2021 г. № 690 (нормы расхода горючего)")
    row += 2

    for text in (
        "Пробег (наработку) машин (агрегатов) подтверждаю.",
        "Председатель комиссии: ______________________________",
        "Члены комиссии: ______________________________________",
        "Материально ответственное лицо: ______________________",
        "«___» _________________ 20__ г.",
    ):
        _merge(ws, row, 1, 12)
        put(row, 1, text)
        row += 1

    # ---------- Печать ----------
    ws.print_title_rows = "12:13"
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = 0.4
    ws.page_margins.right = 0.3
    ws.page_margins.top = 0.4
    ws.page_margins.bottom = 0.4

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
