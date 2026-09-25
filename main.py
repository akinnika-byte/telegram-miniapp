"""Telegram Mini App — проверка путевой документации.

Backend на FastAPI:
  • проверяет подпись Telegram initData (кто именно открыл приложение);
  • вход водителя по 4-значному коду = ГРЗ машины;
  • вход админов (owner / commander / technician) по кодам доступа;
  • расчёт и сохранение путевых листов в Supabase;
  • отчёты и журнал активности для админов.
"""

from __future__ import annotations

import time
from collections import deque
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
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


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Логирует необработанные ошибки в error_log (для диагностики)."""
    try:
        with db() as conn:
            conn.execute(
                "insert into error_log (method, path, detail) values (%s, %s, %s)",
                (request.method, request.url.path,
                 f"{type(exc).__name__}: {str(exc)[:800]}"),
            )
    except Exception:
        pass
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})


REQUEST_LOG = deque(maxlen=400)
_req_counter = 0


def request_log_counter() -> int:
    global _req_counter
    _req_counter += 1
    return _req_counter


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Лёгкий журнал запросов в памяти (без обращений к БД).

    status=-1 — запрос в процессе, -2 — упал.
    """
    start = time.time()
    entry = {
        "id": request_log_counter(),
        "method": request.method,
        "path": request.url.path[:200],
        "status": -1,
        "duration_ms": 0,
        "created_at": datetime.now(timezone.utc),
    }
    REQUEST_LOG.appendleft(entry)
    try:
        response = await call_next(request)
    except Exception:
        entry["status"] = -2
        entry["duration_ms"] = int((time.time() - start) * 1000)
        raise
    entry["status"] = response.status_code
    entry["duration_ms"] = int((time.time() - start) * 1000)
    return response


# ============================================================
#   Модели запросов
# ============================================================
class CodeIn(BaseModel):
    code: str


class WaybillIn(BaseModel):
    id: Optional[int] = None          # если задан — редактируем существующий
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


class VehicleIn(BaseModel):
    """Создание/редактирование машины (техник роты)."""
    id: Optional[int] = None
    plate: str
    model: str = ""
    fuel_norm: float = 0
    motohour_norm: float = 0
    tank_capacity: float = 0
    is_active: bool = True
    vin: Optional[str] = None
    engine_no: Optional[str] = None
    chassis_no: Optional[str] = None
    driver_name: Optional[str] = None
    driver_rank: Optional[str] = None
    driver_position: Optional[str] = None
    driver_license: Optional[str] = None
    sts_expires: Optional[str] = None
    diagnostic_card_expires: Optional[str] = None
    red_stripe_expires: Optional[str] = None


class SqlIn(BaseModel):
    """Запрос на чтение к базе (только для админов и MCP-прокси)."""
    sql: str


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


def parse_optional_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Неверный формат даты")


def period_sig(conn, period_id: int) -> str:
    """Отпечаток набора путевых периода: меняется при добавлении/правке."""
    row = conn.execute(
        "select count(*) as n, max(updated_at) as m "
        "from waybills where period_id = %s",
        (period_id,),
    ).fetchone()
    stamp = int(row["m"].timestamp()) if row["m"] else 0
    return f"{row['n']}-{stamp}"


# ============================================================
#   Страницы
# ============================================================
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "admin.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


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


@app.post("/api/login")
def login(payload: CodeIn, user: dict = Depends(current_user)) -> dict:
    """Единый вход по 4-значному коду.

    Сначала ищем машину с таким ГРЗ (водитель), затем код администратора.
    """
    code = (payload.code or "").strip()
    if not code.isdigit() or len(code) != 4:
        raise HTTPException(status_code=400, detail="Код — ровно 4 цифры")

    with db() as conn:
        vehicle = conn.execute(
            "select * from vehicles where plate = %s and is_active", (code,)
        ).fetchone()
        if vehicle:
            conn.execute(
                "update app_users set role = 'driver', vehicle_plate = %s, "
                "last_seen_at = now() where telegram_id = %s",
                (vehicle["plate"], user["telegram_id"]),
            )
            log_action(conn, user, "driver_login", "vehicle", vehicle["id"],
                       {"plate": vehicle["plate"]})
            return {"kind": "driver", "vehicle": vehicle}

        admin = conn.execute(
            "select role from access_codes where code_hash = crypt(%s, code_hash)",
            (code,),
        ).fetchone()
        if admin:
            conn.execute(
                "update app_users set role = %s, last_seen_at = now() "
                "where telegram_id = %s",
                (admin["role"], user["telegram_id"]),
            )
            log_action(conn, user, "admin_login", "app_user", user["telegram_id"],
                       {"role": admin["role"]})
            return {
                "kind": "admin",
                "role": admin["role"],
                "role_title": ADMIN_ROLE_TITLES.get(admin["role"], admin["role"]),
            }

    raise HTTPException(
        status_code=404,
        detail="Код не распознан. Проверьте ГРЗ машины или код доступа.",
    )


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


