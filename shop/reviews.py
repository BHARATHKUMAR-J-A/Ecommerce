"""Customer reviews. These are the source of truth for a product's star rating."""

from __future__ import annotations

import sqlite3

from flask import Blueprint, abort, flash, g, redirect, request, url_for

from .db import get_db
from .security import login_required

bp = Blueprint("reviews", __name__, url_prefix="/reviews")

MAX_TITLE = 100
MAX_BODY = 2000


def refresh_product_rating(db: sqlite3.Connection, product_id: int) -> None:
    """products.rating/review_count are a cache; recompute them from reviews."""
    db.execute(
        "UPDATE products SET"
        "  rating = COALESCE((SELECT ROUND(AVG(rating), 2) FROM reviews"
        "                     WHERE product_id = ?), 0),"
        "  review_count = (SELECT COUNT(*) FROM reviews WHERE product_id = ?)"
        " WHERE id = ?",
        (product_id, product_id, product_id),
    )


def reviews_for(product_id: int) -> list:
    return get_db().execute(
        "SELECT r.*, u.name AS author,"
        "  EXISTS (SELECT 1 FROM order_items oi JOIN orders o ON o.id = oi.order_id"
        "          WHERE oi.product_id = r.product_id AND o.user_id = r.user_id"
        "            AND o.status != 'cancelled') AS verified"
        " FROM reviews r JOIN users u ON u.id = r.user_id"
        " WHERE r.product_id = ? ORDER BY verified DESC, r.id DESC",
        (product_id,),
    ).fetchall()


def own_review(product_id: int):
    if g.user is None:
        return None
    return get_db().execute(
        "SELECT * FROM reviews WHERE product_id = ? AND user_id = ?",
        (product_id, g.user["id"]),
    ).fetchone()


def rating_breakdown(reviews: list) -> list[tuple[int, int]]:
    counts = {star: 0 for star in range(5, 0, -1)}
    for review in reviews:
        counts[review["rating"]] = counts.get(review["rating"], 0) + 1
    return list(counts.items())


@bp.post("/<int:product_id>")
@login_required
def submit(product_id: int):
    db = get_db()
    product = db.execute(
        "SELECT id FROM visible_products WHERE id = ?", (product_id,)
    ).fetchone()
    if product is None:
        abort(404, description="That product is no longer available.")

    try:
        rating = int(request.form.get("rating", 0))
    except (TypeError, ValueError):
        rating = 0
    if not 1 <= rating <= 5:
        flash("Please choose a star rating between 1 and 5.", "error")
        return redirect(url_for("catalog.product", product_id=product_id))

    title = (request.form.get("title") or "").strip()[:MAX_TITLE]
    body = (request.form.get("body") or "").strip()[:MAX_BODY]

    # One review per person per product; posting again edits the existing one.
    db.execute(
        "INSERT INTO reviews (product_id, user_id, rating, title, body)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT (product_id, user_id) DO UPDATE SET"
        "   rating = excluded.rating, title = excluded.title, body = excluded.body,"
        "   created_at = datetime('now')",
        (product_id, g.user["id"], rating, title, body),
    )
    refresh_product_rating(db, product_id)
    db.commit()

    flash("Thanks for reviewing this product.", "success")
    return redirect(url_for("catalog.product", product_id=product_id) + "#reviews")


@bp.post("/<int:product_id>/delete")
@login_required
def delete(product_id: int):
    db = get_db()
    # Admins can remove any review; everyone else only their own.
    if g.user["is_admin"] and request.form.get("review_id"):
        db.execute(
            "DELETE FROM reviews WHERE id = ? AND product_id = ?",
            (request.form["review_id"], product_id),
        )
    else:
        db.execute(
            "DELETE FROM reviews WHERE product_id = ? AND user_id = ?",
            (product_id, g.user["id"]),
        )
    refresh_product_rating(db, product_id)
    db.commit()

    flash("Review removed.", "info")
    return redirect(url_for("catalog.product", product_id=product_id) + "#reviews")
