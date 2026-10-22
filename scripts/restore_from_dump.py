"""Восстановление таблиц схемы public из логического дампа Supabase.

Дамп — обычный SQL (pg_dumpall), возможно сжатый gzip.
Скрипт берёт ТОЛЬКО объекты схемы public (наши таблицы), не трогая
служебные схемы Supabase (auth, storage, realtime...).

Использование:
    python scripts/restore_from_dump.py "<путь к .backup.gz>" [--drop] [--dry-run]

  --drop     сначала удалить наши таблицы (полная перезаливка)
  --dry-run  только показать, что будет выполнено
"""

from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db  # noqa: E402

TABLES = [
    "vehicles", "periods", "waybills", "app_users", "access_codes",
    "activity_log", "vehicle_documents", "vehicle_state",
    "web_sessions", "error_log", "request_log",
]


def read_sql(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"Файл не найден: {p}")
    if p.suffix.lower() == ".gz":
        with gzip.open(p, "rt", encoding="utf-8", errors="replace") as f:
            return f.read()
    return p.read_text(encoding="utf-8", errors="replace")


def split_blocks(sql: str):
    """Делит дамп на ('stmt', text) и ('copy', text)."""
    lines = sql.splitlines()
    out = []
    buf = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]

        if line.startswith("COPY "):
            if buf:
                out.append(("stmt", "\n".join(buf)))
                buf = []
            block = [line]
            i += 1
            while i < n and lines[i] != "\\.":
                block.append(lines[i])
                i += 1
            if i < n:
                i += 1
            out.append(("copy", "\n".join(block)))
            continue

        # psql-метакоманды (\restrict, \connect, \unrestrict) — пропускаем
        if line.startswith("\\"):
            i += 1
            continue
        if not line.strip() or line.startswith("--"):
            i += 1
            continue

        buf.append(line)
        if line.rstrip().endswith(";"):
            out.append(("stmt", "\n".join(buf)))
            buf = []
        i += 1

    if buf:
        out.append(("stmt", "\n".join(buf)))
    return out


def is_public(text: str) -> bool:
    """Только объекты схемы public (не graphql_public, не тела функций)."""
    import re

    t = text.lstrip()
    allowed = re.compile(
        r"^(CREATE TABLE|CREATE SEQUENCE|ALTER TABLE|ALTER SEQUENCE|"
        r"CREATE INDEX|CREATE UNIQUE INDEX|GRANT|REVOKE|COMMENT ON|"
        r"CREATE POLICY|ALTER POLICY|DROP POLICY|SELECT pg_catalog\.setval)\b",
        re.I,
    )
    ref = re.compile(r"(?<![\w])public\.")
    return bool(allowed.match(t)) and bool(ref.search(t))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--drop", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sql = read_sql(args.dump)
    blocks = split_blocks(sql)

    public_blocks = []
    for kind, text in blocks:
        if kind == "copy":
            header = text.split("\n", 1)[0]
            if header.startswith("COPY public."):
                public_blocks.append((kind, text))
        elif is_public(text):
            public_blocks.append((kind, text))

    stmts = sum(1 for k, _ in public_blocks if k == "stmt")
    copies = sum(1 for k, _ in public_blocks if k == "copy")
    print(f"Дамп: {len(sql)} символов, блоков public: {stmts} операторов + {copies} COPY")

    if args.dry_run:
        for kind, text in public_blocks[:12]:
            print(f"  [{kind}] {text.splitlines()[0][:100]}")
        print("  ...")
        return 0

    with db() as conn:
        if args.drop:
            print("Удаляю наши таблицы (CASCADE)...")
            conn.execute(
                "drop table if exists " + ", ".join(f"public.{t}" for t in TABLES) + " cascade"
            )

        done = 0
        for kind, text in public_blocks:
            if kind == "stmt":
                conn.execute(text)
            else:
                header, data = text.split("\n", 1)
                header = header.rstrip().rstrip(";")
                with conn.cursor() as cur, cur.copy(header) as cp:
                    cp.write(data + "\n")
            done += 1

        counts = {}
        for t in ("vehicles", "periods", "waybills", "app_users", "access_codes"):
            row = conn.execute(f"select count(*) as n from public.{t}").fetchone()
            counts[t] = row["n"] if row else "?"

    print(f"Выполнено блоков: {done}")
    print("Записей:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
