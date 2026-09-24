"""Тестовый FastAPI-сервер для Telegram Mini App.

Пока содержит только проверочный эндпоинт, чтобы убедиться,
что деплой на Render работает. Логика будет добавлена далее.
"""

from fastapi import FastAPI

app = FastAPI(title="Telegram Mini App")


@app.get("/")
def root():
    return {"status": "ok", "message": "Telegram Mini App backend is running"}
