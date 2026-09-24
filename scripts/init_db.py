"""Создание таблиц и загрузка начальных данных в базу Supabase.

Запуск из корня проекта (нужен DATABASE_URL в .env):

    python scripts/init_db.py

Скрипт идемпотентен: повторный запуск не ломает существующие данные.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db  # noqa: E402

BASE = Path(__file__).resolve().parent.parent


def run_file(conn, rel_path: str) -> None:
    path = BASE / rel_path
    print(f"→ {rel_path}")
    conn.execute(path.read_text(encoding="utf-8"))


def main() -> None:
    with db() as conn:
        run_file(conn, "sql/schema.sql")
        run_file(conn, "sql/seed.sql")
        vehicles = conn.execute("select count(*) as n from vehicles").fetchone()["n"]
        periods = conn.execute("select count(*) as n from periods").fetchone()["n"]

    print(f"\nГотово. Машин в БД: {vehicles}, периодов: {periods}")


if __name__ == "__main__":
    main()
