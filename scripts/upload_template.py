"""Загрузка шаблона «Расшифровки» (форма № 63) в базу.

Шаблон хранится в таблице report_templates, чтобы бланк с ФИО и номером
воинской части не попадал в публичный репозиторий.

Использование:
    python scripts/upload_template.py "C:\\путь\\Расшифровка сентябрь 26.xlsx"

Повторный запуск заменяет шаблон.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

NAME = "rasshifrovka"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    path = Path(sys.argv[1]).expanduser()
    if not path.is_file():
        print(f"Файл не найден: {path}")
        return 2

    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        print("Не задан DATABASE_URL в .env")
        return 2

    data = path.read_bytes()
    with psycopg.connect(url, prepare_threshold=None) as conn:
        conn.execute(
            """
            insert into report_templates (name, data, updated_at)
            values (%s, %s, now())
            on conflict (name) do update
               set data = excluded.data, updated_at = now()
            """,
            (NAME, data),
        )
        conn.commit()

    print(f"Шаблон загружен: {NAME}, {len(data)} байт ({path.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
