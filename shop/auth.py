"""Registration, sign-in, sign-out and password reset."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .mail import password_reset, send_email
from .security import AttemptThrottle, get_throttle, safe_redirect_target

bp = Blueprint("auth", __name__, url_prefix="/auth")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
MIN_PASSWORD_LENGTH = 10


def get_login_throttle() -> AttemptThrottle:
    return get_throttle(
        "login",
        current_app.config["LOGIN_MAX_ATTEMPTS"],
        current_app.config["LOGIN_WINDOW_SECONDS"],
    )


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_password(password: str, confirm: str) -> list[str]:
    errors = []
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    elif not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        errors.append("Password must contain both a letter and a number.")
    if password != confirm:
        errors.append("The two passwords do not match.")
    return errors


def validate_registration(name: str, email: str, password: str, confirm: str) -> list[str]:
    errors = []
    if not 2 <= len(name) <= 60:
        errors.append("Please enter your name (2-60 characters).")
    if not EMAIL_RE.match(email) or len(email) > 120:
        errors.append("Please enter a valid email address.")
    errors.extend(validate_password(password, confirm))
    return errors


def _start_session(user_id: int) -> None:
    """New session id on login (fixation defence), but keep the shopping cart."""
    cart = session.get("cart")
    session.clear()
    if cart:
        session["cart"] = cart
    session["user_id"] = user_id
    session.permanent = True


@bp.route("/register", methods=("GET", "POST"))
def register():
    if g.user is not None:
        return redirect(url_for("catalog.index"))

    form = {"name": "", "email": ""}
    if request.method == "POST":
        form["name"] = (request.form.get("name") or "").strip()
        form["email"] = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""

        errors = validate_registration(form["name"], form["email"], password, confirm)
        if not errors:
            try:
                db = get_db()
                cursor = db.execute(
                    "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
                    (form["email"], form["name"], generate_password_hash(password)),
                )
                db.commit()
            except sqlite3.IntegrityError:
                errors.append("An account with that email already exists.")
            else:
                _start_session(int(cursor.lastrowid))
                flash(f"Welcome, {form['name']}!", "success")
                return redirect(url_for("catalog.index"))

        for message in errors:
            flash(message, "error")

    return render_template("register.html", form=form)


@bp.route("/login", methods=("GET", "POST"))
def login():
    if g.user is not None:
        return redirect(url_for("catalog.index"))

    next_url = request.values.get("next", "")
    email = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        throttle = get_login_throttle()
        keys = (f"ip:{request.remote_addr}", f"email:{email}")

        wait = throttle.retry_after(*keys)
        if wait:
            flash(
                f"Too many failed sign-in attempts. Try again in {wait} seconds.",
                "error",
            )
            return render_template("login.html", email=email, next_url=next_url), 429

        user = get_db().execute(
            "SELECT id, name, password_hash FROM users WHERE email = ?", (email,)
        ).fetchone()

        # Same message either way so the form cannot be used to enumerate accounts.
        if user is not None and check_password_hash(user["password_hash"], password):
            throttle.reset(*keys)
            _start_session(user["id"])
            flash(f"Signed in as {user['name']}.", "success")
            return redirect(safe_redirect_target(next_url))

        throttle.record_failure(*keys)
        flash("Incorrect email or password.", "error")

    return render_template("login.html", email=email, next_url=next_url)


@bp.post("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("catalog.index"))


@bp.route("/forgot", methods=("GET", "POST"))
def forgot_password():
    if g.user is not None:
        return redirect(url_for("catalog.index"))

    # Identical response whether or not the address exists, so this page cannot
    # be used to discover who has an account.
    sent_message = (
        "If that address has an account, a reset link is on its way. "
        "Check your inbox, and the link expires in "
        f"{current_app.config['RESET_TOKEN_MINUTES']} minutes."
    )

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()[:120]
        throttle = get_throttle(
            "reset",
            current_app.config["RESET_MAX_ATTEMPTS"],
            current_app.config["LOGIN_WINDOW_SECONDS"],
        )
        keys = (f"ip:{request.remote_addr}", f"email:{email}")

        if throttle.retry_after(*keys):
            flash("Too many reset requests. Please wait a few minutes.", "error")
            return render_template("forgot_password.html"), 429

        throttle.record_failure(*keys)
        user = get_db().execute(
            "SELECT id, name, email FROM users WHERE email = ?", (email,)
        ).fetchone()
        if user is not None:
            _issue_reset_token(user)

        flash(sent_message, "success")
        return redirect(url_for("auth.login"))

    return render_template("forgot_password.html")


def _issue_reset_token(user) -> None:
    minutes = current_app.config["RESET_TOKEN_MINUTES"]
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    db = get_db()
    # One live token at a time - asking again invalidates the previous link.
    db.execute(
        "UPDATE password_resets SET used_at = datetime('now')"
        " WHERE user_id = ? AND used_at IS NULL",
        (user["id"],),
    )
    db.execute(
        "INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
        (user["id"], hash_token(token), expires.strftime("%Y-%m-%d %H:%M:%S")),
    )
    db.commit()

    send_email(
        user["email"],
        "Reset your ShopSphere password",
        password_reset(
            user["name"],
            url_for("auth.reset_password", token=token, _external=True),
            minutes,
        ),
    )


@bp.route("/reset/<token>", methods=("GET", "POST"))
def reset_password(token: str):
    db = get_db()
    record = db.execute(
        "SELECT r.id, r.user_id, u.email FROM password_resets r"
        " JOIN users u ON u.id = r.user_id"
        " WHERE r.token_hash = ? AND r.used_at IS NULL"
        "   AND r.expires_at > datetime('now')",
        (hash_token(token),),
    ).fetchone()

    if record is None:
        flash("That reset link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password") or ""
        errors = validate_password(password, request.form.get("confirm") or "")
        if errors:
            for message in errors:
                flash(message, "error")
        else:
            db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(password), record["user_id"]),
            )
            db.execute(
                "UPDATE password_resets SET used_at = datetime('now') WHERE id = ?",
                (record["id"],),
            )
            db.commit()
            # A reset is also the remedy for a hijacked account, so drop any
            # existing session rather than signing the visitor straight in.
            session.clear()
            get_login_throttle().reset(f"email:{record['email']}")
            flash("Password updated. Please sign in.", "success")
            return redirect(url_for("auth.login"))

    return render_template("reset_password.html", token=token)