def _driver_plate(user: dict) -> str:
    plate = user.get("vehicle_plate")
    if not plate:
        raise HTTPException(status_code=409, detail="Сначала введите код машины")
    return plate


@app.get("/api/driver/profile")
def driver_profile(user: dict = Depends(current_user)) -> dict:
    """Профиль водителя: ФИО, права, закреплённая техника, кол-во путевых."""
    plate = _driver_plate(user)
    with db() as conn:
        v = conn.execute("select * from vehicles where plate = %s", (plate,)).fetchone()
        n = conn.execute(
            "select count(*) as n from waybills where plate = %s", (plate,)
        ).fetchone()["n"]
    return {
        "plate": plate,
        "full_name": (v or {}).get("driver_name"),
        "rank": (v or {}).get("driver_rank"),
        "position": (v or {}).get("driver_position"),
        "license_number": (v or {}).get("driver_license"),
        "model": (v or {}).get("model"),
        "closed_docs": n,
    }


@app.get("/api/driver/vehicle")
def driver_vehicle(user: dict = Depends(current_user)) -> dict:
    """Всё о машине водителя (в т.ч. VIN, двигатель, сроки документов)."""
    plate = _driver_plate(user)
    with db() as conn:
        v = conn.execute("select * from vehicles where plate = %s", (plate,)).fetchone()
    if not v:
        raise HTTPException(status_code=404, detail="Машина не найдена")
    return v


