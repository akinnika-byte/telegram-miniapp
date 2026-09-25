"""Smoke-тест API после деплоя.

Запуск из корня проекта:  .venv\\Scripts\\python scripts/api_check.py

Проверяет /api/health, вход админа (6666), вход водителя (9857) и отчёт.
Использует BOT_TOKEN из .env (через config.py).
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import BOT_TOKEN  # noqa: E402

BASE = os.getenv("API_BASE", "https://telegram-miniapp-emy5.onrender.com")


def make_init_data(user_id: int, first_name: str) -> str:
    data = {
        "auth_date": str(int(time.time())),
        "query_id": "AAHsmoke",
        "user": json.dumps(
            {"id": user_id, "first_name": first_name, "username": "smoke"},
            separators=(",", ":"), ensure_ascii=False,
        ),
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def call(method, path, init, body=None, timeout=90):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("X-Telegram-Init-Data", init)
    req.add_header("Content-Type", "application/json")
    payload = json.dumps(body).encode() if body is not None else None
    t = time.time()
    try:
        with urllib.request.urlopen(req, payload, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode()), round(time.time() - t, 1)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:120], round(time.time() - t, 1)
    except Exception as e:
        return "EXC", f"{type(e).__name__}: {e}", round(time.time() - t, 1)


def main() -> int:
    failures = 0

    def check(name, ok, extra=""):
        nonlocal failures
        print(f"[{'OK' if ok else 'FAIL'}] {name} {extra}")
        if not ok:
            failures += 1

    s, d, t = call("GET", "/api/health", "")
    check("health", s == 200 and d.get("db_ok") is True, f"{d} ({t}s)")

    admin = make_init_data(990000001, "SmokeAdmin")
    s, d, t = call("POST", "/api/login", admin, {"code": "6666"})
    check("логин админа 6666", s == 200 and d.get("kind") == "admin", f"({t}s)")

    driver = make_init_data(990000002, "SmokeDriver")
    s, d, t = call("POST", "/api/login", driver, {"code": "9857"})
    check("логин водителя 9857", s == 200 and d.get("kind") == "driver", f"({t}s)")

    s, periods, t = call("GET", "/api/periods", admin)
    if isinstance(periods, list) and periods:
        pid = periods[0]["id"]
        s2, rep, t2 = call("GET", f"/api/admin/report?period_id={pid}", admin)
        check("отчёт за период", s2 == 200 and "total_count" in rep, f"({t2}s)")
    else:
        check("периоды", False, str(periods)[:80])

    print("\nИтог:", "ВСЁ ОК" if failures == 0 else f"ошибок: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
