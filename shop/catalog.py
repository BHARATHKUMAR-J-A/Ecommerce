"""Storefront browsing: home, category pages, search and product detail."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, redirect, render_template, request

from .db import get_db
from .reviews import own_review, rating_breakdown, reviews_for
from .security import safe_redirect_target
from .variants import price_range, variants_for

bp = Blueprint("catalog", __name__)

# Whitelist - the key comes from the query string and must never reach SQL directly.
SORT_OPTIONS = {
    "featured": ("p.rating DESC, p.id ASC", "Featured"),
    "price-asc": ("p.price_cents ASC, p.id ASC", "Price: low to high"),
    "price-desc": ("p.price_cents DESC, p.id ASC", "Price: high to low"),
    "name": ("p.name ASC", "Name A-Z"),
    "newest": ("p.created_at DESC, p.id DESC", "Newest"),
}
DEFAULT_SORT = "featured"


def build_fts_query(query: str) -> str:
    """Turn user text into an FTS5 MATCH expression.

    Every word is wrapped in double quotes so FTS operators the visitor typed
    (NEAR, OR, *, ^, parentheses) are treated as literal text rather than syntax.
    Words are ANDed, and the last one gets a prefix wildcard so search behaves
    sensibly while someone is still typing.
    """
    words = [word.replace('"', "") for word in query.split()]
    # Punctuation-only words index to nothing, so keeping them would AND the
    # whole query down to zero results.
    words = [word for word in words if any(char.isalnum() for char in word)][:6]
    if not words:
        return ""
    terms = [f'"{word}"' for word in words[:-1]]
    terms.append(f'"{words[-1]}"*')
    return " AND ".join(terms)


def page_window(page: int, pages: int, span: int) -> list[int | None]:
    """Page numbers to render, with None marking a gap. Keeps the pager short."""
    if pages <= 2 * span + 3:
        return list(range(1, pages + 1))

    wanted = {1, pages}
    wanted.update(range(max(1, page - span), min(pages, page + span) + 1))

    out: list[int | None] = []
    previous = 0
    for number in sorted(wanted):
        if previous and number - previous > 1:
            out.append(None)
        out.append(number)
        previous = number
    return out


def _int_arg(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


@bp.route("/")
def index():
    return _listing(category_slug=None)


@bp.route("/c/<slug>")
def category(slug: str):
    return _listing(category_slug=slug)


@bp.post("/theme")
def theme():
    """Theme is a cookie the server renders, so there is no flash of the wrong one
    and no inline script for the CSP to allow."""
    choice = "dark" if request.form.get("theme") == "dark" else "light"
    response = redirect(safe_redirect_target(request.form.get("next")))
    response.set_cookie(
        "theme", choice, max_age=60 * 60 * 24 * 365, samesite="Lax", httponly=True
    )
    return response


def _listing(category_slug: str | None):
    db = get_db()
    per_page = current_app.config["PRODUCTS_PER_PAGE"]
    page = _int_arg("page", 1, 1, 10_000)
    query = (request.args.get("q") or "").strip()[:80]
    sort_key = request.args.get("sort", DEFAULT_SORT)
    if sort_key not in SORT_OPTIONS:
        sort_key = DEFAULT_SORT

    category_row = None
    if category_slug is not None:
        category_row = db.execute(
            "SELECT * FROM categories WHERE slug = ?", (category_slug,)
        ).fetchone()
        if category_row is None:
            abort(404, description="That category does not exist.")

    where = ["1 = 1"]
    params: list = []
    if category_row is not None:
        where.append("p.category_id = ?")
        params.append(category_row["id"])

    # Word-level matching via FTS5, so "earbuds wireless" and "wireless earbuds"
    # agree and "pen" no longer matches "opening".
    match_expression = build_fts_query(query)
    if match_expression:
        where.append(
            "p.id IN (SELECT rowid FROM products_fts WHERE products_fts MATCH ?)"
        )
        params.append(match_expression)
    elif query:
        where.append("0")  # searched for something with no indexable words

    min_price = _int_arg("min", 0, 0, 100_000)
    max_price = _int_arg("max", 0, 0, 100_000)
    if min_price:
        where.append("p.price_cents >= ?")
        params.append(min_price * 100)
    if max_price:
        where.append("p.price_cents <= ?")
        params.append(max_price * 100)
    if request.args.get("instock"):
        where.append("p.stock > 0")

    clause = " AND ".join(where)
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM visible_products p WHERE {clause}", params
    ).fetchone()["n"]

    # A name hit beats a description-only hit, but only while sorting is untouched.
    order_sql = SORT_OPTIONS[sort_key][0]
    order_params: list = []
    if match_expression and sort_key == DEFAULT_SORT:
        order_sql = (
            "p.id IN (SELECT rowid FROM products_fts WHERE products_fts MATCH ?) DESC, "
            + order_sql
        )
        order_params.append(f"name : ({match_expression})")

    pages = max(1, -(-total // per_page))
    page = min(page, pages)
    products = db.execute(
        f"SELECT p.*, c.name AS category_name, c.slug AS category_slug, c.tint,"
        f" s.shop_name, s.slug AS seller_slug,"
        f" (SELECT filename FROM product_images WHERE product_id = p.id"
        f"  ORDER BY position, id LIMIT 1) AS image,"
        f" (SELECT COUNT(*) FROM product_variants"
        f"  WHERE product_id = p.id AND is_active = 1) AS option_count"
        f" FROM visible_products p JOIN categories c ON c.id = p.category_id"
        f" LEFT JOIN sellers s ON s.id = p.seller_id"
        f" WHERE {clause} ORDER BY {order_sql} LIMIT ? OFFSET ?",
        [*params, *order_params, per_page, (page - 1) * per_page],
    ).fetchall()

    return render_template(
        "index.html",
        products=products,
        category=category_row,
        query=query,
        sort_key=sort_key,
        sort_options=SORT_OPTIONS,
        page=page,
        pages=pages,
        page_numbers=page_window(page, pages, current_app.config["PAGER_SPAN"]),
        total=total,
        show_hero=category_row is None and not query and page == 1,
    )


@bp.route("/p/<int:product_id>")
def product(product_id: int):
    db = get_db()
    row = db.execute(
        "SELECT p.*, c.name AS category_name, c.slug AS category_slug, c.tint,"
        " s.shop_name, s.slug AS seller_slug, s.bio AS seller_bio"
        " FROM visible_products p JOIN categories c ON c.id = p.category_id"
        " LEFT JOIN sellers s ON s.id = p.seller_id"
        " WHERE p.id = ?",
        (product_id,),
    ).fetchone()
    if row is None:
        abort(404, description="That product is no longer available.")

    images = db.execute(
        "SELECT * FROM product_images WHERE product_id = ? ORDER BY position, id",
        (product_id,),
    ).fetchall()
    related = db.execute(
        "SELECT p.*, (SELECT filename FROM product_images WHERE product_id = p.id"
        "  ORDER BY position, id LIMIT 1) AS image,"
        " (SELECT COUNT(*) FROM product_variants"
        "  WHERE product_id = p.id AND is_active = 1) AS option_count"
        " FROM visible_products p WHERE p.category_id = ? AND p.id != ?"
        " ORDER BY p.rating DESC LIMIT 4",
        (row["category_id"], row["id"]),
    ).fetchall()

    product_reviews = reviews_for(product_id)
    options = variants_for(product_id)
    low, high = price_range(product_id, row["price_cents"])
    return render_template(
        "product.html",
        product=row,
        images=images,
        related=related,
        options=options,
        price_low=low,
        price_high=high,
        reviews=product_reviews,
        breakdown=rating_breakdown(product_reviews),
        my_review=own_review(product_id),
    )
