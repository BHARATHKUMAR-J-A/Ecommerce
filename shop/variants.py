"""Product variants: one buyable row per glaze, size or colour."""

from __future__ import annotations

import sqlite3

from .db import get_db

MAX_VARIANTS = 12
MAX_LABEL = 60


def variants_for(product_id: int, active_only: bool = True) -> list:
    clause = " AND is_active = 1" if active_only else ""
    return get_db().execute(
        f"SELECT * FROM product_variants WHERE product_id = ?{clause}"
        " ORDER BY position, id",
        (product_id,),
    ).fetchall()


def refresh_product_stock(db: sqlite3.Connection, product_id: int) -> None:
    """Roll variant stock up into products.stock.

    Every existing query - the in-stock filter, the sold-out badge, the low-stock
    report - reads products.stock, so keeping it as the sum means none of them
    had to learn about variants.
    """
    has_variants = db.execute(
        "SELECT 1 FROM product_variants WHERE product_id = ? LIMIT 1", (product_id,)
    ).fetchone()
    if has_variants is None:
        return
    db.execute(
        "UPDATE products SET stock = COALESCE("
        "  (SELECT SUM(stock) FROM product_variants"
        "   WHERE product_id = ? AND is_active = 1), 0)"
        " WHERE id = ?",
        (product_id, product_id),
    )


def unit_price(product, variant) -> int:
    """A variant only overrides the price when it was actually given one."""
    if variant is not None and variant["price_cents"] is not None:
        return variant["price_cents"]
    return product["price_cents"]


def price_range(product_id: int, product_price: int) -> tuple[int, int]:
    row = get_db().execute(
        "SELECT MIN(COALESCE(price_cents, ?)) AS low,"
        "       MAX(COALESCE(price_cents, ?)) AS high"
        " FROM product_variants WHERE product_id = ? AND is_active = 1",
        (product_price, product_price, product_id),
    ).fetchone()
    if row is None or row["low"] is None:
        return product_price, product_price
    return row["low"], row["high"]
