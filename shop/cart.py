"""Shopping cart. Stored in the signed session so guests can shop before signing in.

A cart is a list of lines rather than a map of product to quantity: the same
product can appear twice with a different variant or different personalisation,
and each of those is its own line.
"""

from __future__ import annotations

import secrets

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from . import settings
from .db import get_db
from .security import safe_redirect_target
from .variants import unit_price

bp = Blueprint("cart", __name__, url_prefix="/cart")


def _lines() -> list[dict]:
    cart = session.get("cart")
    if not isinstance(cart, list):
        return []
    return [line for line in cart if isinstance(line, dict) and "product_id" in line]


def _save(lines: list[dict]) -> None:
    session["cart"] = lines
    session.modified = True


def clear_cart() -> None:
    session.pop("cart", None)
    session.modified = True


def _clean_text(raw: str, limit: int) -> str:
    return "".join(c for c in (raw or "") if c.isprintable()).strip()[:limit]


def cart_summary() -> dict:
    """Resolve each line against live rows and price the order."""
    lines = _lines()
    if not lines:
        return _empty_summary()

    db = get_db()
    items, subtotal, count, kept = [], 0, 0, []

    for line in lines:
        product = db.execute(
            "SELECT * FROM visible_products WHERE id = ?", (line["product_id"],)
        ).fetchone()
        if product is None:
            continue

        variant = None
        if line.get("variant_id"):
            variant = db.execute(
                "SELECT * FROM product_variants"
                " WHERE id = ? AND product_id = ? AND is_active = 1",
                (line["variant_id"], product["id"]),
            ).fetchone()
            if variant is None:
                continue  # the seller retired that option
        elif db.execute(
            "SELECT 1 FROM product_variants WHERE product_id = ? AND is_active = 1 LIMIT 1",
            (product["id"],),
        ).fetchone():
            continue  # options were added after this went into the cart

        available = variant["stock"] if variant is not None else product["stock"]
        wanted = int(line.get("quantity", 1))
        quantity = max(0, min(wanted, available))
        if quantity <= 0:
            continue

        price = unit_price(product, variant)
        subtotal += price * quantity
        count += quantity
        kept.append({**line, "quantity": quantity})
        items.append(
            {
                "line_id": line["id"],
                "product": product,
                "variant": variant,
                "text": line.get("text", ""),
                "quantity": quantity,
                "unit_cents": price,
                "line_cents": price * quantity,
                "capped": quantity < wanted,
                "available": available,
            }
        )

    if kept != lines:
        _save(kept)

    threshold = settings.get("FREE_SHIPPING_THRESHOLD_CENTS")
    shipping = 0 if subtotal == 0 or subtotal >= threshold else settings.get("SHIPPING_FLAT_CENTS")
    tax = round(subtotal * settings.get("TAX_RATE"))
    items.sort(
        key=lambda i: (i["product"]["name"], i["variant"]["label"] if i["variant"] else "")
    )
    return {
        "items": items,
        "count": count,
        "subtotal_cents": subtotal,
        "shipping_cents": shipping,
        "tax_cents": tax,
        "total_cents": subtotal + shipping + tax,
        "free_shipping_gap": max(0, threshold - subtotal),
    }


def _empty_summary() -> dict:
    return {
        "items": [],
        "count": 0,
        "subtotal_cents": 0,
        "shipping_cents": 0,
        "tax_cents": 0,
        "total_cents": 0,
        "free_shipping_gap": settings.get("FREE_SHIPPING_THRESHOLD_CENTS"),
    }


def _quantity_from_form(field: str = "quantity", default: int = 1) -> int:
    try:
        value = int(request.form.get(field, default))
    except (TypeError, ValueError):
        value = default
    return max(0, min(current_app.config["MAX_QTY_PER_LINE"], value))


@bp.route("/")
def view():
    return render_template("cart.html", summary=cart_summary())


@bp.post("/add/<int:product_id>")
def add(product_id: int):
    db = get_db()
    product = db.execute(
        "SELECT * FROM visible_products WHERE id = ?", (product_id,)
    ).fetchone()
    if product is None:
        flash("That product is no longer available.", "error")
        return redirect(url_for("catalog.index"))

    back = safe_redirect_target(request.form.get("next"), "cart.view")
    options = db.execute(
        "SELECT * FROM product_variants WHERE product_id = ? AND is_active = 1"
        " ORDER BY position, id",
        (product_id,),
    ).fetchall()

    variant = None
    if options:
        try:
            chosen = int(request.form.get("variant_id", 0))
        except (TypeError, ValueError):
            chosen = 0
        variant = next((v for v in options if v["id"] == chosen), None)
        if variant is None:
            flash(f"Please choose {(product['option_label'] or 'an option').lower()}.", "error")
            return redirect(url_for("catalog.product", product_id=product_id))

    text = ""
    if product["personalisation_label"]:
        text = _clean_text(
            request.form.get("personalisation", ""), product["personalisation_max"]
        )
        if product["personalisation_required"] and not text:
            flash(f"{product['personalisation_label']} is required for this item.", "error")
            return redirect(url_for("catalog.product", product_id=product_id))

    available = variant["stock"] if variant is not None else product["stock"]
    if available <= 0:
        flash(f"{product['name']} is out of stock.", "error")
        return redirect(back)

    lines = _lines()
    asked = max(1, _quantity_from_form())
    match = next(
        (
            line
            for line in lines
            if line["product_id"] == product_id
            and line.get("variant_id") == (variant["id"] if variant else None)
            and line.get("text", "") == text
        ),
        None,
    )
    wanted = (match["quantity"] if match else 0) + asked
    quantity = min(wanted, available, current_app.config["MAX_QTY_PER_LINE"])

    if match:
        match["quantity"] = quantity
    else:
        lines.append(
            {
                "id": secrets.token_hex(4),
                "product_id": product_id,
                "variant_id": variant["id"] if variant else None,
                "quantity": quantity,
                "text": text,
            }
        )
    _save(lines)

    name = product["name"] + (f" ({variant['label']})" if variant else "")
    if quantity < wanted:
        flash(f"Only {quantity} x {name} could be added.", "info")
    else:
        flash(f"Added {name} to your cart.", "success")
    return redirect(back)


@bp.post("/update/<line_id>")
def update(line_id: str):
    lines = _lines()
    line = next((l for l in lines if l["id"] == line_id), None)
    if line is None:
        return redirect(url_for("cart.view"))

    quantity = _quantity_from_form()
    if quantity == 0:
        lines = [l for l in lines if l["id"] != line_id]
        flash("Item removed.", "info")
    else:
        line["quantity"] = quantity
    _save(lines)
    return redirect(url_for("cart.view"))


@bp.post("/remove/<line_id>")
def remove(line_id: str):
    lines = _lines()
    remaining = [l for l in lines if l["id"] != line_id]
    if len(remaining) != len(lines):
        _save(remaining)
        flash("Item removed.", "info")
    return redirect(url_for("cart.view"))


@bp.post("/clear")
def clear():
    clear_cart()
    flash("Cart emptied.", "info")
    return redirect(url_for("cart.view"))
