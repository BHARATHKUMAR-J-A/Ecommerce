# ShopSphere

[![CI](https://github.com/yongjunmun/E-Commerce-Website/actions/workflows/ci.yml/badge.svg)](https://github.com/yongjunmun/E-Commerce-Website/actions/workflows/ci.yml)

A complete e-commerce marketplace: browse a catalogue across 10 categories, search
and filter it, fill a cart, check out, and manage stock from an admin area.
Independent sellers can apply, list their own products with photos, and the
platform takes a commission on every sale.

Flask + SQLite + server-rendered HTML. No Node, no build step, two dependencies.

## Run it

```powershell
git clone https://github.com/yongjunmun/E-Commerce-Website.git
cd E-Commerce-Website
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

222 tests covering the catalogue, search, cart maths, checkout, stock accounting,
order fulfilment, reviews, password reset, seller onboarding, image uploads,
commission accounting, sales reporting, notifications, CSV export and the security
controls listed below. It takes about three minutes — most of that is scrypt
deliberately being slow.

CI runs the whole suite on every push against Python 3.12, 3.13 and 3.14 on Linux
and 3.13 on Windows, plus a smoke job that boots the real app and serves every
main page. The badge above is the honest answer to whether those tests pass.

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

## What the owner actually earns

A marketplace has two numbers that are easy to confuse, so the admin area names
them separately and never mixes them:

| Figure | What it is |
| --- | --- |
| **Customers paid** | `SUM(orders.total_cents)` — the money that moved. Includes shipping and tax **and the sellers' share**, so it is not yours. |
| **Goods sold** | the product lines only, before shipping and tax. |
| **Owed to sellers** | `SUM(seller_earning_cents)`. |
| **Your revenue** | commission on seller items **plus** own-brand sales. This is the one that is yours to keep. |

`goods sold = owed to sellers + your revenue`, exactly, and there is a test that
asserts it. The dashboard used to call the first figure "gross revenue", which
flattered the number by counting other people's money and the tax collected on
their behalf.

`/admin/reports` breaks that down over a chosen window (24 hours, 7 days, 30 days,
12 months, or all time):

- **every item sold**, with units, orders, goods sold, the seller's cut and yours.
  Product options are reported as separate rows, so "Mug — copper red" and
  "Mug — oatmeal" have their own numbers rather than being averaged together.
- **every seller**, including ones with no sales in the window, which appear at zero
  rather than vanishing — a shop that has stopped selling is the thing you want to
  notice.
- **a per-day series** for the last 30 days.

`/admin/sellers/<id>` is the drill-down for one shop: their totals, what they sold,
what is owed, and their current listings.

All of it is derived from `order_items` joined to live order status, so cancelling
an order removes it from every figure at once. The period and sort selectors are
looked up in a fixed dictionary — the SQL never sees user input, so a hostile
`?period=` is a fallback rather than an injection.

### Getting the numbers out

`/admin/reports.csv` and `/admin/payouts.csv` export the same figures, honouring
the period filter. The payout file carries each shop's contact email and payout
reference, which is what you would hand to whoever actually moves the money.

Both are written through `shop/exports.py`, which neutralises **formula
injection**. Sellers choose their own shop and product names, and a spreadsheet
treats a leading `=`, `+`, `-` or `@` as a formula — so a shop called
`=cmd|'/c calc'!A1` would run on the accountant's machine. Every field is escaped
on the way out, except genuine negative numbers, which are left alone so they
still sum.

## The order queue

`/admin/orders` is where fulfilment actually happens, so it is built for a shop
with a real number of orders rather than a demo's worth:

- **paginated**, 25 at a time, using the same windowed pager as the storefront.
- **filtered by status**, with a live count on each tab — "12 paid" is the number
  you need to pack this morning.
- **searchable** by reference, email or recipient name, which is the actual daily
  task: a customer emails about `SS-20260803-4A92BF` and you need it now.

The search escapes `%` and `_` before they reach `LIKE`, so searching for `%`
finds nothing rather than matching every order in the shop.

## Notifications

| When | Who hears about it |
| --- | --- |
| Order placed | the customer gets a receipt; **each seller with a line in it** gets their own email |
| Order shipped / delivered / cancelled | the customer |
| Seller application approved / rejected / suspended | the applicant |

Two deliberate choices:

- **A seller's email contains only their own lines.** They learn what they sold,
  not what else was in the customer's basket. There is a test for it.
- **"Packed" emails nobody.** It is warehouse bookkeeping, not news, and mailing
  every internal step is how a shop teaches its customers to ignore its email.

Emails are sent *after* the database commit, and `send_email` never raises, so a
dead mail server cannot fail a checkout or undo a fulfilment step.

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

**Admin** — dashboard separating takings from actual revenue, a sales report by item
and by seller, CSV export, low-stock lists, product create/edit/hide, and a
searchable, filterable, paginated order queue. Orders move
`paid → packed → shipped → delivered`, or get cancelled from `paid`/`packed`;
cancelling returns the items to stock and drops the order out of every money figure.
Illegal jumps are refused rather than silently applied. Products are soft-deleted so
past orders stay intact.

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
| SQL injection | Every query is parameterised. Sort order comes from a whitelist. Search words are quoted before they reach FTS5, so typed operators (`OR`, `NEAR`, `*`, `^`) are literal text. Report periods and admin order-status filters are dictionary lookups, so an unrecognised value falls back instead of reaching SQL. Admin search escapes `%` and `_` before `LIKE` sees them. |
| CSV formula injection | Shop and product names are written by sellers, and a spreadsheet executes a cell starting `=`, `+`, `-` or `@`. `shop/exports.py` prefixes those with an apostrophe on export, leaving real negative numbers alone. |
| XSS | Jinja autoescaping throughout, plus a CSP with no `unsafe-inline` (category colours come from CSS classes, not inline styles). Review text is user content and is escaped like everything else. |
| CSRF | Per-session token required on every POST/PUT/PATCH/DELETE, compared with `secrets.compare_digest`. |
| Information leak between sellers | A sale notification contains only the recipient's own order lines, never the rest of the customer's basket. |
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
- **The reports are computed live on every request.** That is honest and always
  correct, but it is a full scan of `order_items` each time. Fine at demo scale;
  at real scale you would want a nightly rollup table, or at least an index on
  `orders.created_at`.
- **Emails are sent inline, in the request.** A slow SMTP server makes checkout
  slow. A real shop would queue them.
- **Refunds are not modelled.** An order is either counted or cancelled; there is
  no partial return, so the money figures cannot express "two of the three came
  back".
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
  settings.py           store settings held in the database, with config fallback
  security.py           CSRF, auth guards, throttling, redirect safety, headers
  mail.py               console / file / SMTP backends and message bodies
  uploads.py            image validation, re-encoding and storage
  products.py           product form parsing shared by admin and sellers
  variants.py           product options, stock roll-up and price ranges
  catalog.py            browse, search, product detail
  cart.py               cart state and pricing
  orders.py             checkout, commission split, fulfilment, guest lookup
  reviews.py            review CRUD and the rating cache
  reports.py            the sales queries behind the dashboard and the report
  exports.py            CSV writing, with the spreadsheet-formula guard
  sellers.py            applications, listings, photos, sales and earnings
  admin.py              dashboard, sales report, products, order queue, sellers
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
