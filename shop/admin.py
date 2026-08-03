"""Staff area: dashboard, product management, order queue and seller approvals."""

from __future__ import annotations

import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import settings
from .db import get_db
from .orders import STATUS_TRANSITIONS, advance_status
from .products import read_product_fields
from .security import admin_required

bp = Blueprint("admin", __name__, url_prefix="/admin")

SELLER_ACTIONS = {
    "approve": "approved",
    "reject": "rejected",
    "suspend": "suspended",
    "reinstate": "approved",
}


@bp.route("/")
@admin_required
def dashboard():
    db = get_db()
    stats = db.execute(
        "SELECT (SELECT COUNT(*) FROM products WHERE is_active = 1) AS products,"
        " (SELECT COUNT(*) FROM products WHERE stock = 0 AND is_active = 1) AS sold_out,"
        " (SELECT COUNT(*) FROM users) AS customers,"
        " (SELECT COUNT(*) FROM orders) AS orders,"
        " (SELECT COUNT(*) FROM sellers WHERE status = 'approved') AS sellers,"
        " (SELECT COUNT(*) FROM sellers WHERE status = 'pending') AS pending_sellers,"
        " (SELECT COALESCE(SUM(total_cents), 0) FROM orders"
        "  WHERE status != 'cancelled') AS revenue,"
        " (SELECT COALESCE(SUM(i.commission_cents), 0) FROM order_items i"
        "  JOIN orders o ON o.id = i.order_id WHERE o.status != 'cancelled') AS commission"
    ).fetchone()
    recent = db.execute(
        "SELECT o.* FROM orders o ORDER BY o.id DESC LIMIT 8"
    ).fetchall()
    low_stock = db.execute(
        "SELECT * FROM products WHERE is_active = 1 ORDER BY stock ASC LIMIT 8"
    ).fetchall()
    return render_template(
        "admin_dashboard.html", stats=stats, recent=recent, low_stock=low_stock
    )


@bp.route("/products")
@admin_required
def products():
    rows = get_db().execute(
        "SELECT p.*, c.name AS category_name, s.shop_name, s.slug AS seller_slug"
        " FROM products p JOIN categories c ON c.id = p.category_id"
        " LEFT JOIN sellers s ON s.id = p.seller_id"
        " ORDER BY p.is_active DESC, p.name"
    ).fetchall()
    return render_template("admin_products.html", products=rows)


@bp.route("/products/new", methods=("GET", "POST"))
@admin_required
def create_product():
    categories = get_db().execute("SELECT id, name FROM categories ORDER BY name").fetchall()
    form = {"is_active": 1, "icon": "\U0001F4E6", "stock": 0}

    if request.method == "POST":
        form, errors = read_product_fields(require_sku=True)
        if not errors:
            try:
                db = get_db()
                db.execute(
                    "INSERT INTO products (sku, name, description, price_cents, stock,"
                    " category_id, icon, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        form["sku"], form["name"], form["description"], form["price_cents"],
                        form["stock"], form["category_id"], form["icon"], form["is_active"],
                    ),
                )
                db.commit()
            except sqlite3.IntegrityError:
                errors.append("That SKU is already in use.")
            else:
                flash(f"Added {form['name']}.", "success")
                return redirect(url_for("admin.products"))
        for message in errors:
            flash(message, "error")

    return render_template(
        "admin_product_form.html", form=form, categories=categories, product=None
    )


@bp.route("/products/<int:product_id>/edit", methods=("GET", "POST"))
@admin_required
def edit_product(product_id: int):
    db = get_db()
    # Seller listings belong to their seller; staff can hide one but not rewrite it.
    product = db.execute(
        "SELECT * FROM products WHERE id = ? AND seller_id IS NULL", (product_id,)
    ).fetchone()
    if product is None:
        abort(404, description="No such own-brand product.")

    categories = db.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
    form = dict(product)
    form["price"] = f"{product['price_cents'] / 100:.2f}"

    if request.method == "POST":
        form, errors = read_product_fields(require_sku=True)
        if not errors:
            try:
                db.execute(
                    "UPDATE products SET sku = ?, name = ?, description = ?, price_cents = ?,"
                    " stock = ?, category_id = ?, icon = ?, is_active = ?"
                    " WHERE id = ? AND seller_id IS NULL",
                    (
                        form["sku"], form["name"], form["description"], form["price_cents"],
                        form["stock"], form["category_id"], form["icon"],
                        form["is_active"], product_id,
                    ),
                )
                db.commit()
            except sqlite3.IntegrityError:
                errors.append("That SKU is already in use.")
            else:
                flash(f"Updated {form['name']}.", "success")
                return redirect(url_for("admin.products"))
        for message in errors:
            flash(message, "error")

    return render_template(
        "admin_product_form.html", form=form, categories=categories, product=product
    )


