"""Установка кодов доступа администраторов.

Запуск (из корня проекта, при заданном DATABASE_URL в .env):

    python scripts/set_admin_codes.py

Коды нигде не сохраняются в открытом виде — только хеш в таблице access_codes.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ADMIN_ROLES, ADMIN_ROLE_TITLES  # noqa: E402
from db import db  # noqa: E402
from security import hash_code  # noqa: E402


def main() -> None:
    print("Установка кодов доступа администраторов.")
    print("Пусто = оставить прежний код.\n")

    new_codes: dict[str, str] = {}
    for role in ADMIN_ROLES:
        title = ADMIN_ROLE_TITLES.get(role, role)
        code = getpass.getpass(f"{title} [{role}]: ").strip()
        if not code:
            continue
        if not code.isdigit() or len(code) != 4:
            print(f"  ! Код должен состоять из 4 цифр — пропущено для «{title}»")
            continue
        new_codes[role] = code

    if not new_codes:
        print("\nНичего не задано.")
        return

    with db() as conn:
        for role, code in new_codes.items():
            conn.execute(
                """
                insert into access_codes (role, code_hash, updated_at)
                values (%s, %s, now())
                on conflict (role) do update set
                    code_hash = excluded.code_hash,
                    updated_at = now()
                """,
                (role, hash_code(code)),
            )

    print("\nГотово. Обновлены роли:", ", ".join(new_codes.keys()))


if __name__ == "__main__":
    main()
