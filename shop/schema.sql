-- ShopSphere schema. Money is stored in integer cents, never floats.
-- Bump SCHEMA_VERSION in shop/db.py whenever this file changes shape.

DROP VIEW IF EXISTS visible_products;
DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS password_resets;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS product_images;
DROP TABLE IF EXISTS products_fts;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS sellers;
DROP TABLE IF EXISTS categories;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS schema_meta;

CREATE TABLE schema_meta (
    version INTEGER NOT NULL
);

CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT    NOT NULL UNIQUE,
    name          TEXT    NOT NULL,
    password_hash TEXT    NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Only the SHA-256 of a reset token is stored, so a database leak cannot be
-- replayed against the reset endpoint.
CREATE TABLE password_resets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash TEXT    NOT NULL UNIQUE,
    expires_at TEXT    NOT NULL,
    used_at    TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_password_resets_user ON password_resets (user_id);

CREATE TABLE categories (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT    NOT NULL UNIQUE,
    name TEXT    NOT NULL,
    icon TEXT    NOT NULL DEFAULT '.',
    tint TEXT    NOT NULL DEFAULT '#6366f1'
);

-- Marketplace sellers. commission_rate is stored per seller so the platform can
-- agree a different cut with someone without a schema change.
CREATE TABLE sellers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL UNIQUE REFERENCES users (id) ON DELETE CASCADE,
    shop_name       TEXT    NOT NULL,
    slug            TEXT    NOT NULL UNIQUE,
    bio             TEXT    NOT NULL DEFAULT '',
    contact_email   TEXT    NOT NULL,
    payout_reference TEXT   NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
    commission_rate REAL    NOT NULL DEFAULT 0.10 CHECK (commission_rate BETWEEN 0 AND 1),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    reviewed_at     TEXT
);

CREATE INDEX idx_sellers_status ON sellers (status);

-- rating and review_count are caches of the reviews table, refreshed by
-- shop.reviews.refresh_product_rating. seller_id NULL means the shop's own stock.
CREATE TABLE products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sku          TEXT    NOT NULL UNIQUE,
    name         TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    price_cents  INTEGER NOT NULL CHECK (price_cents >= 0),
    stock        INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    category_id  INTEGER NOT NULL REFERENCES categories (id) ON DELETE RESTRICT,
    seller_id    INTEGER REFERENCES sellers (id) ON DELETE CASCADE,
    icon         TEXT    NOT NULL DEFAULT '.',
    rating       REAL    NOT NULL DEFAULT 0 CHECK (rating BETWEEN 0 AND 5),
    review_count INTEGER NOT NULL DEFAULT 0,
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_products_category ON products (category_id);
CREATE INDEX idx_products_active ON products (is_active);
CREATE INDEX idx_products_seller ON products (seller_id);

-- Uploaded photos. filename is a generated hex name; the original is never used.
CREATE TABLE product_images (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    filename   TEXT    NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_product_images_product ON product_images (product_id, position);

-- One definition of "a shopper may see and buy this", used by the catalogue and
-- the cart, so suspending a seller pulls their listings everywhere at once.
CREATE VIEW visible_products AS
SELECT p.*
FROM products p
LEFT JOIN sellers s ON s.id = p.seller_id
WHERE p.is_active = 1
  AND (p.seller_id IS NULL OR s.status = 'approved');

-- Word-level search. External-content table: it indexes products rather than
-- duplicating it, and the triggers below keep the two in step.
CREATE VIRTUAL TABLE products_fts USING fts5 (
    name,
    description,
    sku,
    content = 'products',
    content_rowid = 'id',
    tokenize = 'unicode61'
);

CREATE TRIGGER products_fts_insert AFTER INSERT ON products BEGIN
    INSERT INTO products_fts (rowid, name, description, sku)
    VALUES (new.id, new.name, new.description, new.sku);
END;

CREATE TRIGGER products_fts_delete AFTER DELETE ON products BEGIN
    INSERT INTO products_fts (products_fts, rowid, name, description, sku)
    VALUES ('delete', old.id, old.name, old.description, old.sku);
END;

CREATE TRIGGER products_fts_update AFTER UPDATE ON products BEGIN
    INSERT INTO products_fts (products_fts, rowid, name, description, sku)
    VALUES ('delete', old.id, old.name, old.description, old.sku);
    INSERT INTO products_fts (rowid, name, description, sku)
    VALUES (new.id, new.name, new.description, new.sku);
END;

CREATE TABLE reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    rating     INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    title      TEXT    NOT NULL DEFAULT '',
    body       TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (product_id, user_id)
);

CREATE INDEX idx_reviews_product ON reviews (product_id);

-- user_id is nullable so guests can order; email is always present and is what
-- a guest must supply to look the order up again.
CREATE TABLE orders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    reference      TEXT    NOT NULL UNIQUE,
    user_id        INTEGER REFERENCES users (id) ON DELETE SET NULL,
    email          TEXT    NOT NULL,
    ship_name      TEXT    NOT NULL,
    ship_address   TEXT    NOT NULL,
    ship_city      TEXT    NOT NULL,
    ship_postcode  TEXT    NOT NULL,
    ship_country   TEXT    NOT NULL,
    subtotal_cents INTEGER NOT NULL CHECK (subtotal_cents >= 0),
    shipping_cents INTEGER NOT NULL CHECK (shipping_cents >= 0),
    tax_cents      INTEGER NOT NULL CHECK (tax_cents >= 0),
    total_cents    INTEGER NOT NULL CHECK (total_cents >= 0),
    status         TEXT    NOT NULL DEFAULT 'paid',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_orders_user ON orders (user_id);
CREATE INDEX idx_orders_email ON orders (email);

-- Name and price are copied in so historical orders survive product edits.
-- The commission split is frozen here too: changing a seller's rate later must
-- not rewrite what they already earned.
CREATE TABLE order_items (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id             INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    product_id           INTEGER REFERENCES products (id) ON DELETE SET NULL,
    seller_id            INTEGER REFERENCES sellers (id) ON DELETE SET NULL,
    name                 TEXT    NOT NULL,
    icon                 TEXT    NOT NULL DEFAULT '.',
    unit_cents           INTEGER NOT NULL CHECK (unit_cents >= 0),
    quantity             INTEGER NOT NULL CHECK (quantity > 0),
    line_cents           INTEGER NOT NULL CHECK (line_cents >= 0),
    commission_rate      REAL    NOT NULL DEFAULT 0,
    commission_cents     INTEGER NOT NULL DEFAULT 0 CHECK (commission_cents >= 0),
    seller_earning_cents INTEGER NOT NULL DEFAULT 0 CHECK (seller_earning_cents >= 0)
);

CREATE INDEX idx_order_items_order ON order_items (order_id);
CREATE INDEX idx_order_items_seller ON order_items (seller_id);