@app.get("/api/driver/waybills")
def driver_waybills(user: dict = Depends(current_user)) -> list:
    """История путевых листов водителя (по его машине)."""
    plate = _driver_plate(user)
    with db() as conn:
        return conn.execute(
            """
            select w.id, w.waybill_no, w.plate, w.created_at, w.waybill_date,
                   w.km_start, w.km_end, w.km_empty, w.km_loaded,
                   w.tank_start, w.fuel_in, w.tank_end, w.fuel_spent, w.motohours,
                   w.km_total_odo, w.fuel_calc, w.is_ok, w.is_corrected,
                   p.name as period_name
            from waybills w
            join periods p on p.id = w.period_id
            where w.plate = %s
            order by w.created_at desc
            limit 200
            """,
            (plate,),
        ).fetchall()


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

        # редактирование существующего путевого
        record = None
        if payload.id:
            record = conn.execute(
                "select * from waybills where id = %s", (payload.id,)
            ).fetchone()
            if not record:
                raise HTTPException(status_code=404, detail="Путевой не найден")
            if user.get("role") not in ADMIN_ROLES and \
                    record["plate"] != (user.get("vehicle_plate") or ""):
                raise HTTPException(
                    status_code=403, detail="Можно изменить только свой путевой"
                )

        dupe = conn.execute(
            "select plate from waybills "
            "where waybill_no = %s and period_id = %s and id <> %s",
            (wb_no, period["id"], payload.id or -1),
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

        params = {
            **r,
            "period_id": period["id"],
            "vehicle_id": vehicle["id"],
            "plate": vehicle["plate"],
            "model": vehicle["model"],
            "waybill_no": wb_no,
            "wb_date": d,
            "tg_id": user["telegram_id"],
        }

        if payload.id:
            params["id"] = payload.id
            row = conn.execute(
                """
                update waybills set
                    period_id=%(period_id)s, vehicle_id=%(vehicle_id)s,
                    plate=%(plate)s, model=%(model)s, waybill_no=%(waybill_no)s,
                    waybill_date=%(wb_date)s,
                    km_start=%(km_start)s, km_end=%(km_end)s,
                    km_empty=%(km_empty)s, km_loaded=%(km_loaded)s,
                    tank_start=%(tank_start)s, fuel_in=%(fuel_in)s,
                    tank_end=%(tank_end)s, fuel_spent=%(fuel_spent)s,
                    motohours=%(motohours)s,
                    fuel_norm_snapshot=%(fuel_norm)s,
                    motohour_norm_snapshot=%(motohour_norm)s,
                    p1=%(p1)s, p2=%(p2)s, h=%(h)s, fuel_calc=%(fuel_calc)s,
                    km_total_odo=%(km_total_odo)s,
                    km_total_input=%(km_total_input)s,
                    tank_end_calc=%(tank_end_calc)s,
                    km_ok=%(km_ok)s, tank_ok=%(tank_ok)s, fuel_ok=%(fuel_ok)s,
                    is_ok=%(is_ok)s,
                    is_corrected=true, corrected_at=now(), updated_at=now()
                where id = %(id)s
                returning id, created_at, is_corrected
                """,
                params,
            ).fetchone()
            action = "update_waybill"
        else:
            row = conn.execute(
                """
                insert into waybills (
                    period_id, vehicle_id, plate, model, waybill_no, waybill_date,
                    km_start, km_end, km_empty, km_loaded,
                    tank_start, fuel_in, tank_end, fuel_spent, motohours,
                    fuel_norm_snapshot, motohour_norm_snapshot,
                    p1, p2, h, fuel_calc,
                    km_total_odo, km_total_input, tank_end_calc,
                    km_ok, tank_ok, fuel_ok, is_ok,
                    created_by_telegram_id
                ) values (
                    %(period_id)s, %(vehicle_id)s, %(plate)s, %(model)s,
                    %(waybill_no)s, %(wb_date)s,
                    %(km_start)s, %(km_end)s, %(km_empty)s, %(km_loaded)s,
                    %(tank_start)s, %(fuel_in)s, %(tank_end)s, %(fuel_spent)s,
                    %(motohours)s,
                    %(fuel_norm)s, %(motohour_norm)s,
                    %(p1)s, %(p2)s, %(h)s, %(fuel_calc)s,
                    %(km_total_odo)s, %(km_total_input)s, %(tank_end_calc)s,
                    %(km_ok)s, %(tank_ok)s, %(fuel_ok)s, %(is_ok)s,
                    %(tg_id)s
                )
                returning id, created_at, is_corrected
                """,
                params,
            ).fetchone()
            action = "create_waybill"

        log_action(conn, user, action, "waybill", row["id"],
                   {"plate": vehicle["plate"], "waybill_no": wb_no,
                    "period": period["name"], "is_ok": r["is_ok"]})

    corrected = bool(row.get("is_corrected")) or bool(payload.id)
    if payload.id:
        message = ("Путевой исправлен" if r["is_ok"]
                   else "Путевой исправлен (остались расхождения)")
    else:
        message = ("Путевой сохранён" if r["is_ok"]
                   else "Путевой сохранён (есть расхождения)")

    return {
        "id": row["id"],
        "result": r,
        "period": period,
        "is_corrected": corrected,
        "message": message,
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
        sig = period_sig(conn, period_id)

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
            "is_corrected": r["is_corrected"],
            "waybill_date": r["waybill_date"].isoformat() if r["waybill_date"] else None,
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
        "sig": sig,
    }


@app.get("/api/admin/summary")
def admin_summary(period_id: int, user: dict = Depends(require_admin)) -> dict:
    """Сводка по машинам за период, с раскрытием на отдельные путевые.

    По машине отдаётся суммарный пробег/топливо, а в items — каждый путевой.
    """
    with db() as conn:
        period = conn.execute(
            "select * from periods where id = %s", (period_id,)
        ).fetchone()
        if not period:
            raise HTTPException(status_code=404, detail="Период не найден")
        records = conn.execute(
            """
            select id, plate, model, waybill_no, km_total_odo, fuel_in,
                   fuel_spent, fuel_calc, is_ok, is_corrected
            from waybills
            where period_id = %s
            order by plate, created_at
            """,
            (period_id,),
        ).fetchall()
        sig = period_sig(conn, period_id)

    groups: dict[str, dict] = {}
    for r in records:
        g = groups.setdefault(r["plate"], {
            "plate": r["plate"], "model": r["model"], "wb_count": 0,
            "total_km": 0.0, "fuel_in": 0.0, "fuel_spent": 0.0, "fuel_calc": 0.0,
            "has_bad": False, "items": [],
        })
        g["wb_count"] += 1
        g["total_km"] += float(r["km_total_odo"])
        g["fuel_in"] += float(r["fuel_in"])
        g["fuel_spent"] += float(r["fuel_spent"])
        g["fuel_calc"] += float(r["fuel_calc"])
        if not r["is_ok"]:
            g["has_bad"] = True
        g["items"].append({
            "id": r["id"],
            "waybill_no": r["waybill_no"],
            "total_km": float(r["km_total_odo"]),
            "fuel_in": float(r["fuel_in"]),
            "fuel_spent": float(r["fuel_spent"]),
            "fuel_calc": float(r["fuel_calc"]),
            "is_ok": r["is_ok"],
            "is_corrected": r["is_corrected"],
        })

    def sort_key(p: str):
        digits = "".join(ch for ch in p if ch.isdigit())
        return (0, int(digits)) if digits else (1, p)

    rows = [groups[p] for p in sorted(groups, key=sort_key)]
    totals = {
        "wb_count": sum(r["wb_count"] for r in rows),
        "total_km": sum(r["total_km"] for r in rows),
        "fuel_in": sum(r["fuel_in"] for r in rows),
        "fuel_spent": sum(r["fuel_spent"] for r in rows),
        "fuel_calc": sum(r["fuel_calc"] for r in rows),
    }
    return {"period": period, "rows": rows, "totals": totals, "sig": sig}


@app.get("/api/admin/requests")
def admin_requests(limit: int = 30, user: dict = Depends(require_admin)) -> list:
    """Последние запросы к серверу (в памяти, status=-1 — в процессе)."""
    limit = max(1, min(limit, 200))
    return list(REQUEST_LOG)[:limit]


@app.post("/api/admin/sql")
def admin_sql(payload: SqlIn, user: dict = Depends(require_admin)) -> dict:
    """Выполняет один запрос только на чтение (для MCP-прокси и админов)."""
    sql = (payload.sql or "").strip().rstrip(";").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="Пустой запрос")
    if ";" in sql:
        raise HTTPException(status_code=400, detail="Разрешён только один запрос")
    first = sql.lstrip().split(None, 1)[0].upper()
    if first in ("SELECT", "WITH", "TABLE", "VALUES"):
        if "limit" not in sql.lower():
            sql = f"{sql} LIMIT 200"
    elif first not in ("SHOW", "EXPLAIN"):
        raise HTTPException(
            status_code=400,
            detail="Разрешены только запросы на чтение (SELECT/SHOW/EXPLAIN)",
        )
    with db() as conn:
        rows = conn.execute(sql).fetchall()
    return {"rows": rows}


