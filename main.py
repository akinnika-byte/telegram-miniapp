"""Telegram Mini App — проверка путевой документации.

Backend на FastAPI:
  • проверяет подпись Telegram initData (кто именно открыл приложение);
  • вход водителя по 4-значному коду = ГРЗ машины;
  • вход админов (owner / commander / technician) по кодам доступа;
  • расчёт и сохранение путевых листов в Supabase;
  • отчёты и журнал активности для админов.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from config import (
    ADMIN_ROLES,
    ADMIN_ROLE_TITLES,
    BOT_TOKEN,
    DATABASE_URL,
    INIT_DATA_MAX_AGE,
    STATIC_DIR,
)
from db import db
from periods import period_for_date
from security import InitDataError, validate_init_data
from waybill import compute

app = FastAPI(title="АВТР(ПГ) — проверка путевой документации")

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============================================================
#   Модели запросов
# ============================================================
class CodeIn(BaseModel):
    code: str


class WaybillIn(BaseModel):
    plate: str
    waybill_no: str
    date: Optional[str] = None
    km_start: float = 0
    km_end: float = 0
    km_empty: float = 0
    km_loaded: float = 0
    tank_start: float = 0
    fuel_in: float = 0
    tank_end: float = 0
    fuel_spent: float = 0
    motohours: float = 0


# ============================================================
#   Служебные функции
# ============================================================
def actor_name(user: dict) -> str:
    parts = [user.get("first_name") or "", user.get("last_name") or ""]
    name = " ".join(p for p in parts if p).strip()
    if user.get("username"):
        name = f"{name} (@{user['username']})" if name else f"@{user['username']}"
    return name or f"id{user.get('telegram_id')}"


def log_action(conn, user: dict, action: str, entity: str | None = None,
               entity_id: Any = None, details: dict | None = None) -> None:
    conn.execute(
        """
        insert into activity_log
            (telegram_id, actor_name, role, action, entity, entity_id, details)
        values (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user.get("telegram_id"),
            actor_name(user),
            user.get("role"),
            action,
            entity,
            None if entity_id is None else str(entity_id),
            Jsonb(details) if details else None,
        ),
    )


def current_user(
    x_telegram_init_data: str = Header(default="", alias="X-Telegram-Init-Data"),
) -> dict:
    """Проверяет initData и возвращает (создаёт) пользователя из app_users."""
    try:
        data = validate_init_data(x_telegram_init_data, BOT_TOKEN, INIT_DATA_MAX_AGE)
    except InitDataError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    tg_user = data.get("user") or {}
    tg_id = tg_user.get("id")
    if not tg_id:
        raise HTTPException(status_code=401, detail="в initData нет пользователя")

    with db() as conn:
        row = conn.execute(
            """
            insert into app_users (telegram_id, username, first_name, last_name)
            values (%s, %s, %s, %s)
            on conflict (telegram_id) do update set
                username    = excluded.username,
                first_name  = excluded.first_name,
                last_name   = excluded.last_name,
                last_seen_at = now()
            returning *
            """,
            (tg_id, tg_user.get("username"), tg_user.get("first_name"),
             tg_user.get("last_name")),
        ).fetchone()
    return row


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("role") not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Требуется доступ администратора")
    return user


def find_vehicle(conn, plate: str) -> dict:
    row = conn.execute(
        "select * from vehicles where plate = %s and is_active",
        (plate.strip(),),
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"Машина с ГРЗ {plate} не найдена. Обратитесь к технику роты.",
        )
    return row


def parse_date(value: Optional[str]) -> date:
    if not value:
        return date.today()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты")


# ============================================================
#   Страницы
# ============================================================
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html", media_type="text/html")


@app.get("/api/health")
def health() -> dict:
    """Диагностика: настроены ли бот и база, и доступна ли база."""
    db_ok = False
    db_error = None
    if DATABASE_URL:
        try:
            with db() as conn:
                conn.execute("select 1")
            db_ok = True
        except Exception as exc:  # noqa: BLE001
            db_error = type(exc).__name__
    return {
        "status": "ok",
        "bot_configured": bool(BOT_TOKEN),
        "db_configured": bool(DATABASE_URL),
        "db_ok": db_ok,
        "db_error": db_error,
    }