@bp.post("/products/<int:product_id>/toggle")
@admin_required
def toggle_product(product_id: int):
    db = get_db()
    # Soft delete only - hard deletes would orphan historical order lines.
    updated = db.execute(
        "UPDATE products SET is_active = 1 - is_active WHERE id = ?", (product_id,)
    )
    db.commit()
    if updated.rowcount:
        flash("Product visibility updated.", "success")
    return redirect(url_for("admin.products"))


@bp.route("/orders")
@admin_required
def orders():
    rows = get_db().execute(
        "SELECT o.*, COALESCE(SUM(i.quantity), 0) AS units,"
        " COALESCE(SUM(i.commission_cents), 0) AS commission"
        " FROM orders o LEFT JOIN order_items i ON i.order_id = o.id"
        " GROUP BY o.id ORDER BY o.id DESC"
    ).fetchall()
    return render_template("admin_orders.html", orders=rows, transitions=STATUS_TRANSITIONS)


@bp.route("/settings", methods=("GET", "POST"))
@admin_required
def store_settings():
    if request.method == "POST":
        values = {name: request.form.get(name, "") for name in settings.EDITABLE}
        # Unticked checkboxes are simply absent from a form post, so make the
        # boolean explicit rather than letting "missing" mean "unchanged".
        values["ALLOW_GUEST_CHECKOUT"] = "1" if request.form.get("ALLOW_GUEST_CHECKOUT") else ""
        errors = settings.save(values)
        for message in errors:
            flash(message, "error")
        if not errors:
            flash("Store settings saved.", "success")
            return redirect(url_for("admin.store_settings"))

    return render_template("admin_settings.html", current=settings.get)


@bp.post("/orders/<int:order_id>/status")
@admin_required
def update_order_status(order_id: int):
    error = advance_status(order_id, request.form.get("status", ""))
    if error:
        flash(error, "error")
    else:
        flash("Order updated.", "success")
    return redirect(url_for("admin.orders"))


@bp.route("/sellers")
@admin_required
def sellers():
    rows = get_db().execute(
        "SELECT s.*, u.email AS account_email,"
        " (SELECT COUNT(*) FROM products WHERE seller_id = s.id) AS listings,"
        " (SELECT COALESCE(SUM(i.line_cents), 0) FROM order_items i"
        "  JOIN orders o ON o.id = i.order_id"
        "  WHERE i.seller_id = s.id AND o.status != 'cancelled') AS gross,"
        " (SELECT COALESCE(SUM(i.commission_cents), 0) FROM order_items i"
        "  JOIN orders o ON o.id = i.order_id"
        "  WHERE i.seller_id = s.id AND o.status != 'cancelled') AS commission,"
        " (SELECT COALESCE(SUM(i.seller_earning_cents), 0) FROM order_items i"
        "  JOIN orders o ON o.id = i.order_id"
        "  WHERE i.seller_id = s.id AND o.status != 'cancelled') AS owed"
        " FROM sellers s JOIN users u ON u.id = s.user_id"
        " ORDER BY CASE s.status WHEN 'pending' THEN 0 ELSE 1 END, s.shop_name"
    ).fetchall()
    return render_template("admin_sellers.html", sellers=rows, actions=SELLER_ACTIONS)


@bp.post("/sellers/<int:seller_id>/<action>")
@admin_required
def update_seller(seller_id: int, action: str):
    if action not in SELLER_ACTIONS:
        abort(404)

    db = get_db()
    updated = db.execute(
        "UPDATE sellers SET status = ?, reviewed_at = datetime('now') WHERE id = ?",
        (SELLER_ACTIONS[action], seller_id),
    )
    db.commit()
    if updated.rowcount:
        flash(f"Seller {SELLER_ACTIONS[action]}.", "success")
    return redirect(url_for("admin.sellers"))


@bp.post("/sellers/<int:seller_id>/rate")
@admin_required
def update_commission(seller_id: int):
    try:
        percent = float(request.form.get("commission_percent", ""))
    except ValueError:
        percent = -1.0
    if not 0 <= percent <= 100:
        flash("Commission must be between 0 and 100 percent.", "error")
        return redirect(url_for("admin.sellers"))

    db = get_db()
    # Only future orders are affected; past splits are frozen on the order line.
    db.execute(
        "UPDATE sellers SET commission_rate = ? WHERE id = ?", (percent / 100, seller_id)
    )
    db.commit()
    flash(f"Commission set to {percent:g}% for future sales.", "success")
    return redirect(url_for("admin.sellers"))
