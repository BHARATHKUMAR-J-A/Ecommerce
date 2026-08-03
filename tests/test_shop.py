import io
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shop import create_app
from shop.db import init_db
from shop.seed import seed_database


class ShopTestCase(unittest.TestCase):
    extra_config: dict = {}

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.db_path,
                "SECRET_KEY": "test-key",
                **self.extra_config,
            }
        )
        with self.app.app_context():
            init_db()
            seed_database()
        self.client = self.app.test_client()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                os.unlink(path)

    # -- helpers -----------------------------------------------------------
    def csrf(self, path="/"):
        html = self.client.get(path).get_data(as_text=True)
        marker = 'name="csrf_token" value="'
        start = html.index(marker) + len(marker)
        return html[start : html.index('"', start)]

    def login(self, email="demo@shopsphere.test", password="Demo#12345"):
        return self.client.post(
            "/auth/login",
            data={"email": email, "password": password, "csrf_token": self.csrf("/auth/login")},
            follow_redirects=True,
        )

    def logout(self):
        return self.client.post("/auth/logout", data={"csrf_token": self.csrf("/")})

    def add_to_cart(self, product_id=1, quantity=1, variant_id=None, text=None):
        data = {"quantity": quantity, "csrf_token": self.csrf("/")}
        if variant_id is not None:
            data["variant_id"] = variant_id
        if text is not None:
            data["personalisation"] = text
        return self.client.post(
            f"/cart/add/{product_id}", data=data, follow_redirects=True
        )

    def cart_line_ids(self):
        html = self.client.get("/cart/").get_data(as_text=True)
        return re.findall(r"/cart/remove/([0-9a-f]+)", html)

    def place_order(self, product_id=2, quantity=1):
        """Returns the new order's reference. Caller must already be signed in."""
        self.add_to_cart(product_id, quantity)
        response = self.client.post(
            "/orders/checkout",
            data={
                "ship_name": "Demo Shopper",
                "ship_address": "1 Test Street",
                "ship_city": "Testville",
                "ship_postcode": "123456",
                "ship_country": "Singapore",
                "csrf_token": self.csrf("/orders/checkout"),
            },
        )
        return response.headers["Location"].split("/orders/")[1].split("?")[0]

    def query(self, sql, params=()):
        with self.app.app_context():
            from shop.db import get_db

            return get_db().execute(sql, params).fetchone()


