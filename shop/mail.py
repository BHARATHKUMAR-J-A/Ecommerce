"""Outbound email.

Three backends, chosen with MAIL_BACKEND:

* ``console`` (default) - writes the message to the app log. Nothing leaves the box.
* ``file``              - writes one .eml per message into instance/mail/.
* ``smtp``              - a real send, configured from SHOP_SMTP_* environment vars.

Sending never raises into a request: a shop that cannot email should still be
able to take the order.
"""

from __future__ import annotations

import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from flask import current_app


def send_email(to: str, subject: str, body: str) -> bool:
    """Returns True if the message was handed off successfully."""
    message = EmailMessage()
    message["From"] = current_app.config["MAIL_FROM"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    backend = current_app.config["MAIL_BACKEND"]
    try:
        if backend == "smtp":
            _send_smtp(message)
        elif backend == "file":
            _write_to_disk(message)
        else:
            current_app.logger.info(
                "[email:%s] To: %s | %s\n%s", backend, to, subject, body
            )
    except Exception:
        current_app.logger.exception("Could not send %r to %s", subject, to)
        return False
    return True


def _write_to_disk(message: EmailMessage) -> None:
    outbox = Path(current_app.instance_path) / "mail"
    outbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    (outbox / f"{stamp}.eml").write_text(message.as_string(), encoding="utf-8")


def _send_smtp(message: EmailMessage) -> None:
    config = current_app.config
    host, port = config["SMTP_HOST"], config["SMTP_PORT"]
    if not host:
        raise RuntimeError("MAIL_BACKEND is 'smtp' but SHOP_SMTP_HOST is unset.")

    with smtplib.SMTP(host, port, timeout=15) as server:
        if config["SMTP_STARTTLS"]:
            server.starttls(context=ssl.create_default_context())
        if config["SMTP_USER"]:
            server.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
        server.send_message(message)


def order_confirmation(order, items) -> str:
    currency = current_app.config["CURRENCY_SYMBOL"]

    def money(cents: int) -> str:
        return f"{currency}{cents / 100:,.2f}"

    lines = [
        f"Thanks for your order, {order['ship_name']}.",
        "",
        f"Reference: {order['reference']}",
        "",
    ]
    lines += [
        f"  {item['quantity']} x {item['name']} - {money(item['line_cents'])}"
        for item in items
    ]
    shipping = "Free" if order["shipping_cents"] == 0 else money(order["shipping_cents"])
    lines += [
        "",
        f"Subtotal: {money(order['subtotal_cents'])}",
        f"Shipping: {shipping}",
        f"Tax:      {money(order['tax_cents'])}",
        f"Total:    {money(order['total_cents'])}",
        "",
        "Shipping to:",
        f"  {order['ship_name']}",
        f"  {order['ship_address']}",
        f"  {order['ship_city']} {order['ship_postcode']}",
        f"  {order['ship_country']}",
        "",
        "This is a demo store. No payment was taken and nothing will be shipped.",
    ]
    return "\n".join(lines)


def password_reset(name: str, reset_url: str, valid_minutes: int) -> str:
    return "\n".join(
        [
            f"Hello {name},",
            "",
            "Someone asked to reset the password on your ShopSphere account.",
            "Use the link below to choose a new one:",
            "",
            f"  {reset_url}",
            "",
            f"The link works once and expires in {valid_minutes} minutes.",
            "If this was not you, ignore this email - nothing has changed.",
        ]
    )
