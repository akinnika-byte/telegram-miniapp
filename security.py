"""Проверка подлинности Telegram WebApp initData.

Документация:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

Без этой проверки любой сможет подделать telegram_id и роль.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InitDataError(Exception):
    pass


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict:
    """Проверяет подпись initData и возвращает разобранные данные.

    Возвращает словарь с ключами: user, auth_date, query_id, ... 
    Бросает InitDataError при неверной подписи или истёкшем сроке.
    """
    if not init_data:
        raise InitDataError("initData пуста")
    if not bot_token:
        raise InitDataError("BOT_TOKEN не задан на сервере")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InitDataError("в initData нет hash")

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(pairs.items())
    )
    secret_key = hmac.new(
        b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
    ).digest()
    computed_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise InitDataError("подпись initData не совпала")

    auth_date = int(pairs.get("auth_date", "0") or 0)
    if max_age_seconds and auth_date:
        if time.time() - auth_date > max_age_seconds:
            raise InitDataError("initData устарела")

    result = dict(pairs)
    if "user" in result:
        try:
            result["user"] = json.loads(result["user"])
        except (ValueError, TypeError):
            result["user"] = None
    return result
