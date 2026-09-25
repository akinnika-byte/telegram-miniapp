# АВТР(ПГ) — проверка путевой документации (Telegram Mini App)

## Суть
Водитель в Telegram вводит 4-значный код (ГРЗ машины), вносит данные путевого листа — приложение считает топливо, километраж и баки, показывает сверку «ваши данные / по расчёту» и сразу сохраняет результат. Администратор, командир роты и техник роты видят отчёты, сводку, журнал активности и управляют справочниками.

## Стек и окружение
- Backend: Python + FastAPI. Деплой — Render Web Service `telegram-miniapp-emy5`, автодеплой из ветки `main`.
- БД: Supabase (PostgreSQL), project ref `agjmazjbrfmllxzuijwf`, регион eu-west-2.
  - Приложение использует session-пулер (порт 5432) через пул psycopg_pool (см. db.py).
  - Скрипты/миграции могут ходить через transaction-пулер (порт 6543); для него обязателен `prepare_threshold=None`.
- Frontend: `static/index.html` (водитель), `static/admin.html` (панель управления). Telegram WebApp SDK, тема из tg-переменных.
- Секреты: файл `.env` (gitignored): `BOT_TOKEN`, `DATABASE_URL`. **Репозиторий публичный — секреты и персональные данные водителей в git не коммитить.**

## Ключевые файлы
- `main.py` — все эндпоинты
- `waybill.py` — формулы расчёта (единственный источник правды)
- `periods.py` — определение отчётного периода по дате
- `security.py` — проверка подписи initData (HMAC-SHA256, ключ "WebAppData" + BOT_TOKEN)
- `db.py` — пул соединений с Supabase
- `sql/schema.sql` — схема (идемпотентная), `sql/seed.sql` — периоды и 19 машин
- `scripts/init_db.py`, `scripts/set_admin_codes.py`, `scripts/api_check.py`

## Формулы путевого (НЕ менять без согласования с заказчиком)
```
P1 = round(km_empty  * fuel_norm / 100 * 0.85)   # без груза, −15%
P2 = round(km_loaded * fuel_norm / 100)          # с грузом
H  = round(motohours * motohour_norm)            # моточасы (если норма задана)
P  = P1 + P2 + H
km_total_odo   = km_end - km_start
km_total_input = km_empty + km_loaded
tank_end_calc  = tank_start + fuel_in - P
```
Допуск `EPS = 0.5`. Путевой корректен, если `km_ok` и `tank_ok` и `fuel_ok`.

## Роли и коды доступа
- `driver` — код = 4 цифры ГРЗ (`vehicles.plate`)
- `owner` — 6666, «Администратор»
- `commander` — 5555, «Командир роты»
- `technician` — 7777, «Техник роты»

У всех трёх админ-ролей на данный момент одинаковый функционал. Коды хранятся в `access_codes` как bcrypt (pgcrypto). Единый вход — `POST /api/login`: сначала ищется машина по ГРЗ, затем админ-код.

## Отчётные периоды
Таблица `periods` (name, start_date, end_date, is_open). Границы нестандартные (по приказу), путевой привязывается к периоду по `waybill_date`:
- Октябрь 26: 2026-09-21 … 2026-10-20
- Ноябрь 26: 2026-10-21 … 2026-11-15
- Декабрь 26: 2026-11-16 … 2026-12-20

Новые периоды добавляются строками в таблицу — код не менять.

## Безопасность
- Все API-эндпоинты (кроме страниц и `/api/health`) требуют заголовок `X-Telegram-Init-Data`; подпись проверяется в `security.py`.
- RLS включён на всех таблицах; бэкенд подключается как `postgres`.
- `initData` устаревает через `INIT_DATA_MAX_AGE` (по умолчанию 86400 с).

## Инструменты агента (MCP)
- `sqlproxy.query` — SQL только на чтение к базе (через `POST /api/admin/sql` на Render; надёжнее прямого подключения к Supabase с этой машины).
- `db-admin` — сабагент для диагностики базы (права: только sqlproxy).
- `github`, `context7` — удалённые MCP (если подключены).

## Процедура деплоя
1. `git push origin main` → Render передеплоивает сам (~1–2 мин).
2. Проверка: `GET https://telegram-miniapp-emy5.onrender.com/api/health` → `db_ok: true`.
3. Проверить, что `/` и `/admin` отдают новую версию.
4. Smoke-тест: `python scripts/api_check.py` (генерирует подписанную initData и проверяет вход/отчёт).

## Известные грабли
- psycopg + transaction-пулер (6543): обязателен `prepare_threshold=None`.
- Render Free засыпает после ~15 мин простоя; настроен autoping (cron-job.org) на `/api/health`.
- В `admin.html` обработчики вешать только через помощника `on(id, evt, fn)`; не ссылаться на удалённые id (была ошибка с `form-back`/`wb-back`, ломавшая всю страницу).
- Кнопки «назад» в админке — атрибут `data-backto`, а не id.
- JS должен работать на старых webview: не использовать `flatMap`, `??`, `AbortController` без проверки.
- После тестов чистить тестовые данные (`waybills`, `app_users`, `activity_log`).
- Кэш Telegram: страницы отдают `Cache-Control: no-store`; при изменении админки менять параметр версии в редиректе `/admin?v=N#just` в `index.html`.
