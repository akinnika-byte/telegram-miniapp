---
name: Проверка деплоя
description: Проверка после git push, что изменения задеплоились на Render и работают
---

Проверка после пуша в `main` (Render деплоит ~1–2 мин).

## Шаги

1. `GET https://telegram-miniapp-emy5.onrender.com/api/health` — ожидается
   `{"status":"ok","bot_configured":true,"db_configured":true,"db_ok":true,"db_error":null}`.
2. Убедиться, что страницы `/` и `/admin` отдают новую версию
   (проверить маркеры только что внесённых изменений).
3. Smoke-тест: `.venv\Scripts\python scripts/api_check.py` — генерирует подписанную
   initData и проверяет входы (6666, 9857) и отчёт за период.
4. Если сервис спал — первый запрос может занять 30–50 с (cold start), это норма.

## Если что-то не так

- `db_ok:false` — посмотреть `db_error`, проверить `DATABASE_URL` в Render → Environment.
- Вкладки не грузятся — открыть консоль ошибок страницы (в проекте есть красный
  баннер `#fatal` с текстом ошибки JS).
- См. также «Процедура деплоя» в AGENTS.md.
