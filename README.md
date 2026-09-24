# АВТР(ПГ) — Telegram Mini App

Проверка путевой документации: водитель сам проверяет путевой лист в Telegram,
данные автоматически уходят на сервер; командиры и техник роты видят отчёты,
сводки и журнал активности.

Стек: **FastAPI** (Render) + **PostgreSQL/Supabase** + Telegram Web App.

## Структура проекта

```
main.py                  — FastAPI: API и страницы
config.py                — настройки из переменных окружения
db.py                    — подключение к Supabase
security.py              — проверка подписи Telegram initData
waybill.py               — формулы расчёта путевого листа
periods.py               — определение отчётного периода по дате
sql/schema.sql           — таблицы БД
sql/seed.sql             — периоды и машины (нормы расхода)
scripts/init_db.py       — создать таблицы и залить начальные данные
scripts/set_admin_codes.py — задать коды админов
static/index.html        — экран водителя
static/admin.html        — админ-панель
```

## Формулы (не менять без согласования)

```
P1 = round(km_empty  * fuel_norm / 100 * 0.85)   # без груза, −15%
P2 = round(km_loaded * fuel_norm / 100)          # с грузом
H  = round(motohours * motohour_norm)            # моточасы
P  = P1 + P2 + H
km_total_odo   = km_end - km_start
km_total_input = km_empty + km_loaded
tank_end_calc  = tank_start + fuel_in - P
допуск EPS = 0.5
```

## Роли

| Роль | Кто | Вход |
|---|---|---|
| `driver` | водитель | 4-значный код = ГРЗ машины |
| `owner` | командир части | код доступа |
| `commander` | командир роты | код доступа |
| `technician` | техник роты | код доступа |

Коды админов хранятся в БД только в виде bcrypt-хеша (pgcrypto).

## Настройка

1. **Supabase** → SQL Editor: выполнить `sql/schema.sql`, затем `sql/seed.sql`.
   (Либо локально: `python scripts/init_db.py` при заданном `DATABASE_URL`.)
2. **Коды админов**: `python scripts/set_admin_codes.py`
   (или SQL с `crypt(..., gen_salt('bf'))` в SQL Editor).
3. **Переменные окружения** (локально — `.env`, на Render — Environment):
   - `BOT_TOKEN` — токен бота от @BotFather
   - `DATABASE_URL` — строка подключения Supabase (pooler, порт 6543)

## Запуск локально

```powershell
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn main:app --reload
```

## Деплой (Render)

- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Env: `BOT_TOKEN`, `DATABASE_URL`
- Кнопка мини-аппа задаётся в @BotFather (Menu Button) на адрес сервиса.

## Ключевые эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/api/session` | кто я (роль) |
| POST | `/api/driver/login` | вход водителя по ГРЗ |
| POST | `/api/waybill/check` | расчёт без сохранения |
| POST | `/api/waybill/save` | сохранить путевой |
| POST | `/api/admin/login` | вход админа по коду |
| GET | `/api/admin/report` | отчёт за период |
| GET | `/api/admin/summary` | сводка за период |
| GET | `/api/admin/activity` | журнал активности |

Все запросы требуют заголовок `X-Telegram-Init-Data` с подписью Telegram.
