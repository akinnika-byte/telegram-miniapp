"""MCP-прокси к базе данных через API приложения.

Прямой доступ с этой машины к Supabase нестабилен (сеть), поэтому запросы
идут через HTTPS на боевой сервер Render, у которого соединение с базой стабильно.

Запуск: python scripts/sqlproxy_mcp.py  (нужны BOT_TOKEN и ADMIN_CODE в .env)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CODE = os.getenv("ADMIN_CODE", "6666")
API_BASE = os.getenv("API_BASE", "https://telegram-miniapp-emy5.onrender.com")
TG_ID = 980000001

if not BOT_TOKEN:
    print("sqlproxy: BOT_TOKEN не задан в .env", file=sys.stderr)
    sys.exit(1)

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("sqlproxy")
_authed = False


def _init_data() -> str:
    data = {
        "auth_date": str(int(time.time())),
        "query_id": "AAHsqlproxy",
        "user": json.dumps(
            {"id": TG_ID, "first_name": "SQLProxy", "username": "sqlproxy"},
            separators=(",", ":"), ensure_ascii=False,
        ),
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def _call(method: str, path: str, body=None):
    req = urllib.request.Request(API_BASE + path, method=method)
    req.add_header("X-Telegram-Init-Data", _init_data())
    req.add_header("Content-Type", "application/json")
    payload = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, payload, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"detail": raw[:200]}
    except Exception as e:
        return 0, {"detail": f"{type(e).__name__}: {e}"}


def _ensure_auth() -> bool:
    global _authed
    if _authed:
        return True
    s, d = _call("POST", "/api/login", {"code": ADMIN_CODE})
    _authed = s == 200 and isinstance(d, dict) and d.get("kind") == "admin"
    return _authed


@mcp.tool()
def query(sql: str) -> str:
    """Выполнить SQL-запрос ТОЛЬКО НА ЧТЕНИЕ к базе проекта (SELECT/SHOW/EXPLAIN).

    Примеры: "select count(*) from waybills", "select plate, model from vehicles order by plate".
    Возвращает JSON-строку с результатом.
    """
    _ensure_auth()
    s, d = _call("POST", "/api/admin/sql", {"sql": sql})
    if s != 200:
        # повтор с повторным логином
        global _authed
        _authed = False
        _ensure_auth()
        s, d = _call("POST", "/api/admin/sql", {"sql": sql})
    if s != 200:
        detail = d.get("detail", d) if isinstance(d, dict) else d
        return f"Ошибка {s}: {detail}"
    rows = d.get("rows", [])
    if not rows:
        return "(нет строк)"
    return json.dumps(rows, ensure_ascii=False, default=str)


if __name__ == "__main__":
    mcp.run()