# ============================================================
#   Сессия
# ============================================================
@app.post("/api/session")
def session(user: dict = Depends(current_user)) -> dict:
    """Кто я: роль и последняя машина (если водитель)."""
    return {
        "telegram_id": user["telegram_id"],
        "name": actor_name(user),
        "role": user["role"],
        "vehicle_plate": user.get("vehicle_plate"),
        "is_admin": user["role"] in ADMIN_ROLES,
        "role_title": ADMIN_ROLE_TITLES.get(user["role"]),
    }


# ============================================================
#   Водитель
# ============================================================
@app.post("/api/driver/login")
def driver_login(payload: CodeIn, user: dict = Depends(current_user)) -> dict:
    code = (payload.code or "").strip()
    if not code.isdigit() or len(code) != 4:
        raise HTTPException(status_code=400, detail="Код — ровно 4 цифры")

    with db() as conn:
        vehicle = find_vehicle(conn, code)
        conn.execute(
            "update app_users set role = 'driver', vehicle_plate = %s, "
            "last_seen_at = now() where telegram_id = %s",
            (vehicle["plate"], user["telegram_id"]),
        )
        period = period_for_date(conn, date.today())
        log_action(conn, user, "driver_login", "vehicle", vehicle["id"],
                   {"plate": vehicle["plate"]})

    return {"vehicle": vehicle, "period": period}


@app.post("/api/waybill/check")
def waybill_check(payload: WaybillIn, user: dict = Depends(current_user)) -> dict:
    """Считает путевой без сохранения (кнопка «Проверить»)."""
    with db() as conn:
        vehicle = find_vehicle(conn, payload.plate)
        period = period_for_date(conn, parse_date(payload.date))

        result = compute(
            km_start=payload.km_start, km_end=payload.km_end,
            km_empty=payload.km_empty, km_loaded=payload.km_loaded,
            tank_start=payload.tank_start, fuel_in=payload.fuel_in,
            tank_end=payload.tank_end, fuel_spent=payload.fuel_spent,
            motohours=payload.motohours,
            fuel_norm=vehicle["fuel_norm"],
            motohour_norm=vehicle["motohour_norm"],
        )
        duplicate = conn.execute(
            "select plate from waybills where waybill_no = %s and period_id = %s",
            (payload.waybill_no.strip(), period["id"] if period else -1),
        ).fetchone()

    return {
        "result": result,
        "vehicle": vehicle,
        "period": period,
        "duplicate": (duplicate or {}).get("plate") if duplicate else None,
    }


