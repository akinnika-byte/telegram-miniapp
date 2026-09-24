"""FastAPI-сервер Telegram Mini App.

Отдаёт приветственную страницу и служебные эндпоинты.
Секреты читаются из переменных окружения (.env локально, Render env — на сервере).
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

# Загружаем .env (локально). На Render переменные задаются в dashboard.
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Telegram Mini App")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Приветственная страница Telegram Mini App."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=500, detail="index.html not found")
    return FileResponse(index_file, media_type="text/html")


@app.get("/api/health")
def health() -> dict:
    """Проверка живости сервиса и наличия секретов (без раскрытия значений)."""
    return {
        "status": "ok",
        "message": "Telegram Mini App backend is running",
        "bot_configured": bool(os.getenv("BOT_TOKEN")),
        "supabase_configured": bool(os.getenv("NEXT_PUBLIC_SUPABASE_URL")),
    }
