"""Marketplace sellers: applications, listings, sales and earnings."""

from __future__ import annotations

import functools
import re
import secrets
import sqlite3

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from .db import get_db
from .products import read_product_fields
from .security import login_required
from .uploads import delete_product_image, is_stored_name, save_product_image, uploads_dir

bp = Blueprint("sellers", __name__)

MAX_IMAGES_PER_PRODUCT = 5
SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    return SLUG_STRIP.sub("-", value.lower()).strip("-")[:60] or "shop"


def current_seller():
    if g.user is None:
        return None
    return get_db().execute(
        "SELECT * FROM sellers WHERE user_id = ?", (g.user["id"],)
    ).fetchone()


def seller_required(view):
    """Only an approved seller may manage listings."""

    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        seller = current_seller()
        if seller is None:
            return redirect(url_for("sellers.apply"))
        if seller["status"] != "approved":
            return render_template("seller_status.html", seller=seller), 403
        g.seller = seller
        return view(*args, **kwargs)

    return wrapped


def owned_product(product_id: int):
    """Fetch a product only if it belongs to the signed-in seller."""
    product = get_db().execute(
        "SELECT * FROM products WHERE id = ? AND seller_id = ?",
        (product_id, g.seller["id"]),
    ).fetchone()
    if product is None:
        abort(404, description="No such product in your shop.")
    return product


# ---------------------------------------------------------------- public pages


@bp.route("/media/products/<filename>")
def media(filename: str):
    # Only names this app generated are servable, so ../ can never be reached.
    if not is_stored_name(filename):
        abort(404)
    response = send_from_directory(uploads_dir(), filename, mimetype="image/webp")
    response.headers["Cache-Control"] = "public, max-age=604800"
    return response


@bp.route("/s/<slug>")
def storefront(slug: str):
    db = get_db()
    seller = db.execute(
        "SELECT * FROM sellers WHERE slug = ? AND status = 'approved'", (slug,)
    ).fetchone()
    if seller is None:
        abort(404, description="No such shop.")

    products = db.execute(
        "SELECT p.*, c.name AS category_name, c.slug AS category_slug,"
        " (SELECT filename FROM product_images WHERE product_id = p.id"
        "  ORDER BY position, id LIMIT 1) AS image"
        " FROM visible_products p JOIN categories c ON c.id = p.category_id"
        " WHERE p.seller_id = ? ORDER BY p.rating DESC, p.id",
        (seller["id"],),
    ).fetchall()
    return render_template("seller_storefront.html", seller=seller, products=products)


@bp.route("/sell", methods=("GET", "POST"))
@login_required
def apply():
    existing = current_seller()
    if existing is not None:
        if existing["status"] == "approved":
            return redirect(url_for("sellers.dashboard"))
        return render_template("seller_status.html", seller=existing)

    form = {"shop_name": "", "bio": "", "contact_email": g.user["email"], "payout_reference": ""}
    if request.method == "POST":
        form = {
            "shop_name": (request.form.get("shop_name") or "").strip()[:80],
            "bio": (request.form.get("bio") or "").strip()[:1000],
            "contact_email": (request.form.get("contact_email") or "").strip().lower()[:120],
            "payout_reference": (request.form.get("payout_reference") or "").strip()[:80],
        }
        errors = []
        if not 3 <= len(form["shop_name"]) <= 80:
            errors.append("Shop name must be 3-80 characters.")
        if "@" not in form["contact_email"]:
            errors.append("Please give a contact email address.")

        if not errors:
            db = get_db()
            slug = slugify(form["shop_name"])
            if db.execute("SELECT 1 FROM sellers WHERE slug = ?", (slug,)).fetchone():
                slug = f"{slug}-{secrets.token_hex(2)}"
            try:
                db.execute(
                    "INSERT INTO sellers (user_id, shop_name, slug, bio, contact_email,"
                    " payout_reference, commission_rate) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        g.user["id"], form["shop_name"], slug, form["bio"],
                        form["contact_email"], form["payout_reference"],
                        current_app.config["COMMISSION_RATE"],
                    ),
                )
                db.commit()
            except sqlite3.IntegrityError:
                errors.append("You have already applied.")
            else:
                flash("Application received. We will review it shortly.", "success")
                return redirect(url_for("sellers.apply"))

        for message in errors:
            flash(message, "error")

    return render_template("seller_apply.html", form=form,
                           rate=current_app.config["COMMISSION_RATE"])


