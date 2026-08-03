"""Store settings held in the database so they can be changed without a code edit.

Anything not set falls back to the matching name in config.py, so the shop still
works on a database that predates a setting being added.
"""

from __future__ import annotations

from flask import current_app, g

from .db import get_db

# name -> (config.py fallback key, type). Only these can be written.
EDITABLE = {
    "STORE_NAME": ("STORE_NAME", "text"),
    "STORE_TAGLINE": ("STORE_TAGLINE", "text"),
    "CURRENCY_SYMBOL": ("CURRENCY_SYMBOL", "text"),
    "HERO_HEADING": (None, "text"),
    "HERO_SUBHEADING": (None, "text"),
    "SHIPPING_FLAT_CENTS": ("SHIPPING_FLAT_CENTS", "cents"),
    "FREE_SHIPPING_THRESHOLD_CENTS": ("FREE_SHIPPING_THRESHOLD_CENTS", "cents"),
    "TAX_RATE": ("TAX_RATE", "rate"),
    "COMMISSION_RATE": ("COMMISSION_RATE", "rate"),
    "ALLOW_GUEST_CHECKOUT": ("ALLOW_GUEST_CHECKOUT", "bool"),
}

DEFAULTS = {
    "HERO_HEADING": "Everything you need, one cart away.",
    "HERO_SUBHEADING": "Electronics, fashion, groceries, pet gear and more - shipped free over $50.",
}


def _stored() -> dict[str, str]:
    """Read every setting once per request."""
    if "settings" not in g:
        try:
            rows = get_db().execute("SELECT key, value FROM settings").fetchall()
        except Exception:  # a database older than the settings table
            rows = []
        g.settings = {row["key"]: row["value"] for row in rows}
    return g.settings


def get(name: str):
    """Current value, coerced to the type declared in EDITABLE."""
    raw = _stored().get(name)
    if raw is None:
        fallback, _ = EDITABLE.get(name, (name, "text"))
        if fallback is not None and fallback in current_app.config:
            return current_app.config[fallback]
        return DEFAULTS.get(name, "")

    kind = EDITABLE.get(name, (None, "text"))[1]
    try:
        if kind == "cents":
            return int(raw)
        if kind == "rate":
            return float(raw)
        if kind == "bool":
            return raw == "1"
    except ValueError:
        pass
    return raw


def save(values: dict[str, str]) -> list[str]:
    """Validate and persist. Returns error messages; writes nothing if any fail."""
    errors, cleaned = [], {}

    for name, kind in ((n, k) for n, (_, k) in EDITABLE.items()):
        if name not in values:
            continue
        raw = (values.get(name) or "").strip()

        if kind == "text":
            if not raw and name in ("STORE_NAME", "CURRENCY_SYMBOL"):
                errors.append(f"{name.replace('_', ' ').title()} cannot be empty.")
                continue
            cleaned[name] = raw[:120]
        elif kind == "cents":
            try:
                cents = int(round(float(raw) * 100))
            except ValueError:
                errors.append(f"{name.replace('_', ' ').title()} must be a number.")
                continue
            if not 0 <= cents <= 100_000_00:
                errors.append(f"{name.replace('_', ' ').title()} is out of range.")
                continue
            cleaned[name] = str(cents)
        elif kind == "rate":
            try:
                percent = float(raw)
            except ValueError:
                errors.append(f"{name.replace('_', ' ').title()} must be a percentage.")
                continue
            if not 0 <= percent <= 100:
                errors.append(f"{name.replace('_', ' ').title()} must be 0-100%.")
                continue
            cleaned[name] = str(percent / 100)
        elif kind == "bool":
            cleaned[name] = "1" if raw else "0"

    if errors:
        return errors

    db = get_db()
    db.executemany(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        list(cleaned.items()),
    )
    db.commit()
    g.pop("settings", None)
    return []
