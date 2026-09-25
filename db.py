"""Подключение к базе данных Supabase (PostgreSQL).

Основной путь — пул соединений через session-пулер Supabase (порт 5432):
соединения переиспользуются, запросы быстрые.
Если пул не поднялся — откат на прямое подключение на каждый запрос.
"""

from __future__ import annotations

from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from config import DATABASE_URL

# session-пулер (долгоживущие соединения) — порт 5432 на том же хосте
POOL_URL = DATABASE_URL.replace(":6543", ":5432") if DATABASE_URL else ""

_pool = None
_pool_failed = False


def _get_pool():
    global _pool, _pool_failed
    if _pool is None and not _pool_failed:
        try:
            from psycopg_pool import ConnectionPool

            pool = ConnectionPool(
                conninfo=POOL_URL,
                min_size=1,
                max_size=8,
                kwargs={"row_factory": dict_row},
                open=True,
            )
            pool.wait(timeout=20)
            _pool = pool
        except Exception:
            _pool_failed = True
    return _pool


@contextmanager
def db():
    """Контекст: соединение с БД. Коммит при успехе, откат при ошибке."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL не задан")

    pool = _get_pool()
    if pool is not None:
        with pool.connection() as conn:
            yield conn
        return

    # Резерв: прямое подключение на каждый запрос.
    # prepare_threshold=None — обязательно для transaction-пулера (6543).
    conn = psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        prepare_threshold=None,
        connect_timeout=15,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