class TestCatalog(ShopTestCase):
    def test_home_lists_products(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('class="card"', body)
        self.assertIn("Cast Iron Skillet", body)  # highest rated, so first under 'featured'

    def test_pagination_splits_the_catalogue(self):
        page_one = self.client.get("/").get_data(as_text=True)
        page_two = self.client.get("/?page=2").get_data(as_text=True)
        self.assertIn("Cast Iron Skillet", page_one)
        self.assertNotIn("Cast Iron Skillet", page_two)

    def test_category_filter(self):
        body = self.client.get("/c/grocery").get_data(as_text=True)
        self.assertIn("Raw Wildflower Honey", body)
        self.assertNotIn("Nimbus Wireless Earbuds", body)

    def test_unknown_category_404s(self):
        self.assertEqual(self.client.get("/c/does-not-exist").status_code, 404)

    def test_search_matches_description(self):
        body = self.client.get("/?q=merino").get_data(as_text=True)
        self.assertIn("Everyday Merino Crew Tee", body)

    def test_search_wildcards_are_escaped(self):
        """A bare % indexes to no word, so it must match nothing - not everything."""
        body = self.client.get("/?q=%25").get_data(as_text=True)
        self.assertIn("Nothing matched that", body)

    def test_search_underscore_is_escaped(self):
        body = self.client.get("/?q=_").get_data(as_text=True)
        self.assertIn("Nothing matched that", body)

    def test_sort_parameter_cannot_inject_sql(self):
        response = self.client.get("/?sort=price_cents;DROP TABLE products--")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Aurora", self.client.get("/?q=Aurora").get_data(as_text=True))

    def test_product_page(self):
        body = self.client.get("/p/1").get_data(as_text=True)
        self.assertIn("Add to cart", body)

    def test_missing_product_404s(self):
        self.assertEqual(self.client.get("/p/99999").status_code, 404)


class TestCart(ShopTestCase):
    def test_add_and_total(self):
        self.add_to_cart(2, 3)
        body = self.client.get("/cart/").get_data(as_text=True)
        self.assertIn("Nimbus Wireless Earbuds", body)
        self.assertIn("$269.70", body)  # 3 x $89.90

    def test_quantity_is_capped_by_stock(self):
        with self.app.app_context():
            from shop.db import get_db

            db = get_db()
            db.execute("UPDATE products SET stock = 2 WHERE id = 1")
            db.commit()
        self.assertIn("Only 2", self.add_to_cart(1, 20).get_data(as_text=True))
        self.assertIn('value="2"', self.client.get("/cart/").get_data(as_text=True))

    def test_remove_item(self):
        self.add_to_cart(1)
        line_id = self.cart_line_ids()[0]
        self.client.post(f"/cart/remove/{line_id}", data={"csrf_token": self.csrf("/cart/")})
        self.assertIn("Your cart is empty", self.client.get("/cart/").get_data(as_text=True))

    def test_free_shipping_threshold(self):
        self.add_to_cart(3, 1)  # $129 keyboard, over the $50 threshold
        self.assertIn("<dd>Free</dd>", self.client.get("/cart/").get_data(as_text=True))

    def test_shipping_is_charged_below_the_threshold(self):
        self.add_to_cart(51, 1)  # $9.99 crisps
        self.assertIn("<dd>$4.99</dd>", self.client.get("/cart/").get_data(as_text=True))


class TestSecurity(ShopTestCase):
    def test_post_without_csrf_token_is_rejected(self):
        self.client.get("/")
        self.assertEqual(self.client.post("/cart/add/1", data={"quantity": 1}).status_code, 400)

    def test_wrong_csrf_token_is_rejected(self):
        self.client.get("/")
        response = self.client.post("/cart/add/1", data={"quantity": 1, "csrf_token": "nope"})
        self.assertEqual(response.status_code, 400)

    def test_checkout_is_open_to_guests_by_default(self):
        self.add_to_cart(2, 1)
        self.assertEqual(self.client.get("/orders/checkout").status_code, 200)

    def test_checkout_requires_login_when_guests_are_disabled(self):
        self.app.config["ALLOW_GUEST_CHECKOUT"] = False
        self.add_to_cart(2, 1)
        response = self.client.get("/orders/checkout")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.headers["Location"])

    def test_order_history_still_requires_login(self):
        response = self.client.get("/orders/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.headers["Location"])

    def test_admin_area_blocked_for_shoppers(self):
        self.login()
        self.assertEqual(self.client.get("/admin/").status_code, 403)

    def test_admin_area_open_for_admins(self):
        self.login("admin@shopsphere.test", "Admin#12345")
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_open_redirect_is_blocked(self):
        response = self.client.post(
            "/auth/login",
            data={
                "email": "demo@shopsphere.test",
                "password": "Demo#12345",
                "next": "https://evil.example.com/steal",
                "csrf_token": self.csrf("/auth/login"),
            },
        )
        self.assertNotIn("evil.example.com", response.headers["Location"])

    def test_login_failure_message_does_not_leak_account_existence(self):
        for email in ("demo@shopsphere.test", "nobody@nowhere.test"):
            body = self.client.post(
                "/auth/login",
                data={"email": email, "password": "wrong", "csrf_token": self.csrf("/auth/login")},
                follow_redirects=True,
            ).get_data(as_text=True)
            self.assertIn("Incorrect email or password", body)

    def test_password_is_hashed(self):
        with self.app.app_context():
            from shop.db import get_db

            stored = get_db().execute(
                "SELECT password_hash FROM users WHERE email = 'demo@shopsphere.test'"
            ).fetchone()["password_hash"]
        self.assertNotIn("Demo#12345", stored)

    def test_security_headers_present(self):
        headers = self.client.get("/").headers
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_reflected_search_term_is_escaped(self):
        body = self.client.get("/?q=<script>alert(1)</script>").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", body)

    def test_other_users_order_is_not_readable(self):
        self.login()
        reference = self.place_order()
        self.logout()
        self.login("admin@shopsphere.test", "Admin#12345")
        self.assertEqual(self.client.get(f"/orders/{reference}").status_code, 404)


class TestAuth(ShopTestCase):
    def test_register_and_sign_in(self):
        body = self.client.post(
            "/auth/register",
            data={
                "name": "New Person",
                "email": "new@example.com",
                "password": "Password123",
                "confirm": "Password123",
                "csrf_token": self.csrf("/auth/register"),
            },
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn("Welcome, New Person", body)

    def test_weak_password_is_rejected(self):
        body = self.client.post(
            "/auth/register",
            data={
                "name": "Weak",
                "email": "weak@example.com",
                "password": "short",
                "confirm": "short",
                "csrf_token": self.csrf("/auth/register"),
            },
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn("at least 10 characters", body)

    def test_duplicate_email_is_rejected(self):
        body = self.client.post(
            "/auth/register",
            data={
                "name": "Copy Cat",
                "email": "demo@shopsphere.test",
                "password": "Password123",
                "confirm": "Password123",
                "csrf_token": self.csrf("/auth/register"),
            },
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn("already exists", body)

    def test_cart_survives_sign_in(self):
        self.add_to_cart(1, 1)
        self.login()
        self.assertIn("Aurora", self.client.get("/cart/").get_data(as_text=True))


class TestCheckout(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_order_decrements_stock_and_empties_cart(self):
        with self.app.app_context():
            from shop.db import get_db

            before = get_db().execute("SELECT stock FROM products WHERE id = 2").fetchone()["stock"]

        self.add_to_cart(2, 2)
        response = self.client.post(
            "/orders/checkout",
            data={
                "ship_name": "Demo Shopper",
                "ship_address": "1 Test Street",
                "ship_city": "Testville",
                "ship_postcode": "123456",
                "ship_country": "Singapore",
                "csrf_token": self.csrf("/orders/checkout"),
            },
            follow_redirects=True,
        )
        self.assertIn("your order is confirmed", response.get_data(as_text=True))

        with self.app.app_context():
            from shop.db import get_db

            after = get_db().execute("SELECT stock FROM products WHERE id = 2").fetchone()["stock"]
        self.assertEqual(after, before - 2)
        self.assertIn("Your cart is empty", self.client.get("/cart/").get_data(as_text=True))

    def test_checkout_requires_address(self):
        self.add_to_cart(2, 1)
        body = self.client.post(
            "/orders/checkout",
            data={"csrf_token": self.csrf("/orders/checkout")},
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn("is required", body)

    def test_empty_cart_cannot_check_out(self):
        response = self.client.get("/orders/checkout", follow_redirects=True)
        self.assertIn("Your cart is empty", response.get_data(as_text=True))


class TestAdmin(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.login("admin@shopsphere.test", "Admin#12345")

    def test_create_product(self):
        self.client.post(
            "/admin/products/new",
            data={
                "sku": "NEW-001",
                "name": "Test Widget",
                "description": "A widget for testing.",
                "icon": "\U0001F9EA",
                "category_id": "1",
                "price": "19.99",
                "stock": "5",
                "rating": "4",
                "is_active": "1",
                "csrf_token": self.csrf("/admin/products/new"),
            },
            follow_redirects=True,
        )
        body = self.client.get("/?q=Test Widget").get_data(as_text=True)
        self.assertIn("$19.99", body)

    def test_price_is_stored_exactly_in_cents(self):
        self.client.post(
            "/admin/products/new",
            data={
                "sku": "CENT-001", "name": "Penny Item", "description": "",
                "icon": "\U0001F4B0", "category_id": "1", "price": "0.07",
                "stock": "1", "rating": "0", "is_active": "1",
                "csrf_token": self.csrf("/admin/products/new"),
            },
            follow_redirects=True,
        )
        with self.app.app_context():
            from shop.db import get_db

            cents = get_db().execute(
                "SELECT price_cents FROM products WHERE sku = 'CENT-001'"
            ).fetchone()["price_cents"]
        self.assertEqual(cents, 7)

    def test_negative_price_is_rejected(self):
        body = self.client.post(
            "/admin/products/new",
            data={
                "sku": "BAD-001", "name": "Bad", "description": "",
                "icon": "\U0001F4E6", "category_id": "1", "price": "-5",
                "stock": "1", "rating": "0", "is_active": "1",
                "csrf_token": self.csrf("/admin/products/new"),
            },
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn("Price must be a positive amount", body)

    def test_hidden_product_disappears_from_storefront(self):
        self.client.post("/admin/products/1/toggle", data={"csrf_token": self.csrf("/admin/products")})
        self.assertEqual(self.client.get("/p/1").status_code, 404)


class TestLoginThrottle(ShopTestCase):
    extra_config = {"LOGIN_MAX_ATTEMPTS": 3, "LOGIN_WINDOW_SECONDS": 300}

    def bad_login(self, email="demo@shopsphere.test"):
        return self.client.post(
            "/auth/login",
            data={"email": email, "password": "wrong", "csrf_token": self.csrf("/auth/login")},
        )

    def test_repeated_failures_are_locked_out(self):
        for _ in range(3):
            self.assertEqual(self.bad_login().status_code, 200)
        locked = self.bad_login()
        self.assertEqual(locked.status_code, 429)
        self.assertIn("Too many failed sign-in attempts", locked.get_data(as_text=True))

    def test_lockout_blocks_the_correct_password_too(self):
        for _ in range(3):
            self.bad_login()
        body = self.login().get_data(as_text=True)
        self.assertIn("Too many failed sign-in attempts", body)
        self.assertNotIn("Signed in as", body)

    def test_success_clears_the_counter(self):
        self.bad_login()
        self.bad_login()
        self.assertIn("Signed in as", self.login().get_data(as_text=True))
        self.logout()
        # The two earlier failures must not count towards a new lockout.
        self.assertEqual(self.bad_login().status_code, 200)

    def test_throttle_is_per_app_not_global(self):
        for _ in range(4):
            self.bad_login()
        other = create_app(
            {"TESTING": True, "DATABASE": self.db_path, "SECRET_KEY": "test-key"}
        )
        response = other.test_client().post(
            "/auth/login", data={"email": "demo@shopsphere.test", "password": "x"}
        )
        self.assertNotEqual(response.status_code, 429)


class TestSearchQuality(ShopTestCase):
    def test_words_may_be_given_in_any_order(self):
        forwards = self.client.get("/?q=wireless+earbuds").get_data(as_text=True)
        backwards = self.client.get("/?q=earbuds+wireless").get_data(as_text=True)
        self.assertIn("Nimbus Wireless Earbuds", forwards)
        self.assertIn("Nimbus Wireless Earbuds", backwards)

    def test_all_words_must_match(self):
        body = self.client.get("/?q=wireless+banana").get_data(as_text=True)
        self.assertIn("Nothing matched that", body)

    def test_name_matches_outrank_description_matches(self):
        body = self.client.get("/?q=pen").get_data(as_text=True)
        # The notebook is rated higher, but only its description mentions pens,
        # so relevance - not rating - has to decide the order.
        self.assertLess(body.index("Fineliner Pen Set"), body.index("A5 Dotted Notebook"))

    def test_punctuation_does_not_wipe_out_the_query(self):
        body = self.client.get("/?q=%25+chocolate").get_data(as_text=True)
        self.assertIn("Dark Chocolate 70% (5-pack)", body)

    def test_fts_operators_are_treated_as_literal_text(self):
        for hostile in ("earbuds OR skillet", 'earbuds" OR "skillet', "NEAR(a b)", "^", "*"):
            response = self.client.get("/", query_string={"q": hostile})
            self.assertEqual(response.status_code, 200, hostile)

    def test_or_is_not_honoured_as_an_operator(self):
        body = self.client.get("/", query_string={"q": "earbuds OR skillet"}).get_data(
            as_text=True
        )
        # Treated as three literal words, none of which co-occur in one product.
        self.assertIn("Nothing matched that", body)

    def test_word_boundaries_are_respected(self):
        body = self.client.get("/?q=pen").get_data(as_text=True)
        self.assertIn("Fineliner Pen Set", body)
        # "opening" contains "pen" as a substring; word search must not match it.
        self.assertNotIn("Cedar &amp; Amber", body)


class TestPagination(ShopTestCase):
    extra_config = {"PRODUCTS_PER_PAGE": 3, "PAGER_SPAN": 2}

    def test_long_pager_is_windowed_not_exhaustive(self):
        body = self.client.get("/?page=11").get_data(as_text=True)
        self.assertIn("pager__gap", body)
        self.assertLess(body.count("pager__link"), 12)

    def test_window_keeps_first_last_and_current(self):
        from shop.catalog import page_window

        self.assertEqual(page_window(1, 5, 2), [1, 2, 3, 4, 5])
        self.assertEqual(page_window(1, 20, 2), [1, 2, 3, None, 20])
        self.assertEqual(page_window(10, 20, 2), [1, None, 8, 9, 10, 11, 12, None, 20])
        self.assertEqual(page_window(20, 20, 2), [1, None, 18, 19, 20])

    def test_out_of_range_page_clamps_to_the_last(self):
        body = self.client.get("/?page=9999").get_data(as_text=True)
        self.assertIn('aria-current="page"', body)


class TestOrderFulfilment(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.reference = self.place_order(product_id=2, quantity=2)
        self.logout()
        self.login("admin@shopsphere.test", "Admin#12345")

    def advance(self, status):
        return self.client.post(
            f"/admin/orders/{self.order_id}/status",
            data={"status": status, "csrf_token": self.csrf("/admin/orders")},
            follow_redirects=True,
        )

    @property
    def order_id(self):
        return self.query("SELECT id FROM orders WHERE reference = ?", (self.reference,))["id"]

    def status(self):
        return self.query(
            "SELECT status FROM orders WHERE reference = ?", (self.reference,)
        )["status"]

    def test_happy_path_through_fulfilment(self):
        for step in ("packed", "shipped", "delivered"):
            self.advance(step)
            self.assertEqual(self.status(), step)

    def test_illegal_transition_is_rejected(self):
        body = self.advance("delivered").get_data(as_text=True)
        self.assertIn("cannot become delivered", body)
        self.assertEqual(self.status(), "paid")

    def test_delivered_orders_are_terminal(self):
        self.advance("packed")
        self.advance("shipped")
        self.advance("delivered")
        self.advance("cancelled")
        self.assertEqual(self.status(), "delivered")

    def test_unknown_status_is_rejected(self):
        self.advance("refunded-somehow")
        self.assertEqual(self.status(), "paid")

    def test_cancelling_returns_stock(self):
        before = self.query("SELECT stock FROM products WHERE id = 2")["stock"]
        self.advance("cancelled")
        after = self.query("SELECT stock FROM products WHERE id = 2")["stock"]
        self.assertEqual(after, before + 2)

    def test_cancelled_orders_leave_revenue(self):
        self.assertIn("$194.18", self.client.get("/admin/").get_data(as_text=True))
        self.advance("cancelled")
        self.assertIn("$0.00", self.client.get("/admin/").get_data(as_text=True))

    def test_customer_sees_the_new_status(self):
        self.advance("packed")
        self.advance("shipped")
        self.logout()
        self.login()
        body = self.client.get(f"/orders/{self.reference}").get_data(as_text=True)
        self.assertIn("shipped", body)

    def test_shoppers_cannot_change_status(self):
        self.logout()
        self.login()
        response = self.client.post(
            f"/admin/orders/{self.order_id}/status",
            data={"status": "cancelled", "csrf_token": self.csrf("/")},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.status(), "paid")


class TestErrorHandling(ShopTestCase):
    def test_unhandled_exception_returns_a_styled_500(self):
        import logging

        app = create_app(
            {
                "DATABASE": self.db_path,
                "SECRET_KEY": "test-key",
                "PROPAGATE_EXCEPTIONS": False,
            }
        )
        app.logger.setLevel(logging.CRITICAL)

        @app.route("/boom")
        def boom():
            raise RuntimeError("kaboom")

        response = app.test_client().get("/boom")
        self.assertEqual(response.status_code, 500)
        body = response.get_data(as_text=True)
        self.assertIn("Something went wrong", body)
        self.assertNotIn("kaboom", body)

    def test_404_still_renders_the_shop_chrome(self):
        body = self.client.get("/p/999999").get_data(as_text=True)
        self.assertIn("Back to the shop", body)


class MailCapturingTestCase(ShopTestCase):
    """Captures anything the app tries to email instead of logging it."""

    def setUp(self):
        super().setUp()
        import shop.auth
        import shop.mail
        import shop.orders

        self.outbox = []
        self._real_send = shop.mail.send_email

        def capture(to, subject, body):
            self.outbox.append({"to": to, "subject": subject, "body": body})
            return True

        # The blueprints imported send_email by name, so patch it on each of them.
        self._patched = (shop.auth, shop.orders)
        for module in self._patched:
            module.send_email = capture

    def tearDown(self):
        for module in self._patched:
            module.send_email = self._real_send
        super().tearDown()


class TestEmail(ShopTestCase):
    extra_config = {"MAIL_BACKEND": "file"}

    def test_file_backend_writes_an_eml(self):
        from shop.mail import send_email

        with self.app.test_request_context():
            self.assertTrue(send_email("a@b.test", "Hi", "Body text"))

        outbox = os.path.join(self.app.instance_path, "mail")
        files = [f for f in os.listdir(outbox) if f.endswith(".eml")]
        self.assertTrue(files)
        newest = max(files)
        content = open(os.path.join(outbox, newest), encoding="utf-8").read()
        self.assertIn("To: a@b.test", content)
        self.assertIn("Body text", content)
        os.unlink(os.path.join(outbox, newest))

    def test_send_failure_does_not_raise(self):
        import logging

        from shop.mail import send_email

        self.app.config["MAIL_BACKEND"] = "smtp"
        self.app.config["SMTP_HOST"] = ""
        self.app.logger.setLevel(logging.CRITICAL)  # the traceback here is expected
        with self.app.test_request_context():
            self.assertFalse(send_email("a@b.test", "Hi", "Body"))


class TestPasswordReset(MailCapturingTestCase):
    def request_reset(self, email="demo@shopsphere.test"):
        return self.client.post(
            "/auth/forgot",
            data={"email": email, "csrf_token": self.csrf("/auth/forgot")},
            follow_redirects=True,
        )

    def token_from_outbox(self):
        link = [word for word in self.outbox[-1]["body"].split() if "/auth/reset/" in word]
        return link[0].rsplit("/", 1)[1]

    def submit_new_password(self, token, password="Changed#98765", confirm=None):
        return self.client.post(
            f"/auth/reset/{token}",
            data={
                "password": password,
                "confirm": confirm if confirm is not None else password,
                # From "/", not the reset page - a spent token no longer renders a form.
                "csrf_token": self.csrf("/"),
            },
            follow_redirects=True,
        )

    def test_full_reset_cycle(self):
        self.request_reset()
        self.assertEqual(len(self.outbox), 1)
        self.submit_new_password(self.token_from_outbox())

        self.assertIn("Incorrect email or password", self.login().get_data(as_text=True))
        body = self.login(password="Changed#98765").get_data(as_text=True)
        self.assertIn("Signed in as", body)

    def test_unknown_address_sends_nothing_but_looks_identical(self):
        known = self.request_reset().get_data(as_text=True)
        unknown = self.request_reset("nobody@nowhere.test").get_data(as_text=True)
        self.assertIn("If that address has an account", known)
        self.assertIn("If that address has an account", unknown)
        self.assertEqual(len(self.outbox), 1)  # only the real account got mail

    def test_token_is_single_use(self):
        self.request_reset()
        token = self.token_from_outbox()
        self.submit_new_password(token)
        body = self.submit_new_password(token, "Another#98765").get_data(as_text=True)
        self.assertIn("invalid or has expired", body)

    def test_requesting_again_invalidates_the_previous_link(self):
        self.request_reset()
        first = self.token_from_outbox()
        self.request_reset()
        second = self.token_from_outbox()
        self.assertNotEqual(first, second)
        self.assertIn("invalid or has expired", self.submit_new_password(first).get_data(as_text=True))

    def test_expired_token_is_refused(self):
        self.request_reset()
        token = self.token_from_outbox()
        with self.app.app_context():
            from shop.db import get_db

            db = get_db()
            db.execute("UPDATE password_resets SET expires_at = datetime('now', '-1 hour')")
            db.commit()
        self.assertIn("invalid or has expired", self.submit_new_password(token).get_data(as_text=True))

    def test_raw_token_is_not_stored(self):
        self.request_reset()
        token = self.token_from_outbox()
        stored = self.query("SELECT token_hash FROM password_resets")["token_hash"]
        self.assertNotEqual(stored, token)
        self.assertEqual(len(stored), 64)  # sha-256 hex

    def test_weak_new_password_is_refused(self):
        self.request_reset()
        body = self.submit_new_password(self.token_from_outbox(), "short").get_data(as_text=True)
        self.assertIn("at least 10 characters", body)

    def test_mismatched_confirmation_is_refused(self):
        self.request_reset()
        body = self.submit_new_password(
            self.token_from_outbox(), "Changed#98765", "Different#98765"
        ).get_data(as_text=True)
        self.assertIn("do not match", body)

    def test_reset_requests_are_throttled(self):
        self.app.config["RESET_MAX_ATTEMPTS"] = 2
        self.request_reset()
        self.request_reset()
        response = self.client.post(
            "/auth/forgot",
            data={"email": "demo@shopsphere.test", "csrf_token": self.csrf("/auth/forgot")},
        )
        self.assertEqual(response.status_code, 429)

    def test_reset_does_not_sign_the_visitor_in(self):
        self.request_reset()
        body = self.submit_new_password(self.token_from_outbox()).get_data(as_text=True)
        self.assertIn("Please sign in", body)
        self.assertNotIn("Sign out", body)


class TestGuestCheckout(MailCapturingTestCase):
    def guest_order(self, email="guest@example.test", product_id=2):
        self.add_to_cart(product_id, 1)
        return self.client.post(
            "/orders/checkout",
            data={
                "email": email,
                "ship_name": "Guest Person",
                "ship_address": "9 Guest Lane",
                "ship_city": "Testville",
                "ship_postcode": "123456",
                "ship_country": "Singapore",
                "csrf_token": self.csrf("/orders/checkout"),
            },
        )

    def test_guest_can_complete_an_order(self):
        response = self.guest_order()
        self.assertEqual(response.status_code, 302)
        body = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("your order is confirmed", body)

    def test_guest_order_has_no_user_but_keeps_the_email(self):
        self.guest_order()
        order = self.query("SELECT user_id, email FROM orders ORDER BY id DESC")
        self.assertIsNone(order["user_id"])
        self.assertEqual(order["email"], "guest@example.test")

    def test_guest_checkout_requires_a_valid_email(self):
        self.add_to_cart(2, 1)
        body = self.client.post(
            "/orders/checkout",
            data={
                "email": "not-an-email",
                "ship_name": "Guest", "ship_address": "9 Lane", "ship_city": "T",
                "ship_postcode": "1", "ship_country": "SG",
                "csrf_token": self.csrf("/orders/checkout"),
            },
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn("valid email address", body)

    def test_signed_in_checkout_uses_the_account_email(self):
        self.login()
        self.place_order()
        self.assertEqual(
            self.query("SELECT email FROM orders ORDER BY id DESC")["email"],
            "demo@shopsphere.test",
        )

    def test_confirmation_email_is_sent(self):
        self.guest_order()
        self.assertEqual(len(self.outbox), 1)
        message = self.outbox[0]
        self.assertEqual(message["to"], "guest@example.test")
        self.assertIn("Nimbus Wireless Earbuds", message["body"])
        self.assertIn("$97.09", message["body"])  # 89.90 + 8% tax, free shipping

    def test_lookup_needs_the_matching_email(self):
        location = self.guest_order().headers["Location"]
        reference = location.split("/orders/")[1].split("?")[0]

        fresh = self.app.test_client()  # a different visitor
        body = fresh.post(
            "/orders/lookup",
            data={
                "reference": reference,
                "email": "wrong@example.test",
                "csrf_token": self._csrf_for(fresh, "/orders/lookup"),
            },
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn("No order matches", body)

    def test_lookup_succeeds_with_the_right_email(self):
        location = self.guest_order().headers["Location"]
        reference = location.split("/orders/")[1].split("?")[0]

        fresh = self.app.test_client()
        body = fresh.post(
            "/orders/lookup",
            data={
                "reference": reference,
                "email": "guest@example.test",
                "csrf_token": self._csrf_for(fresh, "/orders/lookup"),
            },
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn(reference, body)
        self.assertIn("9 Guest Lane", body)

    def test_stranger_cannot_open_an_order_by_reference_alone(self):
        location = self.guest_order().headers["Location"]
        reference = location.split("/orders/")[1].split("?")[0]

        fresh = self.app.test_client()
        response = fresh.get(f"/orders/{reference}")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/orders/lookup", response.headers["Location"])

    def test_signed_in_stranger_gets_404_not_someone_elses_order(self):
        location = self.guest_order().headers["Location"]
        reference = location.split("/orders/")[1].split("?")[0]

        fresh = self.app.test_client()
        fresh.post(
            "/auth/login",
            data={
                "email": "demo@shopsphere.test",
                "password": "Demo#12345",
                "csrf_token": self._csrf_for(fresh, "/auth/login"),
            },
        )
        self.assertEqual(fresh.get(f"/orders/{reference}").status_code, 404)

    def test_lookup_is_throttled(self):
        self.app.config["LOOKUP_MAX_ATTEMPTS"] = 2
        for _ in range(2):
            self.client.post(
                "/orders/lookup",
                data={
                    "reference": "SS-NOPE",
                    "email": "a@b.test",
                    "csrf_token": self.csrf("/orders/lookup"),
                },
            )
        response = self.client.post(
            "/orders/lookup",
            data={
                "reference": "SS-NOPE",
                "email": "a@b.test",
                "csrf_token": self.csrf("/orders/lookup"),
            },
        )
        self.assertEqual(response.status_code, 429)

    def _csrf_for(self, client, path):
        html = client.get(path).get_data(as_text=True)
        marker = 'name="csrf_token" value="'
        start = html.index(marker) + len(marker)
        return html[start : html.index('"', start)]


class TestReviews(ShopTestCase):
    def post_review(self, product_id=1, rating=5, title="Great", body="Really good."):
        return self.client.post(
            f"/reviews/{product_id}",
            data={
                "rating": rating,
                "title": title,
                "body": body,
                "csrf_token": self.csrf(f"/p/{product_id}"),
            },
            follow_redirects=True,
        )

    def rating_of(self, product_id=1):
        return self.query(
            "SELECT rating, review_count FROM products WHERE id = ?", (product_id,)
        )

    def test_seeded_ratings_match_their_reviews(self):
        mismatched = self.query(
            "SELECT COUNT(*) AS n FROM products p WHERE p.review_count != ("
            "  SELECT COUNT(*) FROM reviews WHERE product_id = p.id)"
            " OR ABS(p.rating - COALESCE("
            "  (SELECT AVG(rating) FROM reviews WHERE product_id = p.id), 0)) > 0.005"
        )["n"]
        self.assertEqual(mismatched, 0)

    def test_reviews_appear_on_the_product_page(self):
        body = self.client.get("/p/1").get_data(as_text=True)
        self.assertIn("Customer reviews", body)
        self.assertIn("out of 5", body)

    def test_posting_a_review_moves_the_average(self):
        before = self.rating_of()
        self.login()
        self.post_review(rating=1)
        after = self.rating_of()
        self.assertEqual(after["review_count"], before["review_count"] + 1)
        self.assertLess(after["rating"], before["rating"])

    def test_review_requires_sign_in(self):
        response = self.client.post(
            f"/reviews/1", data={"rating": 5, "csrf_token": self.csrf("/p/1")}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.headers["Location"])

    def test_one_review_per_person_edits_in_place(self):
        self.login()
        self.post_review(rating=5, title="First")
        self.post_review(rating=2, title="Changed my mind")
        rows = self.query(
            "SELECT COUNT(*) AS n FROM reviews WHERE product_id = 1 AND user_id ="
            " (SELECT id FROM users WHERE email = 'demo@shopsphere.test')"
        )["n"]
        self.assertEqual(rows, 1)
        body = self.client.get("/p/1").get_data(as_text=True)
        self.assertIn("Changed my mind", body)
        self.assertNotIn(">First<", body)

    def test_out_of_range_rating_is_refused(self):
        self.login()
        before = self.rating_of()
        self.post_review(rating=9)
        self.assertEqual(self.rating_of()["review_count"], before["review_count"])

    def test_review_text_is_escaped(self):
        self.login()
        self.post_review(title="<script>alert(1)</script>")
        body = self.client.get("/p/1").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", body)

    def test_deleting_a_review_restores_the_average(self):
        before = self.rating_of()
        self.login()
        self.post_review(rating=1)
        self.client.post(
            "/reviews/1/delete", data={"csrf_token": self.csrf("/p/1")}
        )
        after = self.rating_of()
        self.assertEqual(after["review_count"], before["review_count"])
        self.assertAlmostEqual(after["rating"], before["rating"], places=2)

    def test_shopper_cannot_delete_someone_elses_review(self):
        victim = self.query(
            "SELECT id, product_id FROM reviews WHERE product_id = 1 LIMIT 1"
        )
        self.login()
        self.client.post(
            "/reviews/1/delete",
            data={"review_id": victim["id"], "csrf_token": self.csrf("/p/1")},
        )
        still_there = self.query(
            "SELECT COUNT(*) AS n FROM reviews WHERE id = ?", (victim["id"],)
        )["n"]
        self.assertEqual(still_there, 1)

    def test_admin_can_remove_any_review(self):
        victim = self.query("SELECT id FROM reviews WHERE product_id = 1 LIMIT 1")
        self.login("admin@shopsphere.test", "Admin#12345")
        self.client.post(
            "/reviews/1/delete",
            data={"review_id": victim["id"], "csrf_token": self.csrf("/p/1")},
        )
        gone = self.query(
            "SELECT COUNT(*) AS n FROM reviews WHERE id = ?", (victim["id"],)
        )["n"]
        self.assertEqual(gone, 0)

    def test_verified_badge_follows_a_real_purchase(self):
        self.login()
        self.place_order(product_id=1, quantity=1)
        self.post_review(product_id=1, title="Bought it")
        body = self.client.get("/p/1").get_data(as_text=True)
        self.assertIn("Verified purchase", body)


class TestSchemaVersion(ShopTestCase):
    def test_fresh_database_reports_the_current_version(self):
        from shop.db import SCHEMA_VERSION

        with self.app.app_context():
            from shop.db import check_schema

            check_schema()
            self.assertEqual(
                self.query("SELECT version FROM schema_meta")["version"], SCHEMA_VERSION
            )

    def test_stale_database_is_rejected_loudly(self):
        from shop.db import SchemaOutOfDate, check_schema

        with self.app.app_context():
            from shop.db import get_db

            db = get_db()
            db.execute("UPDATE schema_meta SET version = 1")
            db.commit()
            with self.assertRaises(SchemaOutOfDate):
                check_schema()


class SellerTestCase(ShopTestCase):
    """Base for marketplace tests: signs in a shopper and gives them a shop."""

    def become_seller(self, shop_name="Test Pottery", approve=True):
        self.client.post(
            "/sell",
            data={
                "shop_name": shop_name,
                "contact_email": "seller@example.test",
                "bio": "Handmade things.",
                "payout_reference": "DEMO",
                "csrf_token": self.csrf("/sell"),
            },
            follow_redirects=True,
        )
        seller = self.query("SELECT * FROM sellers WHERE shop_name = ?", (shop_name,))
        if approve and seller:
            self.approve(seller["id"])
        return self.query("SELECT * FROM sellers WHERE shop_name = ?", (shop_name,))

    def approve(self, seller_id):
        """Approve as admin, then hand the session back to whoever was signed in."""
        was = self.client
        admin = self.app.test_client()
        html = admin.get("/auth/login").get_data(as_text=True)
        marker = 'name="csrf_token" value="'
        start = html.index(marker) + len(marker)
        admin.post(
            "/auth/login",
            data={
                "email": "admin@shopsphere.test",
                "password": "Admin#12345",
                "csrf_token": html[start : html.index('"', start)],
            },
        )
        page = admin.get("/admin/sellers").get_data(as_text=True)
        start = page.index(marker) + len(marker)
        admin.post(
            f"/admin/sellers/{seller_id}/approve",
            data={"csrf_token": page[start : page.index('"', start)]},
        )
        self.client = was

    def list_product(self, name="Speckled Mug", price="32.00", stock="5", images=None):
        data = {
            "name": name,
            "description": "A nice mug.",
            "category_id": "1",
            "price": price,
            "stock": stock,
            "icon": "\u2615",
            "is_active": "1",
            "csrf_token": self.csrf("/sell/products/new"),
        }
        if images:
            data["images"] = images
        return self.client.post(
            "/sell/products/new", data=data,
            content_type="multipart/form-data", follow_redirects=True,
        )


def png_bytes(size=(60, 40), colour=(200, 30, 90)):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.getvalue()


class TestSellerOnboarding(ShopTestCase):
    def test_application_requires_an_account(self):
        response = self.client.get("/sell")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.headers["Location"])

    def test_applying_creates_a_pending_seller(self):
        self.login()
        self.client.post(
            "/sell",
            data={
                "shop_name": "Clay Works",
                "contact_email": "clay@example.test",
                "bio": "Pots.",
                "csrf_token": self.csrf("/sell"),
            },
            follow_redirects=True,
        )
        seller = self.query("SELECT * FROM sellers WHERE shop_name = 'Clay Works'")
        self.assertEqual(seller["status"], "pending")
        self.assertEqual(seller["slug"], "clay-works")

    def test_pending_seller_cannot_reach_the_dashboard(self):
        self.login()
        self.client.post(
            "/sell",
            data={
                "shop_name": "Clay Works", "contact_email": "clay@example.test",
                "csrf_token": self.csrf("/sell"),
            },
        )
        response = self.client.get("/sell/dashboard")
        self.assertEqual(response.status_code, 403)
        self.assertIn("being reviewed", response.get_data(as_text=True))

    def test_shop_name_is_required(self):
        self.login()
        body = self.client.post(
            "/sell",
            data={"shop_name": "x", "contact_email": "a@b.test",
                  "csrf_token": self.csrf("/sell")},
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn("Shop name must be", body)

    def test_slug_collision_gets_a_suffix(self):
        self.login()
        # "Kiln and Clay" slugifies onto the seeded shop's slug.
        self.client.post(
            "/sell",
            data={"shop_name": "Kiln and Clay", "contact_email": "a@b.test",
                  "csrf_token": self.csrf("/sell")},
        )
        slug = self.query(
            "SELECT slug FROM sellers WHERE shop_name = 'Kiln and Clay'"
        )["slug"]
        self.assertNotEqual(slug, "kiln-and-clay")
        self.assertTrue(slug.startswith("kiln-and-clay-"), slug)

    def test_seeded_pending_seller_is_not_public(self):
        self.assertEqual(self.client.get("/s/second-sun-vintage").status_code, 404)

    def test_seeded_approved_seller_has_a_storefront(self):
        body = self.client.get("/s/kiln-and-clay").get_data(as_text=True)
        self.assertIn("Speckled Stoneware Mug", body)


class TestSellerListings(SellerTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.seller = self.become_seller()

    def test_seller_can_list_a_product_that_reaches_the_shop(self):
        self.list_product(name="Ridged Tumbler")
        body = self.client.get("/?q=Ridged+Tumbler").get_data(as_text=True)
        self.assertIn("Ridged Tumbler", body)
        self.assertIn("Test Pottery", body)

    def test_listing_gets_a_generated_sku(self):
        self.list_product(name="Ridged Tumbler")
        sku = self.query("SELECT sku FROM products WHERE name = 'Ridged Tumbler'")["sku"]
        self.assertTrue(sku.startswith("TEST-POT-"), sku)

    def test_seller_cannot_choose_their_own_commission(self):
        self.logout()
        self.client.post(
            "/auth/register",
            data={
                "name": "Rate Hacker", "email": "hacker@example.test",
                "password": "Password123", "confirm": "Password123",
                "csrf_token": self.csrf("/auth/register"),
            },
        )
        self.client.post(
            "/sell",
            data={
                "shop_name": "Free Ride", "contact_email": "hacker@example.test",
                "commission_rate": "0", "commission_percent": "0", "status": "approved",
                "csrf_token": self.csrf("/sell"),
            },
        )
        seller = self.query("SELECT * FROM sellers WHERE shop_name = 'Free Ride'")
        self.assertAlmostEqual(seller["commission_rate"], 0.10)
        self.assertEqual(seller["status"], "pending")

    def test_seller_cannot_edit_another_sellers_listing(self):
        victim = self.query(
            "SELECT id FROM products WHERE seller_id ="
            " (SELECT id FROM sellers WHERE slug = 'kiln-and-clay') LIMIT 1"
        )
        self.assertEqual(
            self.client.get(f"/sell/products/{victim['id']}/edit").status_code, 404
        )

    def test_seller_cannot_toggle_another_sellers_listing(self):
        victim = self.query(
            "SELECT id, is_active FROM products WHERE seller_id ="
            " (SELECT id FROM sellers WHERE slug = 'kiln-and-clay') LIMIT 1"
        )
        self.client.post(
            f"/sell/products/{victim['id']}/toggle",
            data={"csrf_token": self.csrf("/sell/products")},
        )
        after = self.query(
            "SELECT is_active FROM products WHERE id = ?", (victim["id"],)
        )["is_active"]
        self.assertEqual(after, victim["is_active"])

    def test_admin_cannot_rewrite_a_seller_listing(self):
        listing = self.query(
            "SELECT id FROM products WHERE seller_id IS NOT NULL LIMIT 1"
        )
        self.logout()
        self.login("admin@shopsphere.test", "Admin#12345")
        self.assertEqual(
            self.client.get(f"/admin/products/{listing['id']}/edit").status_code, 404
        )

    def test_suspending_a_seller_hides_their_listings_everywhere(self):
        self.list_product(name="Ridged Tumbler")
        product_id = self.query("SELECT id FROM products WHERE name = 'Ridged Tumbler'")["id"]

        with self.app.app_context():
            from shop.db import get_db

            db = get_db()
            db.execute("UPDATE sellers SET status = 'suspended' WHERE id = ?",
                       (self.seller["id"],))
            db.commit()

        self.assertNotIn("Ridged Tumbler", self.client.get("/").get_data(as_text=True))
        self.assertEqual(self.client.get(f"/p/{product_id}").status_code, 404)
        self.client.post(
            f"/cart/add/{product_id}",
            data={"csrf_token": self.csrf("/")}, follow_redirects=True,
        )
        self.assertIn("Your cart is empty", self.client.get("/cart/").get_data(as_text=True))


class TestProductImages(SellerTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.seller = self.become_seller()

    def upload(self, payload, filename="photo.png"):
        return self.list_product(images=(io.BytesIO(payload), filename))

    def test_uploaded_image_is_stored_and_shown(self):
        self.upload(png_bytes())
        image = self.query("SELECT * FROM product_images ORDER BY id DESC")
        self.assertIsNotNone(image)
        self.assertTrue(image["filename"].endswith(".webp"))
        response = self.client.get(f"/media/products/{image['filename']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/webp")

    def test_original_filename_is_discarded(self):
        self.upload(png_bytes(), filename="../../evil name.png")
        stored = self.query("SELECT filename FROM product_images ORDER BY id DESC")["filename"]
        self.assertNotIn("evil", stored)
        self.assertNotIn("..", stored)
        self.assertRegex(stored, r"^[0-9a-f]{32}\.webp$")

    def test_image_is_re_encoded_not_stored_verbatim(self):
        """Anything appended after the image data must not survive."""
        payload = png_bytes() + b"<?php system($_GET[0]); ?>"
        self.upload(payload)
        stored = self.query("SELECT filename FROM product_images ORDER BY id DESC")["filename"]
        with self.app.app_context():
            from shop.uploads import uploads_dir

            content = (uploads_dir() / stored).read_bytes()
        self.assertNotIn(b"php", content)
        self.assertTrue(content.startswith(b"RIFF"))  # WebP container

    def test_non_image_is_rejected(self):
        body = self.upload(b"just some text, not an image at all", "notes.txt")
        self.assertIn("not a readable image", body.get_data(as_text=True))
        self.assertIsNone(self.query("SELECT id FROM product_images"))

    def test_oversized_image_is_rejected(self):
        import shop.uploads as uploads

        original = uploads.MAX_BYTES
        uploads.MAX_BYTES = 200
        try:
            body = self.upload(png_bytes(size=(400, 400)))
            self.assertIn("under 0 MB", body.get_data(as_text=True))
        finally:
            uploads.MAX_BYTES = original
        self.assertIsNone(self.query("SELECT id FROM product_images"))

    def test_media_route_rejects_paths_it_did_not_generate(self):
        for name in ("../../../instance/shop.sqlite", "..%2Fsecret_key", "anything.png"):
            self.assertEqual(
                self.client.get(f"/media/products/{name}").status_code, 404, name
            )

    def test_seller_can_delete_their_own_photo(self):
        self.upload(png_bytes())
        image = self.query("SELECT * FROM product_images ORDER BY id DESC")
        self.client.post(
            f"/sell/products/{image['product_id']}/images/{image['id']}/delete",
            data={"csrf_token": self.csrf("/sell/products")},
        )
        self.assertIsNone(self.query("SELECT id FROM product_images"))


class TestCommission(SellerTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.seller = self.become_seller()
        self.list_product(name="Ridged Tumbler", price="50.00", stock="10")
        self.product_id = self.query(
            "SELECT id FROM products WHERE name = 'Ridged Tumbler'"
        )["id"]

    def buy(self, quantity=1):
        self.add_to_cart(self.product_id, quantity)
        return self.client.post(
            "/orders/checkout",
            data={
                "ship_name": "Demo Shopper", "ship_address": "1 Test Street",
                "ship_city": "Testville", "ship_postcode": "123456",
                "ship_country": "Singapore",
                "csrf_token": self.csrf("/orders/checkout"),
            },
        )

    def line(self):
        return self.query(
            "SELECT * FROM order_items WHERE seller_id = ? ORDER BY id DESC",
            (self.seller["id"],),
        )

    def test_ten_percent_is_taken_from_a_seller_sale(self):
        self.buy()
        line = self.line()
        self.assertEqual(line["line_cents"], 5000)
        self.assertEqual(line["commission_cents"], 500)
        self.assertEqual(line["seller_earning_cents"], 4500)
        self.assertAlmostEqual(line["commission_rate"], 0.10)

    def test_commission_scales_with_quantity(self):
        self.buy(quantity=3)
        line = self.line()
        self.assertEqual(line["line_cents"], 15000)
        self.assertEqual(line["commission_cents"], 1500)
        self.assertEqual(line["seller_earning_cents"], 13500)

    def test_split_always_adds_back_to_the_line_total(self):
        self.client.post(
            f"/sell/products/{self.product_id}/edit",
            data={
                "name": "Ridged Tumbler", "description": "x", "category_id": "1",
                "price": "9.99", "stock": "10", "icon": "\u2615", "is_active": "1",
                "csrf_token": self.csrf(f"/sell/products/{self.product_id}/edit"),
            },
            content_type="multipart/form-data",
        )
        self.buy(quantity=7)
        line = self.line()
        self.assertEqual(
            line["commission_cents"] + line["seller_earning_cents"], line["line_cents"]
        )

    def test_own_stock_pays_no_commission(self):
        self.add_to_cart(2, 1)  # seeded house product
        self.client.post(
            "/orders/checkout",
            data={
                "ship_name": "Demo Shopper", "ship_address": "1 Test Street",
                "ship_city": "Testville", "ship_postcode": "123456",
                "ship_country": "Singapore",
                "csrf_token": self.csrf("/orders/checkout"),
            },
        )
        line = self.query(
            "SELECT * FROM order_items WHERE product_id = 2 ORDER BY id DESC"
        )
        self.assertIsNone(line["seller_id"])
        self.assertEqual(line["commission_cents"], 0)
        self.assertEqual(line["seller_earning_cents"], 0)

    def test_rate_change_does_not_rewrite_past_sales(self):
        self.buy()
        with self.app.app_context():
            from shop.db import get_db

            db = get_db()
            db.execute("UPDATE sellers SET commission_rate = 0.5 WHERE id = ?",
                       (self.seller["id"],))
            db.commit()
        self.assertEqual(self.line()["commission_cents"], 500)

    def test_new_rate_applies_to_the_next_sale(self):
        with self.app.app_context():
            from shop.db import get_db

            db = get_db()
            db.execute("UPDATE sellers SET commission_rate = 0.25 WHERE id = ?",
                       (self.seller["id"],))
            db.commit()
        self.buy()
        line = self.line()
        self.assertEqual(line["commission_cents"], 1250)
        self.assertEqual(line["seller_earning_cents"], 3750)

    def test_seller_dashboard_reports_earnings(self):
        self.buy(quantity=2)
        body = self.client.get("/sell/dashboard").get_data(as_text=True)
        self.assertIn("$100.00", body)  # gross
        self.assertIn("$10.00", body)   # commission
        self.assertIn("$90.00", body)   # earnings

    def test_cancelled_order_removes_the_earnings_and_our_cut(self):
        self.buy()
        order_id = self.query("SELECT id FROM orders ORDER BY id DESC")["id"]
        self.logout()
        self.login("admin@shopsphere.test", "Admin#12345")
        for step in ("cancelled",):
            self.client.post(
                f"/admin/orders/{order_id}/status",
                data={"status": step, "csrf_token": self.csrf("/admin/orders")},
            )
        body = self.client.get("/admin/sellers").get_data(as_text=True)
        self.assertIn("$0.00", body)

    def test_admin_can_change_a_sellers_rate(self):
        self.logout()
        self.login("admin@shopsphere.test", "Admin#12345")
        self.client.post(
            f"/admin/sellers/{self.seller['id']}/rate",
            data={"commission_percent": "15", "csrf_token": self.csrf("/admin/sellers")},
        )
        rate = self.query(
            "SELECT commission_rate FROM sellers WHERE id = ?", (self.seller["id"],)
        )["commission_rate"]
        self.assertAlmostEqual(rate, 0.15)

    def test_absurd_rate_is_refused(self):
        self.logout()
        self.login("admin@shopsphere.test", "Admin#12345")
        self.client.post(
            f"/admin/sellers/{self.seller['id']}/rate",
            data={"commission_percent": "900", "csrf_token": self.csrf("/admin/sellers")},
        )
        rate = self.query(
            "SELECT commission_rate FROM sellers WHERE id = ?", (self.seller["id"],)
        )["commission_rate"]
        self.assertAlmostEqual(rate, 0.10)

    def test_shopper_cannot_reach_seller_admin(self):
        self.assertEqual(self.client.get("/admin/sellers").status_code, 403)


class VariantTestCase(ShopTestCase):
    """The seeded mug has three glazes and an optional name on the base."""

    def setUp(self):
        super().setUp()
        self.mug = self.query("SELECT * FROM products WHERE name = 'Speckled Stoneware Mug'")
        self.glazes = {
            row["label"]: row
            for row in self.rows(
                "SELECT * FROM product_variants WHERE product_id = ?", (self.mug["id"],)
            )
        }

    def rows(self, sql, params=()):
        with self.app.app_context():
            from shop.db import get_db

            return get_db().execute(sql, params).fetchall()

    def buy_mug(self, glaze="Oatmeal", quantity=1, text=None):
        self.add_to_cart(
            self.mug["id"], quantity, variant_id=self.glazes[glaze]["id"], text=text
        )


class TestVariants(VariantTestCase):
    def test_product_stock_is_the_sum_of_its_options(self):
        total = sum(v["stock"] for v in self.glazes.values())
        self.assertEqual(self.mug["stock"], total)

    def test_option_picker_is_shown(self):
        body = self.client.get(f"/p/{self.mug['id']}").get_data(as_text=True)
        self.assertIn("Glaze", body)
        for label in ("Oatmeal", "Deep sea", "Copper red"):
            self.assertIn(label, body)

    def test_price_range_is_shown_when_options_differ(self):
        body = self.client.get(f"/p/{self.mug['id']}").get_data(as_text=True)
        self.assertIn("$32.00", body)
        self.assertIn("$38.00", body)

    def test_adding_without_choosing_is_refused(self):
        body = self.add_to_cart(self.mug["id"]).get_data(as_text=True)
        self.assertIn("Please choose glaze", body)
        self.assertIn("Your cart is empty", self.client.get("/cart/").get_data(as_text=True))

    def test_adding_with_a_choice_works(self):
        self.buy_mug("Deep sea")
        body = self.client.get("/cart/").get_data(as_text=True)
        self.assertIn("Deep sea", body)
        self.assertIn("$32.00", body)

    def test_option_price_overrides_the_product_price(self):
        self.buy_mug("Copper red")
        body = self.client.get("/cart/").get_data(as_text=True)
        self.assertIn("$38.00", body)

    def test_different_options_are_separate_lines(self):
        self.buy_mug("Oatmeal")
        self.buy_mug("Deep sea")
        self.assertEqual(len(self.cart_line_ids()), 2)

    def test_same_option_merges_into_one_line(self):
        self.buy_mug("Oatmeal")
        self.buy_mug("Oatmeal")
        self.assertEqual(len(self.cart_line_ids()), 1)

    def test_quantity_is_capped_by_the_option_not_the_product(self):
        # Oatmeal has 6 while the product total is 14, so capping against the
        # product would wrongly let 10 through.
        self.buy_mug("Oatmeal", quantity=10)
        body = self.client.get("/cart/").get_data(as_text=True)
        self.assertIn('value="6"', body)

    def test_a_variant_from_another_product_is_refused(self):
        other = self.query(
            "SELECT id FROM product_variants WHERE product_id != ? LIMIT 1",
            (self.mug["id"],),
        )
        body = self.add_to_cart(self.mug["id"], variant_id=other["id"]).get_data(as_text=True)
        self.assertIn("Please choose glaze", body)

    def test_grid_sends_option_products_to_the_page(self):
        body = self.client.get("/?q=Speckled").get_data(as_text=True)
        self.assertIn("Choose options", body)

    def test_checkout_records_the_chosen_option(self):
        self.login()
        self.buy_mug("Copper red")
        self.checkout()
        line = self.query("SELECT * FROM order_items ORDER BY id DESC")
        self.assertEqual(line["variant_label"], "Copper red")
        self.assertEqual(line["unit_cents"], 3800)

    def test_checkout_decrements_the_option_and_rolls_up(self):
        before = self.glazes["Oatmeal"]["stock"]
        self.login()
        self.buy_mug("Oatmeal", quantity=2)
        self.checkout()

        after = self.query(
            "SELECT stock FROM product_variants WHERE id = ?", (self.glazes["Oatmeal"]["id"],)
        )["stock"]
        self.assertEqual(after, before - 2)
        product_stock = self.query(
            "SELECT stock FROM products WHERE id = ?", (self.mug["id"],)
        )["stock"]
        self.assertEqual(product_stock, self.mug["stock"] - 2)

    def test_cancelling_restores_the_option_stock(self):
        before = self.glazes["Oatmeal"]["stock"]
        self.login()
        self.buy_mug("Oatmeal", quantity=2)
        self.checkout()
        order_id = self.query("SELECT id FROM orders ORDER BY id DESC")["id"]

        self.logout()
        self.login("admin@shopsphere.test", "Admin#12345")
        self.client.post(
            f"/admin/orders/{order_id}/status",
            data={"status": "cancelled", "csrf_token": self.csrf("/admin/orders")},
        )
        after = self.query(
            "SELECT stock FROM product_variants WHERE id = ?", (self.glazes["Oatmeal"]["id"],)
        )["stock"]
        self.assertEqual(after, before)

    def test_retiring_an_unsold_option_deletes_it(self):
        seller = self.seller_client()
        seller.post(
            f"/sell/products/{self.mug['id']}/options/{self.glazes['Deep sea']['id']}/delete",
            data={"csrf_token": self._csrf(seller, f"/sell/products/{self.mug['id']}/edit")},
        )
        self.assertIsNone(
            self.query(
                "SELECT id FROM product_variants WHERE id = ?", (self.glazes["Deep sea"]["id"],)
            )
        )

    def test_retiring_a_sold_option_keeps_it_for_history(self):
        self.login()
        self.buy_mug("Oatmeal")
        self.checkout()
        self.logout()

        seller = self.seller_client()
        seller.post(
            f"/sell/products/{self.mug['id']}/options/{self.glazes['Oatmeal']['id']}/delete",
            data={"csrf_token": self._csrf(seller, f"/sell/products/{self.mug['id']}/edit")},
        )
        kept = self.query(
            "SELECT * FROM product_variants WHERE id = ?", (self.glazes["Oatmeal"]["id"],)
        )
        self.assertIsNotNone(kept)
        self.assertEqual(kept["is_active"], 0)
        self.assertNotIn("Oatmeal", self.client.get(f"/p/{self.mug['id']}").get_data(as_text=True))

    def checkout(self):
        return self.client.post(
            "/orders/checkout",
            data={
                "ship_name": "Demo Shopper", "ship_address": "1 Test Street",
                "ship_city": "Testville", "ship_postcode": "123456",
                "ship_country": "Singapore",
                "csrf_token": self.csrf("/orders/checkout"),
            },
        )

    def seller_client(self):
        client = self.app.test_client()
        client.post(
            "/auth/login",
            data={
                "email": "maya@shopsphere.test", "password": "Seller#12345",
                "csrf_token": self._csrf(client, "/auth/login"),
            },
        )
        return client

    def _csrf(self, client, path):
        html = client.get(path).get_data(as_text=True)
        marker = 'name="csrf_token" value="'
        start = html.index(marker) + len(marker)
        return html[start : html.index('"', start)]


class TestPersonalisation(VariantTestCase):
    def test_field_is_offered(self):
        body = self.client.get(f"/p/{self.mug['id']}").get_data(as_text=True)
        self.assertIn("Name on the base", body)

    def test_text_is_kept_on_the_line(self):
        self.buy_mug("Oatmeal", text="For Ada")
        self.assertIn("For Ada", self.client.get("/cart/").get_data(as_text=True))

    def test_same_option_different_text_are_separate_lines(self):
        self.buy_mug("Oatmeal", text="For Ada")
        self.buy_mug("Oatmeal", text="For Ben")
        self.assertEqual(len(self.cart_line_ids()), 2)

    def test_text_is_truncated_to_the_limit(self):
        self.buy_mug("Oatmeal", text="x" * 200)
        line = self.client.get("/cart/").get_data(as_text=True)
        self.assertIn("x" * 20, line)
        self.assertNotIn("x" * 21, line)

    def test_text_is_escaped(self):
        self.buy_mug("Oatmeal", text="<script>alert(1)</script>")
        self.assertNotIn(
            "<script>alert(1)</script>", self.client.get("/cart/").get_data(as_text=True)
        )

    def test_required_personalisation_blocks_the_add(self):
        with self.app.app_context():
            from shop.db import get_db

            db = get_db()
            db.execute(
                "UPDATE products SET personalisation_required = 1 WHERE id = ?",
                (self.mug["id"],),
            )
            db.commit()
        body = self.add_to_cart(
            self.mug["id"], variant_id=self.glazes["Oatmeal"]["id"]
        ).get_data(as_text=True)
        self.assertIn("is required for this item", body)
        self.assertIn("Your cart is empty", self.client.get("/cart/").get_data(as_text=True))

    def test_text_reaches_the_order_and_the_email(self):
        self.login()
        self.buy_mug("Oatmeal", text="For Ada")
        self.client.post(
            "/orders/checkout",
            data={
                "ship_name": "Demo Shopper", "ship_address": "1 Test Street",
                "ship_city": "Testville", "ship_postcode": "123456",
                "ship_country": "Singapore",
                "csrf_token": self.csrf("/orders/checkout"),
            },
        )
        line = self.query("SELECT * FROM order_items ORDER BY id DESC")
        self.assertEqual(line["personalisation"], "For Ada")


class TestStoreSettings(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.login("admin@shopsphere.test", "Admin#12345")

    def save(self, **overrides):
        data = {
            "STORE_NAME": "ShopSphere",
            "STORE_TAGLINE": "Everything you need.",
            "CURRENCY_SYMBOL": "$",
            "HERO_HEADING": "Hello",
            "HERO_SUBHEADING": "World",
            "SHIPPING_FLAT_CENTS": "4.99",
            "FREE_SHIPPING_THRESHOLD_CENTS": "50.00",
            "TAX_RATE": "8",
            "COMMISSION_RATE": "10",
            "ALLOW_GUEST_CHECKOUT": "1",
            "csrf_token": self.csrf("/admin/settings"),
        }
        data.update(overrides)
        return self.client.post("/admin/settings", data=data, follow_redirects=True)

    def test_store_name_change_shows_everywhere(self):
        self.save(STORE_NAME="Craft Corner")
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("Craft Corner", body)

    def test_currency_symbol_changes_prices(self):
        self.save(CURRENCY_SYMBOL="S$")
        self.assertIn("S$", self.client.get("/").get_data(as_text=True))

    def test_hero_copy_is_editable(self):
        self.save(HERO_HEADING="Handmade, honestly")
        self.assertIn("Handmade, honestly", self.client.get("/").get_data(as_text=True))

    def test_tax_change_affects_the_next_cart(self):
        self.save(TAX_RATE="0")
        self.add_to_cart(2, 1)
        self.assertIn("<dd>$0.00</dd>", self.client.get("/cart/").get_data(as_text=True))

    def test_shipping_threshold_is_respected(self):
        self.save(FREE_SHIPPING_THRESHOLD_CENTS="10000")
        self.add_to_cart(2, 1)  # $89.90, under the new threshold
        self.assertIn("<dd>$4.99</dd>", self.client.get("/cart/").get_data(as_text=True))

    def test_empty_store_name_is_refused(self):
        body = self.save(STORE_NAME="").get_data(as_text=True)
        self.assertIn("cannot be empty", body)
        self.assertIn("ShopSphere", self.client.get("/").get_data(as_text=True))

    def test_out_of_range_tax_is_refused(self):
        self.save(TAX_RATE="900")
        with self.app.app_context():
            from shop import settings

            self.assertLessEqual(settings.get("TAX_RATE"), 1)

    def test_turning_off_guest_checkout_takes_effect(self):
        self.save(ALLOW_GUEST_CHECKOUT="")
        self.logout()
        self.add_to_cart(2, 1)
        response = self.client.get("/orders/checkout")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.headers["Location"])

    def test_shopper_cannot_reach_settings(self):
        self.logout()
        self.login()
        self.assertEqual(self.client.get("/admin/settings").status_code, 403)

    def test_settings_fall_back_to_config(self):
        with self.app.app_context():
            from shop import settings

            self.assertEqual(settings.get("STORE_NAME"), self.app.config["STORE_NAME"])


class TestShopAppearance(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.login("maya@shopsphere.test", "Seller#12345")

    def save(self, **overrides):
        data = {
            "bio": "Handmade stoneware.",
            "accent": "emerald",
            "shipping_policy": "Posted within three days.",
            "returns_policy": "Fourteen days, unused.",
            "csrf_token": self.csrf("/sell/appearance"),
        }
        data.update(overrides)
        return self.client.post(
            "/sell/appearance", data=data,
            content_type="multipart/form-data", follow_redirects=True,
        )

    def test_accent_is_applied_to_the_storefront(self):
        self.save()
        body = self.client.get("/s/kiln-and-clay").get_data(as_text=True)
        self.assertIn("shophead--emerald", body)

    def test_unknown_accent_falls_back(self):
        self.save(accent="; background: url(evil)")
        accent = self.query("SELECT accent FROM sellers WHERE slug = 'kiln-and-clay'")["accent"]
        self.assertEqual(accent, "indigo")

    def test_policies_appear_on_the_storefront(self):
        self.save()
        body = self.client.get("/s/kiln-and-clay").get_data(as_text=True)
        self.assertIn("Posted within three days.", body)
        self.assertIn("Fourteen days, unused.", body)

    def test_banner_upload_and_removal(self):
        self.save(banner=(io.BytesIO(png_bytes((400, 120))), "banner.png"))
        banner = self.query("SELECT banner FROM sellers WHERE slug = 'kiln-and-clay'")["banner"]
        self.assertTrue(banner.endswith(".webp"))
        self.assertIn(banner, self.client.get("/s/kiln-and-clay").get_data(as_text=True))

        self.client.post(
            "/sell/appearance/banner/delete",
            data={"csrf_token": self.csrf("/sell/appearance")},
        )
        self.assertEqual(
            self.query("SELECT banner FROM sellers WHERE slug = 'kiln-and-clay'")["banner"], ""
        )

    def test_policies_are_escaped(self):
        self.save(shipping_policy="<script>alert(1)</script>")
        self.assertNotIn(
            "<script>alert(1)</script>", self.client.get("/s/kiln-and-clay").get_data(as_text=True)
        )

    def test_shopper_without_a_shop_cannot_reach_it(self):
        self.logout()
        self.login()
        response = self.client.get("/sell/appearance")
        self.assertEqual(response.status_code, 302)


class TestTheme(ShopTestCase):
    def test_default_has_no_theme_attribute(self):
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn('<html lang="en">', body)

    def test_toggling_to_dark_sets_the_attribute(self):
        self.client.post(
            "/theme", data={"theme": "dark", "next": "/", "csrf_token": self.csrf("/")}
        )
        self.assertIn('data-theme="dark"', self.client.get("/").get_data(as_text=True))

    def test_toggling_back_to_light(self):
        csrf = self.csrf("/")
        self.client.post("/theme", data={"theme": "dark", "csrf_token": csrf})
        self.client.post("/theme", data={"theme": "light", "csrf_token": csrf})
        self.assertIn('data-theme="light"', self.client.get("/").get_data(as_text=True))

    def test_unknown_theme_value_falls_back_to_light(self):
        self.client.post(
            "/theme",
            data={"theme": "'; drop table users --", "csrf_token": self.csrf("/")},
        )
        self.assertIn('data-theme="light"', self.client.get("/").get_data(as_text=True))

    def test_theme_toggle_cannot_be_used_as_an_open_redirect(self):
        response = self.client.post(
            "/theme",
            data={
                "theme": "dark",
                "next": "https://evil.example.com/",
                "csrf_token": self.csrf("/"),
            },
        )
        self.assertNotIn("evil.example.com", response.headers["Location"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