@app.post("/api/waybill/save")
def waybill_save(payload: WaybillIn, user: dict = Depends(current_user)) -> dict:
    """Проверяет и сохраняет путевой. Период определяется по дате."""
    wb_no = (payload.waybill_no or "").strip()
    if not wb_no:
        raise HTTPException(status_code=400, detail="Укажите номер путевого листа")

    with db() as conn:
        vehicle = find_vehicle(conn, payload.plate)
        d = parse_date(payload.date)
        period = period_for_date(conn, d)
        if period is None:
            raise HTTPException(
                status_code=400,
                detail=f"Для даты {d.isoformat()} не задан отчётный период. "
                       f"Обратитесь к администратору.",
            )

        dupe = conn.execute(
            "select plate from waybills where waybill_no = %s and period_id = %s",
            (wb_no, period["id"]),
        ).fetchone()
        if dupe:
            raise HTTPException(
                status_code=409,
                detail=f"Путевой №{wb_no} уже внесён (машина {dupe['plate']})",
            )

        r = compute(
            km_start=payload.km_start, km_end=payload.km_end,
            km_empty=payload.km_empty, km_loaded=payload.km_loaded,
            tank_start=payload.tank_start, fuel_in=payload.fuel_in,
            tank_end=payload.tank_end, fuel_spent=payload.fuel_spent,
            motohours=payload.motohours,
            fuel_norm=vehicle["fuel_norm"],
            motohour_norm=vehicle["motohour_norm"],
        )

        row = conn.execute(
            """
            insert into waybills (
                period_id, vehicle_id, plate, model, waybill_no,
                km_start, km_end, km_empty, km_loaded,
                tank_start, fuel_in, tank_end, fuel_spent, motohours,
                fuel_norm_snapshot, motohour_norm_snapshot,
                p1, p2, h, fuel_calc,
                km_total_odo, km_total_input, tank_end_calc,
                km_ok, tank_ok, fuel_ok, is_ok,
                created_by_telegram_id
            ) values (
                %(period_id)s, %(vehicle_id)s, %(plate)s, %(model)s, %(waybill_no)s,
                %(km_start)s, %(km_end)s, %(km_empty)s, %(km_loaded)s,
                %(tank_start)s, %(fuel_in)s, %(tank_end)s, %(fuel_spent)s,
                %(motohours)s,
                %(fuel_norm)s, %(motohour_norm)s,
                %(p1)s, %(p2)s, %(h)s, %(fuel_calc)s,
                %(km_total_odo)s, %(km_total_input)s, %(tank_end_calc)s,
                %(km_ok)s, %(tank_ok)s, %(fuel_ok)s, %(is_ok)s,
                %(tg_id)s
            )
            returning id, created_at
            """,
            {
                **r,
                "period_id": period["id"],
                "vehicle_id": vehicle["id"],
                "plate": vehicle["plate"],
                "model": vehicle["model"],
                "waybill_no": wb_no,
                "tg_id": user["telegram_id"],
            },
        ).fetchone()

        log_action(conn, user, "create_waybill", "waybill", row["id"],
                   {"plate": vehicle["plate"], "waybill_no": wb_no,
                    "period": period["name"], "is_ok": r["is_ok"]})

    return {
        "id": row["id"],
        "result": r,
        "period": period,
        "message": "Путевой сохранён" if r["is_ok"] else "Путевой сохранён (есть расхождения)",
    }


