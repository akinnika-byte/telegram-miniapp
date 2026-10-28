"""Ведомость установленного оборудования и шин АВТР (ПГ) → .docx.

Запуск:
    python scripts/equipment_sheet.py "C:\\путь\\Ведомость.docx"

Структура: разделы «Автомобили», «Тягачи», «Прицепы». В каждой строке видно,
сколько машин этой марки, сколько всего колёс и сколько всего АКБ.
Пустая ячейка (None) остаётся для заполнения вручную.
"""

from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

FONT = "Times New Roman"
SIZE = Pt(12)

# (марка/модель, машин, шины, колёс всего, АКБ, АКБ всего)
SECTIONS = [
    ("Автомобили", [
        ("КамАЗ-63501 (АЦ-14)", 16, "KAMA 425/85 R21", 144, "6-СТ-190", 32),
        ("КамАЗ-63501 (АЦ-12)", 1, "KAMA 425/85 R21", 9, "6-СТ-190", 2),
        ("КамАЗ-5350 (АТЗ-7)", 1, "KAMA 425/85 R21", 9, "6-СТ-190", 2),
        ("КамАЗ-43118 (АТЗ-10)", 3, "KAMA 425/85 R21", 27, "6-СТ-190", 6),
        ("КамАЗ-53212 (АЦ-10,5)", 10, "KAMA 10,00/R10", 110, "6-СТ-190", 20),
        ("КамАЗ-65115 (АЦ-17)", 1, "KAMA 11 R 22,5 148/145", None, "6-СТ-190", 2),
    ]),
    ("Тягачи", [
        ("DAF", 1, None, None, "6-СТ-190", 2),
        ("Scania", 1, None, None, "6-СТ-190", 2),
        ("Freightliner", 2, None, None, "6-СТ-190", None),
        ("МАЗ", None, None, None, None, None),
    ]),
    ("Прицепы", [
        ("Прицеп", None, None, None, "", ""),
    ]),
]

HEADER = (
    "№\nп/п",
    "Марка, модель машины",
    "Кол-во\nмашин, ед.",
    "Наименование шин",
    "Колёс\nвсего, шт.",
    "АКБ\n(наименование)",
    "АКБ\nвсего, шт.",
)

WIDTHS = (
    Cm(0.9), Cm(4.2), Cm(1.6), Cm(3.9),
    Cm(1.8), Cm(2.4), Cm(1.7),
)


def _cell_text(cell, text, *, bold=False, center=True) -> None:
    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    value = "" if text is None else str(text)
    lines = value.split("\n")
    for i, line in enumerate(lines):
        run = p.add_run(line)
        run.font.name = FONT
        run.font.size = SIZE
        run.bold = bold
        if i < len(lines) - 1:
            run.add_break()
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER


def _no_space(paragraph) -> None:
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)


def _repeat_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    tr_pr.append(el)


def _section_row(table, title: str) -> None:
    cells = table.add_row().cells
    merged = cells[0].merge(cells[-1])
    _cell_text(merged, title, bold=True, center=False)
    for idx, cell in enumerate(cells):
        cell.width = WIDTHS[idx] if idx < len(WIDTHS) else Cm(1)


def build(path: Path) -> None:
    doc = Document()

    section = doc.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(1.2)

    style = doc.styles["Normal"]
    style.font.name = FONT
    style.font.size = SIZE
    style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    style.paragraph_format.space_after = Pt(0)
    style.paragraph_format.line_spacing = 1.0

    for i, line in enumerate((
        "ВЕДОМОСТЬ",
        "установленного оборудования и шин",
        "АВТР (ПГ)",
    )):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _no_space(p)
        run = p.add_run(line)
        run.font.name = FONT
        run.font.size = SIZE
        run.bold = i == 0

    doc.add_paragraph()

    table = doc.add_table(rows=1, cols=len(HEADER))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    header = table.rows[0]
    _repeat_header(header)
    for idx, text in enumerate(HEADER):
        _cell_text(header.cells[idx], text, bold=True)
        header.cells[idx].width = WIDTHS[idx]

    number = 0
    total_cars = total_tires = total_acc = 0
    for title, rows in SECTIONS:
        _section_row(table, title)
        for model, cars, tires, tires_qty, acc, acc_qty in rows:
            number += 1
            total_cars += cars or 0
            total_tires += tires_qty or 0
            total_acc += acc_qty or 0
            cells = table.add_row().cells
            values = (
                number, model, cars, tires or "",
                tires_qty, "" if acc is None else acc, acc_qty,
            )
            for idx, value in enumerate(values):
                _cell_text(cells[idx], value, center=idx not in (1, 3))
                cells[idx].width = WIDTHS[idx]

    cells = table.add_row().cells
    values = ("", "Итого", total_cars, "", total_tires, "", total_acc)
    for idx, value in enumerate(values):
        _cell_text(cells[idx], value, bold=True, center=idx != 1)
        cells[idx].width = WIDTHS[idx]

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("Ведомость.docx")
    build(out)
    print(f"Готово: {out.resolve()}")
