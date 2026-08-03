"""Application configuration."""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def load_secret_key(instance_path: str) -> bytes | str:
    """Prefer the env var; otherwise persist a random key under instance/."""
    from_env = os.environ.get("SHOP_SECRET_KEY")
    if from_env:
        return from_env

    key_file = Path(instance_path) / "secret_key"
    if key_file.exists():
        return key_file.read_bytes()

    key = secrets.token_bytes(32)
    key_file.write_bytes(key)
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return key


class Config:
    STORE_NAME = "ShopSphere"
    STORE_TAGLINE = "Everything you need, one cart away."
    CURRENCY_SYMBOL = "$"

    # Fulfilment rules
    SHIPPING_FLAT_CENTS = 499
    FREE_SHIPPING_THRESHOLD_CENTS = 5000
    TAX_RATE = 0.08
    MAX_QTY_PER_LINE = 20
    PRODUCTS_PER_PAGE = 12

    # Session hardening. SESSION_COOKIE_SECURE must be on behind HTTPS.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SHOP_HTTPS", "").lower() in ("1", "true", "yes")
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 14
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024
