"""Staff area: dashboard, product management, order queue and seller approvals."""

from __future__ import annotations

import sqlite3

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from . import exports, reports, settings
from .catalog import page_window
from .db import get_db
from .mail import application_decision, send_email
from .orders import STATUS_TRANSITIONS, advance_status
from .products import read_product_fields
from .security import admin_required

bp = Blueprint("admin", __name__, url_prefix="/admin")

ORDERS_PER_PAGE = 25

SELLER_ACTIONS = {
    "approve": "approved",
    "reject": "rejected",
    "suspend": "suspended",
    "reinstate": "approved",
}


def _like(term: str) -> str:
    """A LIKE pattern where the user's own % and _ are literal, not wildcards."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _csv_response(filename: str, header, rows) -> Response:
    # The BOM is what makes Excel read it as UTF-8 rather than the local codepage.
    body = "\ufeff" + exports.to_csv(header, rows)
    return Response(
        body,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
        "  WHERE status != 'cancelled') AS customers_paid"
    ).fetchone()
    recent = db.execute(
        "SELECT o.* FROM orders o ORDER BY o.id DESC LIMIT 8"
    ).fetchall()
    low_stock = db.execute(
        "SELECT * FROM products WHERE is_active = 1 ORDER BY stock ASC LIMIT 8"
    ).fetchall()
    return render_template(
        "admin_dashboard.html",
        stats=stats,
        money=reports.totals("all"),
        recent=recent,
        low_stock=low_stock,
        top_items=reports.items("all", limit=5),
    )


@bp.route("/reports")
@admin_required
def sales_report():
    period = request.args.get("period", reports.DEFAULT_PERIOD)
    if period not in reports.PERIODS:
        period = reports.DEFAULT_PERIOD
    sort = request.args.get("sort", reports.DEFAULT_ITEM_SORT)
    if sort not in reports.ITEM_SORTS:
        sort = reports.DEFAULT_ITEM_SORT

    return render_template(
        "admin_reports.html",
        period=period,
        sort=sort,
        periods=reports.PERIODS,
        sorts=reports.ITEM_SORTS,
        totals=reports.totals(period),
        items=reports.items(period, sort),
        sellers=reports.sellers(period),
        daily=reports.daily(period),
    )


@bp.route("/reports.csv")
@admin_required
def sales_report_csv():
    period = request.args.get("period", reports.DEFAULT_PERIOD)
    if period not in reports.PERIODS:
        period = reports.DEFAULT_PERIOD

    rows = [
        [
            item["name"],
            item["variant_label"],
            item["shop_name"] or "Own stock",
            item["units"],
            item["orders"],
            exports.money(item["gross"]),
            exports.money(item["seller_earnings"]),
            exports.money(item["platform_revenue"]),
            item["last_sold"],
        ]
        for item in reports.items(period, "revenue", limit=100000)
    ]
    return _csv_response(
        f"shopsphere-sales-{period}.csv",
        ["Item", "Option", "Sold by", "Units", "Orders", "Goods sold",
         "Seller keeps", "You keep", "Last sold"],
        rows,
    )


@bp.route("/payouts.csv")
@admin_required
def payouts_csv():
    """What each seller is owed - the file you hand to whoever moves the money."""
    period = request.args.get("period", reports.DEFAULT_PERIOD)
    if period not in reports.PERIODS:
        period = reports.DEFAULT_PERIOD

    rows = [
        [
            seller["shop_name"],
            seller["contact_email"],
            seller["payout_reference"],
            seller["status"],
            f"{seller['commission_rate'] * 100:.1f}",
            seller["units"],
            exports.money(seller["gross"]),
            exports.money(seller["commission"]),
            exports.money(seller["owed"]),
        ]
        for seller in reports.sellers(period)
    ]
    return _csv_response(
        f"shopsphere-payouts-{period}.csv",
        ["Shop", "Contact email", "Payout reference", "Status", "Commission %",
         "Units", "Goods sold", "Commission", "Owed"],
        rows,
    )


@bp.route("/sellers/<int:seller_id>")
@admin_required
def seller_detail(seller_id: int):
    db = get_db()
    seller = db.execute(
        "SELECT s.*, u.email AS account_email, u.name AS account_name"
        " FROM sellers s JOIN users u ON u.id = s.user_id WHERE s.id = ?",
        (seller_id,),
    ).fetchone()
    if seller is None:
        abort(404, description="No such seller.")

    period = request.args.get("period", "all")
    if period not in reports.PERIODS:
        period = "all"

    listings = db.execute(
        "SELECT p.*, c.name AS category_name,"
        " (SELECT COALESCE(SUM(i.quantity), 0) FROM order_items i"
        "  JOIN orders o ON o.id = i.order_id"
        "  WHERE i.product_id = p.id AND o.status != 'cancelled') AS sold"
        " FROM products p JOIN categories c ON c.id = p.category_id"
        " WHERE p.seller_id = ? ORDER BY sold DESC, p.name",
        (seller_id,),
    ).fetchall()

    return render_template(
        "admin_seller_detail.html",
        seller=seller,
        period=period,
        periods=reports.PERIODS,
        totals=reports.totals(period, seller_id=seller_id),
        items=reports.items(period, seller_id=seller_id),
        listings=listings,
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
    status = request.args.get("status", "")
    if status not in STATUS_TRANSITIONS:
        status = ""
    search = request.args.get("q", "").strip()[:80]

    where, params = [], []
    if status:
        where.append("o.status = ?")
        params.append(status)
    if search:
        where.append(
            "(o.reference LIKE ? ESCAPE '\\' OR o.email LIKE ? ESCAPE '\\'"
            " OR o.ship_name LIKE ? ESCAPE '\\')"
        )
        params += [_like(search)] * 3
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(f"SELECT COUNT(*) AS n FROM orders o{clause}", params).fetchone()["n"]
    pages = max(1, -(-total // ORDERS_PER_PAGE))
    page = max(1, min(pages, request.args.get("page", 1, type=int) or 1))

    rows = db.execute(
        "SELECT o.*, COALESCE(SUM(i.quantity), 0) AS units,"
        " COALESCE(SUM(i.commission_cents), 0) AS commission"
        " FROM orders o LEFT JOIN order_items i ON i.order_id = o.id"
        f"{clause} GROUP BY o.id ORDER BY o.id DESC LIMIT ? OFFSET ?",
        params + [ORDERS_PER_PAGE, (page - 1) * ORDERS_PER_PAGE],
    ).fetchall()

    counts = {
        row["status"]: row["n"]
        for row in db.execute("SELECT status, COUNT(*) AS n FROM orders GROUP BY status")
    }
    return render_template(
        "admin_orders.html",
        orders=rows,
        transitions=STATUS_TRANSITIONS,
        counts=counts,
        total_orders=sum(counts.values()),
        status=status,
        search=search,
        page=page,
        pages=pages,
        matched=total,
        window=page_window(page, pages, 2),
    )


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

    status = SELLER_ACTIONS[action]
    db = get_db()
    seller = db.execute(
        "SELECT s.shop_name, s.contact_email, s.status, u.name"
        " FROM sellers s JOIN users u ON u.id = s.user_id WHERE s.id = ?",
        (seller_id,),
    ).fetchone()
    updated = db.execute(
        "UPDATE sellers SET status = ?, reviewed_at = datetime('now') WHERE id = ?",
        (status, seller_id),
    )
    db.commit()
    if updated.rowcount:
        flash(f"Seller {status}.", "success")
        # Reinstating is an "approved" too, but the applicant already knows.
        if seller and seller["contact_email"] and seller["status"] != status:
            send_email(
                seller["contact_email"],
                f"Your ShopSphere shop - {status}",
                application_decision(
                    seller["name"],
                    seller["shop_name"],
                    status,
                    url_for("sellers.dashboard", _external=True),
                ),
            )
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
