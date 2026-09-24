"""Подключение к базе данных Supabase (PostgreSQL).

Используется подключение на один запрос: Supabase transaction-пулер
(порт 6543) рассчитан именно на короткоживущие соединения и сам
мультиплексирует их. Это надёжнее, чем держать пул на клиенте.

Добавлены повторы на случай кратковременных сетевых сбоев.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

import psycopg
from psycopg import OperationalError
from psycopg.rows import dict_row

from config import DATABASE_URL

CONNECT_TIMEOUT = 15
CONNECT_ATTEMPTS = 3


def _connect() -> psycopg.Connection:
    last_error: Exception | None = None
    for attempt in range(CONNECT_ATTEMPTS):
        try:
            return psycopg.connect(
                DATABASE_URL,
                row_factory=dict_row,
                # обязательно для transaction-пулера Supabase (порт 6543):
                # иначе prepared statements ломаются.
                prepare_threshold=None,
                connect_timeout=CONNECT_TIMEOUT,
            )
        except OperationalError as exc:
            last_error = exc
            if attempt < CONNECT_ATTEMPTS - 1:
                time.sleep(0.3 * (attempt + 1))
    raise last_error  # type: ignore[misc]


@contextmanager
def db():
    """Контекст: соединение с БД. Коммит при успехе, откат при ошибке."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL не задан")

    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
