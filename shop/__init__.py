"""ShopSphere - a small but complete e-commerce storefront built on Flask + SQLite."""

from __future__ import annotations

import logging
import os

from flask import Flask, g, render_template, request, session
from werkzeug.exceptions import HTTPException

from . import db
from .config import Config, load_secret_key
from .security import apply_security_headers, csrf_token, verify_csrf

__version__ = "1.0.0"


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    os.makedirs(app.instance_path, exist_ok=True)

    # Tunables that post-date config.py.
    app.config.setdefault("LOGIN_MAX_ATTEMPTS", 8)
    app.config.setdefault("LOGIN_WINDOW_SECONDS", 300)
    app.config.setdefault("DB_TIMEOUT_SECONDS", 10.0)
    app.config.setdefault("PAGER_SPAN", 2)

    app.config.setdefault("MAIL_BACKEND", os.environ.get("SHOP_MAIL_BACKEND", "console"))
    app.config.setdefault("MAIL_FROM", os.environ.get("SHOP_MAIL_FROM", "shop@shopsphere.test"))
    app.config.setdefault("SMTP_HOST", os.environ.get("SHOP_SMTP_HOST", ""))
    app.config.setdefault("SMTP_PORT", int(os.environ.get("SHOP_SMTP_PORT", "587")))
    app.config.setdefault("SMTP_USER", os.environ.get("SHOP_SMTP_USER", ""))
    app.config.setdefault("SMTP_PASSWORD", os.environ.get("SHOP_SMTP_PASSWORD", ""))
    app.config.setdefault("SMTP_STARTTLS", os.environ.get("SHOP_SMTP_STARTTLS", "1") != "0")

    app.config.setdefault("RESET_TOKEN_MINUTES", 60)
    app.config.setdefault("RESET_MAX_ATTEMPTS", 5)
    app.config.setdefault("LOOKUP_MAX_ATTEMPTS", 10)
    app.config.setdefault("ALLOW_GUEST_CHECKOUT", True)

    # Marketplace: the platform's default cut of a seller's line total.
    app.config.setdefault("COMMISSION_RATE", 0.10)
    # Overrides config.py's 1 MB, which predates image uploads.
    app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024

    app.config["DATABASE"] = os.path.join(app.instance_path, "shop.sqlite")
    app.config["SECRET_KEY"] = load_secret_key(app.instance_path)
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    _configure_logging(app)
    _register_hooks(app)
    _register_blueprints(app)
    _register_template_helpers(app)
    _register_error_handlers(app)
    return app


def _configure_logging(app: Flask) -> None:
    # Without this the app logger inherits root's WARNING level and the console
    # mail backend - the whole point of which is to be readable - prints nothing.
    if not app.config.get("TESTING"):
        app.logger.setLevel(logging.INFO)


def _register_hooks(app: Flask) -> None:
    from .cart import cart_summary

    @app.before_request
    def load_request_context():
        verify_csrf()
        user_id = session.get("user_id")
        g.user = None
        if user_id is not None:
            g.user = db.get_db().execute(
                "SELECT id, email, name, is_admin FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if g.user is None:
                session.clear()

    @app.context_processor
    def inject_globals():
        return {
            "cart_count": cart_summary()["count"],
            "nav_categories": db.get_db()
            .execute("SELECT slug, name, icon FROM categories ORDER BY name")
            .fetchall(),
            "store_name": app.config["STORE_NAME"],
            "store_tagline": app.config["STORE_TAGLINE"],
            "seller_account": _seller_for_nav(),
        }

    app.after_request(apply_security_headers)


def _seller_for_nav():
    """The signed-in user's seller record, if any, for the account menu."""
    if getattr(g, "user", None) is None:
        return None
    return db.get_db().execute(
        "SELECT slug, status FROM sellers WHERE user_id = ?", (g.user["id"],)
    ).fetchone()


def _register_blueprints(app: Flask) -> None:
    from . import admin, auth, cart, catalog, orders, reviews, sellers

    app.register_blueprint(catalog.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(cart.bp)
    app.register_blueprint(orders.bp)
    app.register_blueprint(reviews.bp)
    app.register_blueprint(sellers.bp)
    app.register_blueprint(admin.bp)
    app.add_url_rule("/", endpoint="index")


def _register_template_helpers(app: Flask) -> None:
    # A global rather than a context value so imported macros can reach it.
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.template_filter("money")
    def money(cents: int | None) -> str:
        return f"{app.config['CURRENCY_SYMBOL']}{(cents or 0) / 100:,.2f}"

    @app.template_filter("stars")
    def stars(rating: float) -> str:
        filled = int(rating) + (1 if rating - int(rating) >= 0.5 else 0)
        return "\u2605" * filled + "\u2606" * (5 - filled)


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        return (
            render_template("error.html", code=error.code, message=error.description),
            error.code,
        )

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        app.logger.exception("Unhandled error on %s %s", request.method, request.path)
        try:
            body = render_template(
                "error.html", code=500, message="Something went wrong on our side."
            )
        except Exception:
            # The template itself needs the database, so fall back to plain text.
            body = "500 - something went wrong on our side."
        return body, 500
