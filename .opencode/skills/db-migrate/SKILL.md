---
name: Миграции базы данных
description: Как применять изменения схемы Supabase — новые колонки и таблицы
---

Изменения схемы описаны в `sql/schema.sql` (идемпотентно: `CREATE TABLE IF NOT EXISTS`,
`ADD COLUMN IF NOT EXISTS`).

## Способы применения

1. **Скриптом** (нужен venv с зависимостями и `DATABASE_URL` в `.env`):
   `.venv\Scripts\python scripts/init_db.py` — выполняет schema.sql + seed.sql.
2. **Вручную**: Supabase → SQL Editor → вставить содержимое `sql/schema.sql`
   (или только нужные ALTER) → Run.
3. **Через API**: `POST /api/admin/migrate` (требует роль админа) — применяет
   встроенный набор ALTER из main.py.

## Правила

- Любая новая колонка добавляется и в `CREATE TABLE` (для свежих установок),
  и в блок ALTER в конце schema.sql (для существующих баз).
- Если у агента нет сети до Supabase напрямую — использовать способ 2 или 3,
  а не «долбиться» в пулер из песочницы.
- После миграции прогнать скилл «Проверка деплоя».
