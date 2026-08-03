"""Checkout and order history."""

from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import datetime, timezone

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from . import settings
from .cart import cart_summary, clear_cart
from .db import get_db
from .mail import order_confirmation, seller_sale, send_email, shipping_update
from .security import get_throttle, login_required
from .variants import refresh_product_stock

bp = Blueprint("orders", __name__, url_prefix="/orders")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

ADDRESS_FIELDS = {
    "ship_name": ("Full name", 80),
    "ship_address": ("Address", 200),
    "ship_city": ("City", 80),
    "ship_postcode": ("Postcode", 20),
    "ship_country": ("Country", 60),
}

# Fulfilment states an order may move to next. Terminal states map to nothing.
STATUS_TRANSITIONS = {
    "paid": ("packed", "cancelled"),
    "packed": ("shipped", "cancelled"),
    "shipped": ("delivered",),
    "delivered": (),
    "cancelled": (),
}

# "packed" is warehouse bookkeeping, not news. Emailing every internal step is
# how a shop teaches its customers to ignore its email.
NOTIFY_STATUSES = ("shipped", "delivered", "cancelled")


def advance_status(order_id: int, target: str) -> str | None:
    """Move an order to `target`, restocking if it is being cancelled.

    Returns an error message, or None on success.
    """
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        return "That order no longer exists."
    if target not in STATUS_TRANSITIONS.get(order["status"], ()):
        return f"An order that is {order['status']} cannot become {target}."

    try:
        if target == "cancelled":
            items = db.execute(
                "SELECT product_id, variant_id, quantity FROM order_items"
                " WHERE order_id = ? AND product_id IS NOT NULL",
                (order_id,),
            ).fetchall()
            for item in items:
                if item["variant_id"] is not None:
                    db.execute(
                        "UPDATE product_variants SET stock = stock + ? WHERE id = ?",
                        (item["quantity"], item["variant_id"]),
                    )
                    refresh_product_stock(db, item["product_id"])
                else:
                    db.execute(
                        "UPDATE products SET stock = stock + ? WHERE id = ?",
                        (item["quantity"], item["product_id"]),
                    )
        db.execute("UPDATE orders SET status = ? WHERE id = ?", (target, order_id))
        db.commit()
    except sqlite3.Error:
        db.rollback()
        return "We could not update that order. Please try again."

    # After the commit: a bounced email must not undo a fulfilment step.
    if target in NOTIFY_STATUSES and order["email"]:
        send_email(
            order["email"],
            f"Your ShopSphere order {order['reference']} - {target}",
            shipping_update(order, target),
        )
    return None


def _new_reference() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"SS-{stamp}-{secrets.token_hex(3).upper()}"


def _commission_for(seller_id: int | None, line_cents: int) -> tuple[float, int]:
    """The platform's cut of one line. Own stock has no commission - it is all ours."""
    if seller_id is None:
        return 0.0, 0
    seller = get_db().execute(
        "SELECT commission_rate FROM sellers WHERE id = ?", (seller_id,)
    ).fetchone()
    rate = seller["commission_rate"] if seller else settings.get("COMMISSION_RATE")
    return rate, min(line_cents, round(line_cents * rate))


def _read_address() -> tuple[dict, list[str]]:
    values, errors = {}, []
    for field, (label, max_len) in ADDRESS_FIELDS.items():
        value = (request.form.get(field) or "").strip()
        if not value:
            errors.append(f"{label} is required.")
        elif len(value) > max_len:
            errors.append(f"{label} must be {max_len} characters or fewer.")
        values[field] = value[:max_len]
    return values, errors


def _read_email() -> tuple[str, list[str]]:
    email = (request.form.get("email") or "").strip().lower()[:120]
    if not EMAIL_RE.match(email):
        return email, ["Please enter a valid email address."]
    return email, []


@bp.route("/checkout", methods=("GET", "POST"))
def checkout():
    guests_allowed = settings.get("ALLOW_GUEST_CHECKOUT")
    if g.user is None and not guests_allowed:
        flash("Please sign in to check out.", "info")
        return redirect(url_for("auth.login", next=url_for("orders.checkout")))

    summary = cart_summary()
    if not summary["items"]:
        flash("Your cart is empty.", "info")
        return redirect(url_for("catalog.index"))

    form = {field: "" for field in ADDRESS_FIELDS}
    form["email"] = ""
    if g.user is not None:
        form["ship_name"] = g.user["name"]
        form["email"] = g.user["email"]

    if request.method == "POST":
        form, errors = _read_address()
        if g.user is not None:
            email = g.user["email"]
        else:
            email, email_errors = _read_email()
            errors = email_errors + errors
        form["email"] = email

        if errors:
            for message in errors:
                flash(message, "error")
        else:
            reference = _place_order(summary, form, email)
            if reference:
                clear_cart()
                if g.user is None:
                    # Lets the guest reach the confirmation without the email wall.
                    session["guest_orders"] = (session.get("guest_orders") or [])[-9:] + [
                        reference
                    ]
                return redirect(url_for("orders.detail", reference=reference, placed=1))

    return render_template("checkout.html", summary=summary, form=form)