@app.get("/api/admin/errors")
def admin_errors(limit: int = 50, user: dict = Depends(require_admin)) -> list:
    """Последние ошибки сервера (диагностика)."""
    limit = max(1, min(limit, 200))
    with db() as conn:
        return conn.execute(
            "select id, method, path, detail, created_at "
            "from error_log order by id desc limit %s",
            (limit,),
        ).fetchall()


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


@app.get("/api/admin/vehicles/{vehicle_id}")
def admin_vehicle(vehicle_id: int, user: dict = Depends(require_admin)) -> dict:
    with db() as conn:
        v = conn.execute("select * from vehicles where id = %s", (vehicle_id,)).fetchone()
    if not v:
        raise HTTPException(status_code=404, detail="Машина не найдена")
    return v


@app.post("/api/admin/vehicles/save")
def admin_vehicle_save(payload: VehicleIn, user: dict = Depends(require_admin)) -> dict:
    """Создание или обновление машины (техник роты)."""
    plate = (payload.plate or "").strip()
    if not plate.isdigit() or len(plate) != 4:
        raise HTTPException(status_code=400, detail="ГРЗ — ровно 4 цифры")

    data = {
        "plate": plate,
        "model": payload.model or "",
        "fuel_norm": payload.fuel_norm,
        "motohour_norm": payload.motohour_norm,
        "tank_capacity": payload.tank_capacity,
        "is_active": payload.is_active,
        "vin": payload.vin,
        "engine_no": payload.engine_no,
        "chassis_no": payload.chassis_no,
        "driver_name": payload.driver_name,
        "driver_rank": payload.driver_rank,
        "driver_position": payload.driver_position,
        "driver_license": payload.driver_license,
        "sts_expires": parse_optional_date(payload.sts_expires),
        "diagnostic_card_expires": parse_optional_date(payload.diagnostic_card_expires),
        "red_stripe_expires": parse_optional_date(payload.red_stripe_expires),
    }

    with db() as conn:
        if payload.id:
            data["id"] = payload.id
            row = conn.execute(
                """
                update vehicles set
                    plate=%(plate)s, model=%(model)s, fuel_norm=%(fuel_norm)s,
                    motohour_norm=%(motohour_norm)s, tank_capacity=%(tank_capacity)s,
                    is_active=%(is_active)s, vin=%(vin)s, engine_no=%(engine_no)s,
                    chassis_no=%(chassis_no)s, driver_name=%(driver_name)s,
                    driver_rank=%(driver_rank)s, driver_position=%(driver_position)s,
                    driver_license=%(driver_license)s, sts_expires=%(sts_expires)s,
                    diagnostic_card_expires=%(diagnostic_card_expires)s,
                    red_stripe_expires=%(red_stripe_expires)s, updated_at=now()
                where id = %(id)s
                returning *
                """,
                data,
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Машина не найдена")
            action = "update_vehicle"
        else:
            row = conn.execute(
                """
                insert into vehicles
                    (plate, model, fuel_norm, motohour_norm, tank_capacity,
                     is_active, vin, engine_no, chassis_no, driver_name,
                     driver_rank, driver_position, driver_license,
                     sts_expires, diagnostic_card_expires, red_stripe_expires)
                values
                    (%(plate)s, %(model)s, %(fuel_norm)s, %(motohour_norm)s,
                     %(tank_capacity)s, %(is_active)s, %(vin)s, %(engine_no)s,
                     %(chassis_no)s, %(driver_name)s, %(driver_rank)s,
                     %(driver_position)s, %(driver_license)s,
                     %(sts_expires)s, %(diagnostic_card_expires)s,
                     %(red_stripe_expires)s)
                on conflict (plate) do update set
                    model=excluded.model, fuel_norm=excluded.fuel_norm,
                    motohour_norm=excluded.motohour_norm,
                    tank_capacity=excluded.tank_capacity, is_active=excluded.is_active,
                    vin=excluded.vin, engine_no=excluded.engine_no,
                    chassis_no=excluded.chassis_no, driver_name=excluded.driver_name,
                    driver_rank=excluded.driver_rank,
                    driver_position=excluded.driver_position,
                    driver_license=excluded.driver_license,
                    sts_expires=excluded.sts_expires,
                    diagnostic_card_expires=excluded.diagnostic_card_expires,
                    red_stripe_expires=excluded.red_stripe_expires,
                    updated_at=now()
                returning *
                """,
                data,
            ).fetchone()
            action = "create_vehicle"

        log_action(conn, user, action, "vehicle", row["id"], {"plate": plate})

    return row


@app.get("/api/admin/version")
def admin_version(period_id: int, user: dict = Depends(require_admin)) -> dict:
    """Лёгкая проверка: изменились ли путевые периода."""
    with db() as conn:
        return {"sig": period_sig(conn, period_id)}


@app.get("/api/admin/vehicle-waybills")
def admin_vehicle_waybills(plate: str, user: dict = Depends(require_admin)) -> list:
    """Все путевые конкретной машины (для карточки водителя)."""
    with db() as conn:
        return conn.execute(
            """
            select w.id, w.waybill_no, w.plate, w.created_at, w.waybill_date,
                   w.km_start, w.km_end, w.km_empty, w.km_loaded,
                   w.tank_start, w.fuel_in, w.tank_end, w.fuel_spent, w.motohours,
                   w.km_total_odo, w.fuel_calc, w.is_ok, w.is_corrected,
                   p.name as period_name
            from waybills w
            join periods p on p.id = w.period_id
            where w.plate = %s
            order by w.created_at desc
            limit 200
            """,
            (plate,),
        ).fetchall()


@app.get("/api/admin/drivers")
def admin_drivers(user: dict = Depends(require_admin)) -> list:
    """Водительский состав: машина → водитель и число путевых."""
    with db() as conn:
        return conn.execute(
            """
            select v.id, v.plate, v.model, v.driver_name, v.driver_rank,
                   v.driver_position, v.driver_license,
                   count(w.id) as waybill_count
            from vehicles v
            left join waybills w on w.vehicle_id = v.id
            group by v.id, v.plate, v.model, v.driver_name, v.driver_rank,
                     v.driver_position, v.driver_license
            order by v.plate
            """
        ).fetchall()


# ============================================================
#   Админ: миграции схемы (идемпотентно)
# ============================================================
@app.post("/api/admin/migrate")
def admin_migrate(user: dict = Depends(require_admin)) -> dict:
    """Добавляет недостающие поля таблиц. Безопасно запускать повторно."""
    statements = [
        "alter table waybills add column if not exists is_corrected "
        "boolean not null default false",
        "alter table waybills add column if not exists corrected_at timestamptz",
        "alter table waybills add column if not exists waybill_date date",
        "update waybills set waybill_date = created_at::date "
        "where waybill_date is null",
        "create table if not exists error_log ("
        "id bigserial primary key, method text, path text, detail text, "
        "created_at timestamptz not null default now())",
        "create table if not exists request_log ("
        "id bigserial primary key, method text, path text, status int, "
        "duration_ms int, created_at timestamptz not null default now())",
    ]
    with db() as conn:
        for sql in statements:
            conn.execute(sql)
    return {"ok": True, "applied": len(statements)}
