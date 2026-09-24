"""Настройки приложения (из переменных окружения)."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Срок жизни initData (сек), по умолчанию сутки
INIT_DATA_MAX_AGE = int(os.getenv("INIT_DATA_MAX_AGE", "86400"))

# Роли админов
ADMIN_ROLES = ("owner", "commander", "technician")
ADMIN_ROLE_TITLES = {
    "owner": "Администратор",
    "commander": "Командир роты",
    "technician": "Техник роты",
}