def _place_order(summary: dict, address: dict, email: str) -> str | None:
    """Decrement stock and write the order atomically; abort if anything sold out."""
    db = get_db()
    try:
        cursor = db.execute(
            "INSERT INTO orders (reference, user_id, email, ship_name, ship_address, ship_city,"
            " ship_postcode, ship_country, subtotal_cents, shipping_cents, tax_cents, total_cents)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _new_reference(),
                g.user["id"] if g.user is not None else None,
                email,
                address["ship_name"],
                address["ship_address"],
                address["ship_city"],
                address["ship_postcode"],
                address["ship_country"],
                summary["subtotal_cents"],
                summary["shipping_cents"],
                summary["tax_cents"],
                summary["total_cents"],
            ),
        )
        order_id = int(cursor.lastrowid)

        for item in summary["items"]:
            product, variant, quantity = item["product"], item["variant"], item["quantity"]
            name = product["name"]

            # Guarded update: fails rather than overselling if stock moved underneath us.
            if variant is not None:
                updated = db.execute(
                    "UPDATE product_variants SET stock = stock - ?"
                    " WHERE id = ? AND stock >= ?",
                    (quantity, variant["id"], quantity),
                )
                if updated.rowcount != 1:
                    raise ValueError(f"{name} ({variant['label']})")
                refresh_product_stock(db, product["id"])
            else:
                updated = db.execute(
                    "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                    (quantity, product["id"], quantity),
                )
                if updated.rowcount != 1:
                    raise ValueError(name)

            # Freeze the split now; a later rate change must not rewrite history.
            rate, commission = _commission_for(product["seller_id"], item["line_cents"])
            db.execute(
                "INSERT INTO order_items"
                " (order_id, product_id, variant_id, seller_id, name, variant_label,"
                "  personalisation, icon, unit_cents, quantity, line_cents,"
                "  commission_rate, commission_cents, seller_earning_cents)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    order_id,
                    product["id"],
                    variant["id"] if variant else None,
                    product["seller_id"],
                    name,
                    variant["label"] if variant else "",
                    item["text"],
                    product["icon"],
                    item["unit_cents"],
                    quantity,
                    item["line_cents"],
                    rate,
                    commission,
                    item["line_cents"] - commission if product["seller_id"] else 0,
                ),
            )
        db.commit()
    except ValueError as exc:
        db.rollback()
        flash(f"Sorry - {exc} sold out while you were checking out.", "error")
        return None
    except sqlite3.Error:
        db.rollback()
        flash("We could not process that order. Please try again.", "error")
        return None

    order = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    items = db.execute(
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,)
    ).fetchall()
    send_email(
        email, f"Your ShopSphere order {order['reference']}", order_confirmation(order, items)
    )
    _notify_sellers(db, order, items)
    return order["reference"]


def _notify_sellers(db, order, items) -> None:
    """One email per seller, containing only that seller's lines.

    Own-brand stock notifies nobody, and a seller must never learn what else was
    in a customer's basket.
    """
    seller_ids = sorted({i["seller_id"] for i in items if i["seller_id"] is not None})
    for seller_id in seller_ids:
        seller = db.execute(
            "SELECT shop_name, contact_email FROM sellers WHERE id = ?", (seller_id,)
        ).fetchone()
        if seller is None or not seller["contact_email"]:
            continue
        lines = [i for i in items if i["seller_id"] == seller_id]
        send_email(
            seller["contact_email"],
            f"You made a sale - order {order['reference']}",
            seller_sale(
                seller["shop_name"],
                order,
                lines,
                url_for("sellers.sales", _external=True),
            ),
        )


@bp.route("/")
@login_required
def history():
    rows = get_db().execute(
        "SELECT o.*, COUNT(i.id) AS line_count, COALESCE(SUM(i.quantity), 0) AS units"
        " FROM orders o LEFT JOIN order_items i ON i.order_id = o.id"
        " WHERE o.user_id = ? GROUP BY o.id ORDER BY o.id DESC",
        (g.user["id"],),
    ).fetchall()
    return render_template("orders.html", orders=rows)


def _may_view(order) -> bool:
    """A signed-in owner, or a guest holding the reference from this session."""
    if g.user is not None and order["user_id"] == g.user["id"]:
        return True
    return order["reference"] in (session.get("guest_orders") or [])


@bp.route("/lookup", methods=("GET", "POST"))
def lookup():
    """Guest order tracking. Reference alone is not enough - the email must match."""
    reference = (request.values.get("reference") or "").strip().upper()[:32]

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()[:120]
        throttle = get_throttle(
            "lookup",
            current_app.config["LOOKUP_MAX_ATTEMPTS"],
            current_app.config["LOGIN_WINDOW_SECONDS"],
        )
        key = f"ip:{request.remote_addr}"

        if throttle.retry_after(key):
            flash("Too many lookups. Please wait a few minutes.", "error")
            return render_template("order_lookup.html", reference=reference), 429

        order = get_db().execute(
            "SELECT reference FROM orders WHERE reference = ? AND email = ?",
            (reference, email),
        ).fetchone()
        if order is None:
            throttle.record_failure(key)
            flash("No order matches that reference and email address.", "error")
        else:
            session["guest_orders"] = (session.get("guest_orders") or [])[-9:] + [
                order["reference"]
            ]
            return redirect(url_for("orders.detail", reference=order["reference"]))

    return render_template("order_lookup.html", reference=reference)


@bp.route("/<reference>")
def detail(reference: str):
    db = get_db()
    order = db.execute(
        "SELECT * FROM orders WHERE reference = ?", (reference,)
    ).fetchone()
    # 404 rather than 403 so a wrong guess cannot confirm the reference exists.
    if order is None or not _may_view(order):
        if g.user is None:
            flash("Please confirm the email address used for that order.", "info")
            return redirect(url_for("orders.lookup", reference=reference))
        abort(404, description="We could not find that order.")

    items = db.execute(
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order["id"],)
    ).fetchall()
    return render_template(
        "order_detail.html", order=order, items=items, placed=request.args.get("placed")
    )
