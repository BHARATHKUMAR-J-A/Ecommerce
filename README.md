# ShopSphere

A complete e-commerce marketplace: browse a catalogue across 10 categories, search
and filter it, fill a cart, check out, and manage stock from an admin area.
Independent sellers can apply, list their own products with photos, and the
platform takes a commission on every sale.

Flask + SQLite + server-rendered HTML. No Node, no build step, two dependencies.

## Run it

```powershell
git clone https://github.com/yongjunmun/ShopSphere.git
cd ShopSphere
pip install -r requirements.txt
python run.py
```

The first run creates the database, loads the demo catalogue and opens
<http://127.0.0.1:5000/>.

Demo logins. **These are seeded fixtures and the sign-in page displays them.**
Change or remove them before deploying this anywhere reachable — set
`SHOP_ADMIN_PASSWORD` and delete the note at the bottom of `templates/login.html`.

| Role | Email | Password |
| ------- | ----------------------- | -------------- |
| Shopper | `demo@shopsphere.test`  | `Demo#12345`   |
| Admin   | `admin@shopsphere.test` | `Admin#12345`  |
| Seller  | `maya@shopsphere.test`  | `Seller#12345` |

## Tests

```powershell
python -m unittest discover -s tests -t .
```

130 tests covering the catalogue, search, cart maths, checkout, stock accounting,
order fulfilment, reviews, password reset, seller onboarding, image uploads,
commission accounting and the security controls listed below. It takes about two
minutes — most of that is scrypt deliberately being slow.

If you already have a database from an older version, the app will refuse to start
and tell you so. Delete `instance/shop.sqlite` (or run `flask --app shop init-db`)
to rebuild it.

## The marketplace

Anyone with an account can apply at `/sell`. Applications are reviewed by hand in
the admin area; once approved, the seller gets a dashboard where they can list
products, upload photos, set their own stock and prices, and watch their earnings.
Each shop also gets a public storefront at `/s/<shop-name>`.

**The commission is 10% by default**, taken from the line total of every item sold.
On a $32 sale the platform keeps $3.20 and the seller keeps $28.80. Three things
make that trustworthy rather than approximate:

- The rate and both halves of the split are **written onto the order line at the
  moment of sale**. Changing a seller's rate later never rewrites what they have
  already earned.
- `commission_cents + seller_earning_cents` always equals `line_cents` exactly,
  because it is integer arithmetic on cents with the commission rounded and the
  seller given the remainder. There is a test that buys an awkward quantity of an
  awkwardly priced item to prove it.
- **Cancelled orders earn nothing.** Earnings are derived from order lines joined
  to live order status, so cancelling an order removes it from the seller's
  earnings and the platform's commission at once, and returns the stock.

Own-brand stock (`seller_id IS NULL`) carries no commission — that revenue is
already the platform's.

The admin can set a different rate per seller, suspend a shop (which hides its
listings everywhere immediately, without touching existing orders), and see gross,
commission and amount owed per seller.

## Photo uploads

Sellers upload up to five photos per listing. Uploaded files are never trusted and
never stored as sent: each one is decoded with Pillow, re-encoded to WebP under a
generated 32-character hex name, and written to `instance/uploads/` — outside the
static tree, served by a route that only accepts filenames matching that generated
pattern.

That combination means a file claiming to be a `.png` but containing a script is
rejected when it fails to decode; anything appended after the image data is
discarded by the re-encode; EXIF (including GPS coordinates from a phone camera) is
stripped; and a crafted filename cannot escape the upload folder. SVG is refused
outright because it is XML and can carry script.

## What it does

**Storefront** — home page with hero and category tiles, category pages, search,
price range and in-stock filters, five sort orders, windowed pagination, product
detail pages with related items, and live stock badges.

Search uses SQLite FTS5, so it matches whole words: "earbuds wireless" and "wireless
earbuds" agree, and "pen" no longer matches "opening". Every word must match, the
last one is treated as a prefix so results update as you type, and while sorting is
left on Featured a hit in the product name outranks one that only hit the description.

**Cart** — kept in the signed session so guests can shop before signing in, and it
survives the login. Quantities are clamped to real stock, subtotal/shipping/tax are
recalculated server-side on every render, free delivery over $50.

**Checkout** — open to guests (set `ALLOW_GUEST_CHECKOUT = False` to require an
account). Stock is decremented and the order written in one transaction, then a
confirmation email goes out. Order lines copy the name and price so history is not
rewritten when a product is later edited.

**Accounts** — registration, sign-in, order history, and password reset by emailed
one-time link.

**Order tracking** — signed-in customers see their orders; guests use the reference
plus the email address they checked out with.

**Reviews** — one per customer per product, editable in place. The star rating on
every product is the live average of its reviews, not a fixed number, and reviews
from someone who actually bought the item are badged "Verified purchase".

**Admin** — dashboard with revenue and low-stock lists, product create/edit/hide, and
an order queue. Orders move `paid → packed → shipped → delivered`, or get cancelled
from `paid`/`packed`; cancelling returns the items to stock and drops the order out
of the revenue figure. Illegal jumps are refused rather than silently applied.
Products are soft-deleted so past orders stay intact.

## Email

Nothing is sent anywhere by default. `MAIL_BACKEND` picks how messages are handled:

