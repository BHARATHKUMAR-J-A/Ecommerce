"""Shopping cart. Stored in the signed session so guests can shop before signing in."""

from __future__ import annotations

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

from .db import get_db
from .security import safe_redirect_target

bp = Blueprint("cart", __name__, url_prefix="/cart")


def _raw_cart() -> dict[str, int]:
    cart = session.get("cart")
    return dict(cart) if isinstance(cart, dict) else {}


def _save(cart: dict[str, int]) -> None:
    session["cart"] = cart
    session.modified = True


def clear_cart() -> None:
    session.pop("cart", None)
    session.modified = True


def cart_summary() -> dict:
    """Resolve the session cart against live product rows and price the order."""
    cart = _raw_cart()
    if not cart:
        return _empty_summary()

    ids = [int(k) for k in cart if str(k).isdigit()]
    if not ids:
        return _empty_summary()

    placeholders = ",".join("?" * len(ids))
    rows = get_db().execute(
        f"SELECT * FROM visible_products WHERE id IN ({placeholders})", ids
    ).fetchall()

    items, subtotal, count = [], 0, 0
    live = {str(row["id"]) for row in rows}
    for row in rows:
        quantity = min(int(cart[str(row["id"])]), row["stock"])
        if quantity <= 0:
            continue
        line = row["price_cents"] * quantity
        subtotal += line
        count += quantity
        items.append(
            {
                "product": row,
                "quantity": quantity,
                "line_cents": line,
                "capped": quantity < int(cart[str(row["id"])]),
            }
        )

    stale = set(cart) - live
    if stale:
        _save({k: v for k, v in cart.items() if k not in stale})

    cfg = current_app.config
    shipping = 0 if subtotal >= cfg["FREE_SHIPPING_THRESHOLD_CENTS"] or subtotal == 0 else cfg["SHIPPING_FLAT_CENTS"]
    tax = round(subtotal * cfg["TAX_RATE"])
    items.sort(key=lambda item: item["product"]["name"])
    return {
        "items": items,
        "count": count,
        "subtotal_cents": subtotal,
        "shipping_cents": shipping,
        "tax_cents": tax,
        "total_cents": subtotal + shipping + tax,
        "free_shipping_gap": max(0, cfg["FREE_SHIPPING_THRESHOLD_CENTS"] - subtotal),
    }


def _empty_summary() -> dict:
    return {
        "items": [],
        "count": 0,
        "subtotal_cents": 0,
        "shipping_cents": 0,
        "tax_cents": 0,
        "total_cents": 0,
        "free_shipping_gap": current_app.config["FREE_SHIPPING_THRESHOLD_CENTS"],
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
    product = get_db().execute(
        "SELECT id, name, stock FROM visible_products WHERE id = ?", (product_id,)
    ).fetchone()
    if product is None:
        flash("That product is no longer available.", "error")
        return redirect(url_for("catalog.index"))

    if product["stock"] <= 0:
        flash(f"{product['name']} is out of stock.", "error")
        return redirect(safe_redirect_target(request.form.get("next")))

    cart = _raw_cart()
    key = str(product_id)
    wanted = cart.get(key, 0) + max(1, _quantity_from_form())
    quantity = min(wanted, product["stock"], current_app.config["MAX_QTY_PER_LINE"])
    cart[key] = quantity
    _save(cart)

    if quantity < wanted:
        flash(f"Only {quantity} x {product['name']} could be added.", "info")
    else:
        flash(f"Added {product['name']} to your cart.", "success")
    return redirect(safe_redirect_target(request.form.get("next"), "cart.view"))


@bp.post("/update/<int:product_id>")
def update(product_id: int):
    cart = _raw_cart()
    key = str(product_id)
    if key not in cart:
        return redirect(url_for("cart.view"))

    quantity = _quantity_from_form()
    if quantity == 0:
        cart.pop(key)
        flash("Item removed.", "info")
    else:
        stock = get_db().execute(
            "SELECT stock FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        cart[key] = min(quantity, stock["stock"] if stock else 0)
    _save(cart)
    return redirect(url_for("cart.view"))


@bp.post("/remove/<int:product_id>")
def remove(product_id: int):
    cart = _raw_cart()
    if cart.pop(str(product_id), None) is not None:
        _save(cart)
        flash("Item removed.", "info")
    return redirect(url_for("cart.view"))


@bp.post("/clear")
def clear():
    clear_cart()
    flash("Cart emptied.", "info")
    return redirect(url_for("cart.view"))
