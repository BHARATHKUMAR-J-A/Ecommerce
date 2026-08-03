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


def _money(cents: int) -> str:
    from . import settings

    return f"{settings.get('CURRENCY_SYMBOL')}{cents / 100:,.2f}"


def _describe(item) -> str:
    return (
        item["name"]
        + (f" [{item['variant_label']}]" if item["variant_label"] else "")
        + (f' engraved "{item["personalisation"]}"' if item["personalisation"] else "")
    )


def order_confirmation(order, items) -> str:
    lines = [
        f"Thanks for your order, {order['ship_name']}.",
        "",
        f"Reference: {order['reference']}",
        "",
    ]
    lines += [
        f"  {item['quantity']} x {_describe(item)} - {_money(item['line_cents'])}"
        for item in items
    ]
    shipping = "Free" if order["shipping_cents"] == 0 else _money(order["shipping_cents"])
    lines += [
        "",
        f"Subtotal: {_money(order['subtotal_cents'])}",
        f"Shipping: {shipping}",
        f"Tax:      {_money(order['tax_cents'])}",
        f"Total:    {_money(order['total_cents'])}",
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


def seller_sale(shop_name: str, order, lines, dashboard_url: str) -> str:
    """What one seller sold. Only their own lines, and only their own money."""
    goods = sum(line["line_cents"] for line in lines)
    earnings = sum(line["seller_earning_cents"] for line in lines)
    units = sum(line["quantity"] for line in lines)

    body = [
        f"Good news - {shop_name} has a sale.",
        "",
        f"Order {order['reference']}, placed {order['created_at']}.",
        "",
    ]
    body += [
        f"  {line['quantity']} x {_describe(line)} - {_money(line['line_cents'])}"
        for line in lines
    ]
    body += [
        "",
        f"{units} item(s), {_money(goods)} of goods.",
        f"Your earnings after commission: {_money(earnings)}.",
        "",
        "Pack the order and the shop will mark it shipped:",
        f"  {dashboard_url}",
        "",
        "If the order is cancelled, this sale and the stock are both reversed.",
    ]
    return "\n".join(body)


def application_decision(name: str, shop_name: str, status: str, url: str) -> str:
    if status == "approved":
        middle = [
            f"{shop_name} has been approved. You can list products right away:",
            "",
            f"  {url}",
            "",
            "Your listings appear in the catalogue as soon as you publish them.",
        ]
    elif status == "rejected":
        middle = [
            f"We are not able to approve {shop_name} at the moment, so it will",
            "not appear in the catalogue. If you think this was a mistake, reply",
            "to this email and a person will look at it again.",
        ]
    else:  # suspended
        middle = [
            f"{shop_name} has been suspended, so its listings are hidden from the",
            "catalogue for now. Orders you have already taken are unaffected and",
            "you still need to fulfil them.",
        ]

    return "\n".join([f"Hello {name},", ""] + middle)


def shipping_update(order, status: str) -> str:
    headline = {
        "shipped": "is on its way",
        "delivered": "has been delivered",
        "cancelled": "has been cancelled",
    }[status]

    body = [
        f"Hello {order['ship_name']},",
        "",
        f"Your order {order['reference']} {headline}.",
        "",
    ]
    if status == "cancelled":
        body += [
            "Nothing will be shipped and the items have gone back into stock.",
            "This is a demo store, so there was no payment to refund.",
        ]
    else:
        body += [
            "Shipping to:",
            f"  {order['ship_name']}",
            f"  {order['ship_address']}",
            f"  {order['ship_city']} {order['ship_postcode']}",
            f"  {order['ship_country']}",
            "",
            "This is a demo store, so nothing is really in transit.",
        ]
    return "\n".join(body)
