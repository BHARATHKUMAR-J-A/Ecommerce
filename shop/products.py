"""Product form parsing shared by the admin area and the seller dashboard."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import request

from .db import get_db

MAX_PRICE = Decimal("1000000")


def price_to_cents(raw: str) -> int | None:
    """Parse money with Decimal - binary floats cannot hold 0.07 exactly."""
    try:
        value = Decimal((raw or "").strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None
    if value < 0 or value > MAX_PRICE:
        return None
    return int(value.scaleb(2).to_integral_value())


def read_product_fields(*, require_sku: bool) -> tuple[dict, list[str]]:
    errors: list[str] = []
    form = {
        "name": (request.form.get("name") or "").strip()[:120],
        "description": (request.form.get("description") or "").strip()[:2000],
        "icon": ((request.form.get("icon") or "").strip() or "\U0001F4E6")[:8],
        "category_id": request.form.get("category_id", ""),
        "price": (request.form.get("price") or "").strip(),
        "stock": (request.form.get("stock") or "0").strip(),
        "is_active": 1 if request.form.get("is_active") else 0,
    }

    if require_sku:
        form["sku"] = (request.form.get("sku") or "").strip().upper()[:32]
        if not form["sku"]:
            errors.append("SKU is required.")

    if not form["name"]:
        errors.append("Name is required.")

    cents = price_to_cents(form["price"])
    if cents is None:
        errors.append("Price must be a positive amount.")
    form["price_cents"] = cents or 0

    try:
        form["stock"] = max(0, min(1_000_000, int(form["stock"])))
    except ValueError:
        errors.append("Stock must be a whole number.")
        form["stock"] = 0

    known_category = get_db().execute(
        "SELECT 1 FROM categories WHERE id = ?", (form["category_id"],)
    ).fetchone()
    if known_category is None:
        errors.append("Please choose a valid category.")

    return form, errors
