"""Sales reporting for the store owner.

One thing worth being precise about: what the shop *takes* is not what customers
*pay*. A customer paying $32 for a seller's mug leaves the platform $3.20. So
these queries distinguish three figures:

* gross      - what customers paid for the goods (excludes shipping and tax)
* commission - the platform's cut of seller lines
* own sales  - lines with no seller, where the whole amount is the platform's

Platform revenue is commission + own sales. Cancelled orders count for nothing.
"""

from __future__ import annotations

from .db import get_db

# Query-string value -> (SQLite date modifier or None, label). The modifier is
# passed as a bound parameter, never interpolated.
PERIODS = {
    "all": (None, "All time"),
    "24h": ("-1 day", "Last 24 hours"),
    "7d": ("-7 days", "Last 7 days"),
    "30d": ("-30 days", "Last 30 days"),
    "12m": ("-365 days", "Last 12 months"),
}
DEFAULT_PERIOD = "30d"

ITEM_SORTS = {
    "revenue": "platform_revenue DESC",
    "units": "units DESC",
    "gross": "gross DESC",
    "name": "name ASC",
}
DEFAULT_ITEM_SORT = "revenue"


def _window(period: str) -> tuple[str, list]:
    """SQL fragment and params limiting to the chosen period."""
    modifier = PERIODS.get(period, PERIODS[DEFAULT_PERIOD])[0]
    if modifier is None:
        return "", []
    return " AND o.created_at >= datetime('now', ?)", [modifier]


def totals(period: str = "all", seller_id: int | None = None) -> dict:
    clause, params = _window(period)
    seller_clause = ""
    if seller_id is not None:
        seller_clause = " AND i.seller_id = ?"
        params = params + [seller_id]

    row = get_db().execute(
        "SELECT"
        "  COALESCE(SUM(i.quantity), 0) AS units,"
        "  COUNT(DISTINCT o.id) AS orders,"
        "  COALESCE(SUM(i.line_cents), 0) AS gross,"
        "  COALESCE(SUM(i.commission_cents), 0) AS commission,"
        "  COALESCE(SUM(i.seller_earning_cents), 0) AS seller_earnings,"
        "  COALESCE(SUM(CASE WHEN i.seller_id IS NULL THEN i.line_cents ELSE 0 END), 0)"
        "    AS own_sales"
        " FROM order_items i JOIN orders o ON o.id = i.order_id"
        f" WHERE o.status != 'cancelled'{clause}{seller_clause}",
        params,
    ).fetchone()

    result = dict(row)
    result["platform_revenue"] = result["commission"] + result["own_sales"]
    return result


def items(period: str = "all", sort: str = DEFAULT_ITEM_SORT,
          seller_id: int | None = None, limit: int = 200) -> list:
    """One row per product+option actually sold."""
    clause, params = _window(period)
    seller_clause = ""
    if seller_id is not None:
        seller_clause = " AND i.seller_id = ?"
        params = params + [seller_id]

    order_by = ITEM_SORTS.get(sort, ITEM_SORTS[DEFAULT_ITEM_SORT])
    return get_db().execute(
        "SELECT"
        "  i.product_id, i.name, i.variant_label,"
        "  s.shop_name, s.slug AS seller_slug,"
        "  SUM(i.quantity) AS units,"
        "  COUNT(DISTINCT o.id) AS orders,"
        "  SUM(i.line_cents) AS gross,"
        "  SUM(i.commission_cents) AS commission,"
        "  SUM(i.seller_earning_cents) AS seller_earnings,"
        "  SUM(CASE WHEN i.seller_id IS NULL THEN i.line_cents ELSE i.commission_cents END)"
        "    AS platform_revenue,"
        "  MAX(o.created_at) AS last_sold"
        " FROM order_items i"
        " JOIN orders o ON o.id = i.order_id"
        " LEFT JOIN sellers s ON s.id = i.seller_id"
        f" WHERE o.status != 'cancelled'{clause}{seller_clause}"
        " GROUP BY i.name, i.variant_label, i.seller_id"
        f" ORDER BY {order_by} LIMIT ?",
        [*params, limit],
    ).fetchall()


def sellers(period: str = "all") -> list:
    """Every seller who has sold something in the window, best earner first."""
    clause, params = _window(period)
    return get_db().execute(
        "SELECT s.id, s.shop_name, s.slug, s.status, s.commission_rate,"
        "  COALESCE(SUM(i.quantity), 0) AS units,"
        "  COUNT(DISTINCT o.id) AS orders,"
        "  COALESCE(SUM(i.line_cents), 0) AS gross,"
        "  COALESCE(SUM(i.commission_cents), 0) AS commission,"
        "  COALESCE(SUM(i.seller_earning_cents), 0) AS owed,"
        "  COUNT(DISTINCT i.name) AS distinct_items"
        " FROM sellers s"
        " LEFT JOIN order_items i ON i.seller_id = s.id"
        " LEFT JOIN orders o ON o.id = i.order_id"
        f"   AND o.status != 'cancelled'{clause}"
        " GROUP BY s.id ORDER BY commission DESC, s.shop_name",
        params,
    ).fetchall()


def daily(period: str = "30d", days: int = 30) -> list:
    """Revenue per day, for a simple trend."""
    clause, params = _window(period)
    return get_db().execute(
        "SELECT date(o.created_at) AS day,"
        "  SUM(i.quantity) AS units,"
        "  SUM(i.line_cents) AS gross,"
        "  SUM(CASE WHEN i.seller_id IS NULL THEN i.line_cents ELSE i.commission_cents END)"
        "    AS platform_revenue"
        " FROM order_items i JOIN orders o ON o.id = i.order_id"
        f" WHERE o.status != 'cancelled'{clause}"
        " GROUP BY day ORDER BY day DESC LIMIT ?",
        [*params, days],
    ).fetchall()