| Value | Behaviour |
| --- | --- |
| `console` (default) | Written to the server log, so reset links are visible while developing. |
| `file` | One `.eml` per message under `instance/mail/`. |
| `smtp` | A real send. Configure `SHOP_SMTP_HOST`, `SHOP_SMTP_PORT`, `SHOP_SMTP_USER`, `SHOP_SMTP_PASSWORD`, `SHOP_SMTP_STARTTLS`. |

Set it with `SHOP_MAIL_BACKEND`. Send failures are logged and swallowed — a shop that
cannot email should still be able to take the order.

## Security notes

| Risk | Control |
| --- | --- |
| SQL injection | Every query is parameterised. Sort order comes from a whitelist. Search words are quoted before they reach FTS5, so typed operators (`OR`, `NEAR`, `*`, `^`) are literal text. |
| XSS | Jinja autoescaping throughout, plus a CSP with no `unsafe-inline` (category colours come from CSS classes, not inline styles). Review text is user content and is escaped like everything else. |
| CSRF | Per-session token required on every POST/PUT/PATCH/DELETE, compared with `secrets.compare_digest`. |
| Broken access control | `@login_required` / `@admin_required` / `@seller_required` decorators. Orders are visible only to their owner, or to a guest who proved the email address. Sellers can only touch listings where `seller_id` matches their own, checked in the SQL rather than after the fact. Reviews can only be deleted by their author or an admin. |
| Unrestricted file upload | Uploads are decoded and re-encoded by Pillow, stored under a generated name outside the static tree, and served only if the name matches the generated pattern. Size, pixel count and format are all bounded. |
| Privilege escalation | Commission rate and seller status are set by the platform only; posting them to the application form does nothing. Admins cannot rewrite a seller's listing, only hide it. |
| Credential handling | Passwords hashed with scrypt via Werkzeug. Login failures give one generic message so the form cannot enumerate accounts, and the reset page answers identically whether or not the address exists. |
| Brute force | Sign-in, password reset and order lookup are all throttled per IP (and per email where it applies). In-process only; see the caveat below. |
| Reset token theft | Only the SHA-256 of the token is stored, so a database leak cannot be replayed. Tokens are single-use, expire in an hour, and asking again invalidates the previous link. |
| Order enumeration | A reference alone is not enough — a guest must also supply the checkout email, and failures are throttled. Signed-in strangers get a 404, which does not confirm the reference exists. |
| Session fixation | A fresh session is issued on sign-in (the cart is carried over deliberately). A password reset clears the session instead of signing you in, since a reset is also the remedy for a hijacked account. |
| Open redirect | `?next=` targets are resolved against the request host and rejected if off-site. |
| Secrets in source | `SECRET_KEY` comes from `SHOP_SECRET_KEY`, otherwise a random key is generated into `instance/` — which is gitignored. |
| Information leakage | Unhandled exceptions are logged server-side and shown as a generic 500 page; the traceback never reaches the browser. |
| Money rounding | Prices are integer cents end to end; admin input is parsed with `Decimal`, never `float`. The commission split is integer arithmetic that always reconciles to the line total. |
| Overselling | Stock is decremented with a guarded `UPDATE ... WHERE stock >= ?` and the whole order rolls back if the row count is wrong. |

Set `SHOP_HTTPS=1` behind TLS to add the `Secure` flag to the session cookie.

### Known limits

- **The throttles live in process memory.** They protect one worker. Run more than
  one and an attacker gets one bucket per worker, so put a real rate limiter
  (nginx, Cloudflare, `flask-limiter` with Redis) in front before going live.
- **No payment step, and no payouts.** `_place_order` in `shop/orders.py` is the
  single seam where a gateway would go. The commission is *accounted* for, but no
  money moves and sellers are never actually paid — a real marketplace needs
  split payments (Stripe Connect or similar) and a payout ledger.
- **Seller listings are not moderated.** Approved sellers publish immediately.
  Add a review queue before opening this to the public.
- **Settings added after the first version live in `create_app`,** not `config.py`,
  because that file is excluded from edits in this workspace. Worth consolidating.

## Layout

```
run.py                  click-to-run launcher
shop/
  __init__.py           app factory, hooks, filters, error handling
  config.py             settings and secret-key loading
  db.py                 SQLite connection, schema version guard, `flask init-db`
  schema.sql            tables, constraints, the FTS5 index and visible_products
  seed.py               the demo catalogue, its reviews and two demo shops
  security.py           CSRF, auth guards, throttling, redirect safety, headers
  mail.py               console / file / SMTP backends and message bodies
  uploads.py            image validation, re-encoding and storage
  products.py           product form parsing shared by admin and sellers
  catalog.py            browse, search, product detail
  cart.py               cart state and pricing
  orders.py             checkout, commission split, fulfilment, guest lookup
  reviews.py            review CRUD and the rating cache
  sellers.py            applications, listings, photos, sales and earnings
  admin.py              dashboard, product management, order queue, sellers
  templates/            Jinja templates
  static/               stylesheet, one small script, favicon
tests/test_shop.py
```

## Demo accounts

Alongside the admin and shopper above, the seed creates six reviewer accounts
(`reviewer1@shopsphere.test` … `reviewer6@shopsphere.test`, password
`Review#12345`) purely so every product's star rating is a real average of real
reviews rather than a number typed into a fixture.

## License

MIT — see [LICENSE](LICENSE). Use it, change it, ship it; just keep the copyright
notice. It comes with no warranty, which is worth taking literally here: this is a
demonstration project with no payment processing and no seller payouts.