# ============================================================
#   Админ: вход
# ============================================================
@app.post("/api/admin/login")
def admin_login(payload: CodeIn, user: dict = Depends(current_user)) -> dict:
    code = (payload.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Введите код доступа")

    with db() as conn:
        row = conn.execute(
            "select role from access_codes "
            "where code_hash = crypt(%s, code_hash)",
            (code,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=403, detail="Неверный код доступа")
    role = row["role"]

    with db() as conn:
        conn.execute(
            "update app_users set role = %s, last_seen_at = now() "
            "where telegram_id = %s",
            (role, user["telegram_id"]),
        )
        log_action(conn, user, "admin_login", "app_user", user["telegram_id"],
                   {"role": role})

    return {"role": role, "role_title": ADMIN_ROLE_TITLES.get(role, role)}


@app.post("/api/logout")
def logout(user: dict = Depends(current_user)) -> dict:
    """Сбрасывает роль до водителя (выход из админ-режима)."""
    with db() as conn:
        conn.execute(
            "update app_users set role = 'driver' where telegram_id = %s",
            (user["telegram_id"],),
        )
        log_action(conn, user, "logout", "app_user", user["telegram_id"])
    return {"ok": True}


# ============================================================
#   Админ: данные
# ============================================================
@app.get("/api/periods")
def periods_list(user: dict = Depends(current_user)) -> list:
    with db() as conn:
        return conn.execute(
            "select id, name, year, month, start_date, end_date, is_open "
            "from periods order by start_date desc"
        ).fetchall()


@app.get("/api/admin/report")
def admin_report(period_id: int, user: dict = Depends(require_admin)) -> dict:
    """Отчёт по машинам за период: суммы и статус, с раскрытием на путевые."""
    with db() as conn:
        period = conn.execute(
            "select * from periods where id = %s", (period_id,)
        ).fetchone()
        if not period:
            raise HTTPException(status_code=404, detail="Период не найден")

        records = conn.execute(
            """
            select w.*, u.first_name, u.last_name, u.username
            from waybills w
            left join app_users u on u.telegram_id = w.created_by_telegram_id
            where w.period_id = %s
            order by w.plate, w.km_start
            """,
            (period_id,),
        ).fetchall()

    groups: dict[str, dict] = {}
    for r in records:
        g = groups.setdefault(r["plate"], {
            "plate": r["plate"], "model": r["model"],
            "tank_start": float(r["tank_start"]),
            "fuel_in": 0.0, "fuel_spent": 0.0, "tank_end": float(r["tank_end"]),
            "total_km": 0.0, "count": 0, "has_bad": False, "items": [],
        })
        g["fuel_in"] += float(r["fuel_in"])
        g["fuel_spent"] += float(r["fuel_spent"])
        g["total_km"] += float(r["km_total_odo"])
        g["tank_end"] = float(r["tank_end"])
        g["count"] += 1
        if not r["is_ok"]:
            g["has_bad"] = True
        author = " ".join(filter(None, [r["first_name"], r["last_name"]])) or ""
        if r["username"]:
            author = f"{author} (@{r['username']})".strip()
        g["items"].append({
            "id": r["id"],
            "waybill_no": r["waybill_no"],
            "km_start": float(r["km_start"]),
            "km_end": float(r["km_end"]),
            "km_empty": float(r["km_empty"]),
            "km_loaded": float(r["km_loaded"]),
            "tank_start": float(r["tank_start"]),
            "fuel_in": float(r["fuel_in"]),
            "fuel_spent": float(r["fuel_spent"]),
            "tank_end": float(r["tank_end"]),
            "motohours": float(r["motohours"]),
            "fuel_calc": float(r["fuel_calc"]),
            "total_km": float(r["km_total_odo"]),
            "is_ok": r["is_ok"],
            "author": author,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })

    def sort_key(p: str):
        digits = "".join(ch for ch in p if ch.isdigit())
        return (0, int(digits)) if digits else (1, p)

    return {
        "period": period,
        "groups": [groups[p] for p in sorted(groups, key=sort_key)],
        "total_count": len(records),
        "total_bad": sum(1 for r in records if not r["is_ok"]),
    }


@app.get("/api/admin/summary")
def admin_summary(period_id: int, user: dict = Depends(require_admin)) -> dict:
    """Сводка по машинам за период."""
    with db() as conn:
        period = conn.execute(
            "select * from periods where id = %s", (period_id,)
        ).fetchone()
        if not period:
            raise HTTPException(status_code=404, detail="Период не найден")
        rows = conn.execute(
            """
            select plate, model,
                   count(*)              as wb_count,
                   coalesce(sum(km_total_odo), 0) as total_km,
                   coalesce(sum(fuel_in), 0)      as fuel_in,
                   coalesce(sum(fuel_spent), 0)   as fuel_spent,
                   coalesce(sum(fuel_calc), 0)    as fuel_calc
            from waybills
            where period_id = %s
            group by plate, model
            """,
            (period_id,),
        ).fetchall()

    def sort_key(p: str):
        digits = "".join(ch for ch in p if ch.isdigit())
        return (0, int(digits)) if digits else (1, p)

    rows.sort(key=lambda r: sort_key(r["plate"]))
    totals = {
        "wb_count": sum(r["wb_count"] for r in rows),
        "total_km": sum(float(r["total_km"]) for r in rows),
        "fuel_in": sum(float(r["fuel_in"]) for r in rows),
        "fuel_spent": sum(float(r["fuel_spent"]) for r in rows),
        "fuel_calc": sum(float(r["fuel_calc"]) for r in rows),
    }
    return {"period": period, "rows": rows, "totals": totals}


@app.get("/api/admin/activity")
def admin_activity(limit: int = 100, user: dict = Depends(require_admin)) -> list:
    limit = max(1, min(limit, 500))
    with db() as conn:
        return conn.execute(
            """
            select telegram_id, actor_name, role, action, entity,
                   entity_id, details, created_at
            from activity_log
            order by created_at desc
            limit %s
            """,
            (limit,),
        ).fetchall()


@app.get("/api/admin/vehicles")
def admin_vehicles(user: dict = Depends(require_admin)) -> list:
    with db() as conn:
        return conn.execute(
            "select * from vehicles order by plate"
        ).fetchall()