# ------------------------------------------------------------------- dashboard


@bp.route("/sell/dashboard")
@seller_required
def dashboard():
    db = get_db()
    totals = db.execute(
        "SELECT COUNT(*) AS lines, COALESCE(SUM(i.quantity), 0) AS units,"
        "  COALESCE(SUM(i.line_cents), 0) AS gross,"
        "  COALESCE(SUM(i.commission_cents), 0) AS commission,"
        "  COALESCE(SUM(i.seller_earning_cents), 0) AS earnings"
        " FROM order_items i JOIN orders o ON o.id = i.order_id"
        " WHERE i.seller_id = ? AND o.status != 'cancelled'",
        (g.seller["id"],),
    ).fetchone()

    listings = db.execute(
        "SELECT COUNT(*) AS total,"
        "  COALESCE(SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END), 0) AS live,"
        "  COALESCE(SUM(CASE WHEN stock = 0 THEN 1 ELSE 0 END), 0) AS sold_out"
        " FROM products WHERE seller_id = ?",
        (g.seller["id"],),
    ).fetchone()

    recent = db.execute(
        "SELECT i.*, o.reference, o.created_at, o.status"
        " FROM order_items i JOIN orders o ON o.id = i.order_id"
        " WHERE i.seller_id = ? ORDER BY o.id DESC LIMIT 8",
        (g.seller["id"],),
    ).fetchall()

    return render_template(
        "seller_dashboard.html", seller=g.seller, totals=totals,
        listings=listings, recent=recent,
    )


@bp.route("/sell/sales")
@seller_required
def sales():
    rows = get_db().execute(
        "SELECT i.*, o.reference, o.created_at, o.status, o.ship_city, o.ship_country"
        " FROM order_items i JOIN orders o ON o.id = i.order_id"
        " WHERE i.seller_id = ? ORDER BY o.id DESC",
        (g.seller["id"],),
    ).fetchall()
    return render_template("seller_sales.html", seller=g.seller, rows=rows)


# -------------------------------------------------------------------- listings


@bp.route("/sell/products")
@seller_required
def products():
    rows = get_db().execute(
        "SELECT p.*, c.name AS category_name,"
        " (SELECT filename FROM product_images WHERE product_id = p.id"
        "  ORDER BY position, id LIMIT 1) AS image,"
        " (SELECT COALESCE(SUM(quantity), 0) FROM order_items i"
        "  JOIN orders o ON o.id = i.order_id"
        "  WHERE i.product_id = p.id AND o.status != 'cancelled') AS sold"
        " FROM products p JOIN categories c ON c.id = p.category_id"
        " WHERE p.seller_id = ? ORDER BY p.is_active DESC, p.name",
        (g.seller["id"],),
    ).fetchall()
    return render_template("seller_products.html", seller=g.seller, products=rows)


@bp.route("/sell/products/new", methods=("GET", "POST"))
@seller_required
def create_product():
    db = get_db()
    categories = db.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
    form = {"is_active": 1, "icon": "\U0001F4E6", "stock": 1}

    if request.method == "POST":
        form, errors = read_product_fields(require_sku=False)
        if not errors:
            sku = f"{g.seller['slug'][:8].upper()}-{secrets.token_hex(3).upper()}"
            cursor = db.execute(
                "INSERT INTO products (sku, name, description, price_cents, stock,"
                " category_id, seller_id, icon, is_active)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sku, form["name"], form["description"], form["price_cents"],
                    form["stock"], form["category_id"], g.seller["id"], form["icon"],
                    form["is_active"],
                ),
            )
            product_id = int(cursor.lastrowid)
            db.commit()

            saved, image_errors = _store_uploads(product_id, request.files.getlist("images"))
            for message in image_errors:
                flash(message, "error")
            flash(
                f"{form['name']} is listed"
                + (f" with {saved} photo{'' if saved == 1 else 's'}." if saved else "."),
                "success",
            )
            return redirect(url_for("sellers.edit_product", product_id=product_id))

        for message in errors:
            flash(message, "error")

    return render_template(
        "seller_product_form.html", form=form, categories=categories,
        product=None, images=[], seller=g.seller,
    )


@bp.route("/sell/products/<int:product_id>/edit", methods=("GET", "POST"))
@seller_required
def edit_product(product_id: int):
    db = get_db()
    product = owned_product(product_id)
    categories = db.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
    form = dict(product)
    form["price"] = f"{product['price_cents'] / 100:.2f}"

    if request.method == "POST":
        form, errors = read_product_fields(require_sku=False)
        if not errors:
            db.execute(
                "UPDATE products SET name = ?, description = ?, price_cents = ?,"
                " stock = ?, category_id = ?, icon = ?, is_active = ?"
                " WHERE id = ? AND seller_id = ?",
                (
                    form["name"], form["description"], form["price_cents"],
                    form["stock"], form["category_id"], form["icon"],
                    form["is_active"], product_id, g.seller["id"],
                ),
            )
            db.commit()

            saved, image_errors = _store_uploads(product_id, request.files.getlist("images"))
            for message in image_errors:
                flash(message, "error")
            flash("Listing updated.", "success")
            return redirect(url_for("sellers.edit_product", product_id=product_id))

        for message in errors:
            flash(message, "error")

    images = db.execute(
        "SELECT * FROM product_images WHERE product_id = ? ORDER BY position, id",
        (product_id,),
    ).fetchall()
    return render_template(
        "seller_product_form.html", form=form, categories=categories,
        product=product, images=images, seller=g.seller,
    )


def _store_uploads(product_id: int, files) -> tuple[int, list[str]]:
    db = get_db()
    existing = db.execute(
        "SELECT COUNT(*) AS n FROM product_images WHERE product_id = ?", (product_id,)
    ).fetchone()["n"]

    saved, errors = 0, []
    for storage in files:
        if not storage or not storage.filename:
            continue
        if existing + saved >= MAX_IMAGES_PER_PRODUCT:
            errors.append(f"Only {MAX_IMAGES_PER_PRODUCT} photos per listing.")
            break
        try:
            filename = save_product_image(storage)
        except ValueError as error:
            errors.append(f"{storage.filename}: {error}")
            continue
        db.execute(
            "INSERT INTO product_images (product_id, filename, position)"
            " VALUES (?, ?, ?)",
            (product_id, filename, existing + saved),
        )
        saved += 1

    if saved:
        db.commit()
    return saved, errors


@bp.post("/sell/products/<int:product_id>/images/<int:image_id>/delete")
@seller_required
def delete_image(product_id: int, image_id: int):
    owned_product(product_id)
    db = get_db()
    image = db.execute(
        "SELECT * FROM product_images WHERE id = ? AND product_id = ?",
        (image_id, product_id),
    ).fetchone()
    if image is not None:
        db.execute("DELETE FROM product_images WHERE id = ?", (image_id,))
        db.commit()
        delete_product_image(image["filename"])
        flash("Photo removed.", "info")
    return redirect(url_for("sellers.edit_product", product_id=product_id))


@bp.post("/sell/products/<int:product_id>/toggle")
@seller_required
def toggle_product(product_id: int):
    owned_product(product_id)
    db = get_db()
    db.execute(
        "UPDATE products SET is_active = 1 - is_active WHERE id = ? AND seller_id = ?",
        (product_id, g.seller["id"]),
    )
    db.commit()
    flash("Listing visibility updated.", "success")
    return redirect(url_for("sellers.products"))
