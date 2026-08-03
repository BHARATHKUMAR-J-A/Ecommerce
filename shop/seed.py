"""Demo catalogue. Run via ``flask --app shop init-db``."""

from __future__ import annotations

import os
import random

from werkzeug.security import generate_password_hash

from .db import get_db
from .variants import refresh_product_stock

DEMO_ADMIN_PASSWORD = "Admin#12345"
DEMO_SHOPPER_PASSWORD = "Demo#12345"
DEMO_REVIEWER_PASSWORD = "Review#12345"
DEMO_SELLER_PASSWORD = "Seller#12345"

# (email, person, shop name, slug, bio, status)
SELLERS = [
    (
        "maya@shopsphere.test", "Maya Iskandar", "Kiln & Clay", "kiln-and-clay",
        "Small-batch stoneware thrown and glazed by hand in a home studio.",
        "approved",
    ),
    (
        "tomas@shopsphere.test", "Tomas Berg", "Northline Woodwork", "northline-woodwork",
        "Hand-cut oak and walnut pieces for the kitchen and desk.",
        "approved",
    ),
    (
        "priya@shopsphere.test", "Priya Nair", "Second Sun Vintage", "second-sun-vintage",
        "Carefully chosen vintage clothing, cleaned and repaired before listing.",
        "pending",
    ),
]

# slug -> (name, description, price_cents, stock, category slug, icon)
SELLER_PRODUCTS = {
    "kiln-and-clay": [
        ("Speckled Stoneware Mug", "Wheel-thrown mug with a matte oatmeal glaze. Holds 350 ml and is dishwasher safe.", 3200, 14, "home-kitchen", "\u2615"),
        ("Ash Glaze Serving Bowl", "Wide serving bowl finished in a wood-ash glaze, so no two are identical.", 6800, 6, "home-kitchen", "\U0001F963"),
        ("Ceramic Planter (small)", "Unglazed exterior with a sealed interior and a drainage hole.", 2600, 22, "home-kitchen", "\U0001FAB4"),
    ],
    "northline-woodwork": [
        ("Walnut Chopping Board", "End-grain walnut board, 40 x 28 cm, finished with food-safe oil.", 8900, 9, "home-kitchen", "\U0001FAB5"),
        ("Oak Desk Tidy", "Turned oak holder with three wells for pens, cards and cables.", 4200, 17, "office", "\u270F\uFE0F"),
        ("Hand-Cut Coaster Set", "Four coasters cut from offcuts, each one a different grain.", 2800, 25, "home-kitchen", "\U0001F333"),
    ],
}

REVIEWERS = [
    "Aisha Rahman", "Ben Carter", "Chloe Tan", "Diego Moreno",
    "Emma Lindqvist", "Farid Hassan",
]

REVIEW_TITLES = [
    "Exactly what I wanted", "Solid buy", "Does the job", "Better than expected",
    "Would buy again", "Happy with it", "Good value", "No complaints",
]
REVIEW_BODIES = [
    "Arrived quickly and matches the description. No surprises.",
    "Been using it daily for a few weeks now and it has held up well.",
    "Good quality for the price. I compared a few before settling on this.",
    "Does what it says. Packaging was minimal, which I appreciated.",
    "Works well. Took a day or two to get used to, but no regrets.",
    "Bought one, then bought a second for a family member.",
]
LUKEWARM_BODIES = [
    "It is fine, but I expected a little more at this price.",
    "Works, though the finish is not as nice as the photos suggest.",
    "Does the job but I had a small issue at first. Sorted now.",
]

CATEGORIES = [
    ("electronics", "Electronics", "\U0001F4BB", "#3b82f6"),
    ("fashion", "Fashion & Apparel", "\U0001F457", "#ec4899"),
    ("home-kitchen", "Home & Kitchen", "\U0001F3E0", "#f59e0b"),
    ("sports-outdoors", "Sports & Outdoors", "\u26BD", "#10b981"),
    ("books-media", "Books & Media", "\U0001F4DA", "#8b5cf6"),
    ("beauty-health", "Beauty & Health", "\U0001F9F4", "#f43f5e"),
    ("toys-games", "Toys & Games", "\U0001F9F8", "#06b6d4"),
    ("grocery", "Grocery & Snacks", "\U0001F6D2", "#84cc16"),
    ("pet-supplies", "Pet Supplies", "\U0001F436", "#a855f7"),
    ("office", "Office & Stationery", "\u270F\uFE0F", "#64748b"),
]

# (sku, name, description, price_cents, stock, icon, rating)
PRODUCTS: dict[str, list[tuple]] = {
    "electronics": [
        ("ELE-001", 'Aurora 27" 4K Monitor', "IPS panel at 144 Hz with USB-C power delivery and factory colour calibration.", 42900, 24, "\U0001F5A5\uFE0F", 4.6),
        ("ELE-002", "Nimbus Wireless Earbuds", "Hybrid active noise cancelling, 32-hour case battery and multipoint pairing.", 8990, 140, "\U0001F3A7", 4.4),
        ("ELE-003", "Vertex Mechanical Keyboard", "Hot-swappable switches, milled aluminium case and per-key backlighting.", 12900, 60, "\u2328\uFE0F", 4.7),
        ("ELE-004", "Orbit Smartwatch S3", "AMOLED display, dual-band GPS, seven-day battery and 50 m water resistance.", 19900, 45, "\u231A", 4.2),
        ("ELE-005", "Pulse Bluetooth Speaker", "IP67 waterproof, 20 W stereo drivers and 24 hours of playtime per charge.", 6490, 90, "\U0001F50A", 4.3),
        ("ELE-006", "Photon 65 W GaN Charger", "Three ports and folding pins - runs a laptop and two phones at once.", 3490, 210, "\U0001F50C", 4.8),
        ("ELE-007", "Halo 4K Action Camera", "Waterproof to 10 m with gimbal-free stabilisation and dual preview screens.", 24900, 18, "\U0001F4F7", 4.1),
    ],
    "fashion": [
        ("FSH-001", "Everyday Merino Crew Tee", "Lightweight 17.5 micron merino that resists odour and travels well.", 5900, 180, "\U0001F455", 4.5),
        ("FSH-002", "Selvedge Denim Jacket", "14 oz raw selvedge denim with copper rivets and a relaxed trucker cut.", 12900, 40, "\U0001F9E5", 4.6),
        ("FSH-003", "Trailhead Running Shoes", "Rocker midsole and a grippy lug outsole for road-to-trail transitions.", 10900, 75, "\U0001F45F", 4.4),
        ("FSH-004", "Linen Summer Dress", "Breathable European flax with side pockets and a tie waist.", 7900, 55, "\U0001F457", 4.3),
        ("FSH-005", "Canvas Weekender Bag", "Waxed cotton canvas, leather trim and a 42-litre main compartment.", 8900, 30, "\U0001F45C", 4.7),
        ("FSH-006", "Polarised Aviator Sunglasses", "Glare-cutting polarised lenses in a featherweight titanium frame.", 4500, 120, "\U0001F576\uFE0F", 4.2),
        ("FSH-007", "Ribbed Wool Beanie", "Double-layer lambswool knit with a fold-over cuff.", 2400, 200, "\U0001F9E2", 4.0),
    ],
    "home-kitchen": [
        ("HOM-001", "Barista Pro Espresso Machine", "58 mm portafilter, PID temperature control and a built-in conical grinder.", 44900, 15, "\u2615", 4.6),
        ("HOM-002", 'Cast Iron Skillet 12"', "Pre-seasoned, oven safe to 260 C and effectively indestructible.", 5900, 85, "\U0001F373", 4.9),
        ("HOM-003", 'Damascus Chef Knife 8"', "67-layer folded steel core hardened to 60 HRC with a pakkawood handle.", 8900, 42, "\U0001F52A", 4.7),
        ("HOM-004", "Ceramic Dinner Set (16 pc)", "Reactive-glaze stoneware for four. Dishwasher and microwave safe.", 11900, 28, "\U0001F37D\uFE0F", 4.4),
        ("HOM-005", "Aroma Diffuser + Oil Trio", "Ultrasonic diffuser with an eight-hour tank and three essential oils.", 3900, 130, "\U0001F56F\uFE0F", 4.1),
        ("HOM-006", "Robot Vacuum V5", "LiDAR mapping, 4000 Pa suction and a self-emptying dock.", 32900, 22, "\U0001F9F9", 4.3),
        ("HOM-007", "Stand Mixer 5 L", "Planetary mixing action, 600 W motor and a die-cast metal body.", 27900, 19, "\U0001F963", 4.8),
    ],
    "sports-outdoors": [
        ("SPT-001", "TrailLite 2-Person Tent", "Freestanding double-wall shelter with a 2.1 kg packed weight.", 15900, 26, "\u26FA", 4.5),
        ("SPT-002", "Cork Yoga Mat 6 mm", "Natural cork surface over a recycled rubber base - grippier when damp.", 4900, 110, "\U0001F9D8", 4.6),
        ("SPT-003", "Adjustable Dumbbell 24 kg", "Dial from 2.5 kg to 24 kg in one hand. Replaces fifteen pairs.", 21900, 33, "\U0001F3CB\uFE0F", 4.4),
        ("SPT-004", "Insulated Bottle 1 L", "Double-wall vacuum steel keeps drinks cold 24 h or hot 12 h.", 2900, 240, "\U0001F964", 4.7),
        ("SPT-005", "Match Football Size 5", "Thermally bonded panels for a true flight in wet conditions.", 3400, 95, "\u26BD", 4.2),
        ("SPT-006", "Alpine 40 L Backpack", "Carry-on legal with a load-lifting harness and a rain cover.", 9900, 48, "\U0001F392", 4.5),
        ("SPT-007", "Composite Tennis Racket", "Graphite-composite frame, 300 g strung, strung at 55 lb.", 13900, 25, "\U0001F3BE", 4.3),
    ],
    "books-media": [
        ("BKS-001", "The Quiet Algorithm", "A novel about the engineers behind the machines that quietly run a city.", 2800, 70, "\U0001F4D5", 4.4),
        ("BKS-002", "Atlas of Forgotten Places", "Large-format cartography of eighty abandoned settlements.", 4200, 35, "\U0001F5FA\uFE0F", 4.8),
        ("BKS-003", "Practical Control Systems", "PID, state space and modern control worked through from first principles.", 6900, 40, "\U0001F4D8", 4.6),
        ("BKS-004", "Vinyl: Midnight Sessions", "180 g pressing of the live studio session, remastered from tape.", 3200, 55, "\U0001F4BF", 4.5),
        ("BKS-005", "Cook It Slow", "One hundred braises, stews and roasts with make-ahead timings.", 3600, 62, "\U0001F4D9", 4.3),
        ("BKS-006", "Ink & Grain", "A photo book on letterpress workshops still running today.", 5400, 20, "\U0001F4D6", 4.7),
    ],
    "beauty-health": [
        ("BTY-001", "Vitamin C Serum 30 ml", "15% L-ascorbic acid with ferulic acid in an airless pump bottle.", 3400, 150, "\U0001F9F4", 4.5),
        ("BTY-002", "Bamboo Toothbrush 4-pack", "Compostable handles with soft charcoal-infused bristles.", 1200, 300, "\U0001FAA5", 4.4),
        ("BTY-003", "Sonic Facial Cleanser", "Silicone brush head at 7000 pulses a minute. Fully waterproof.", 5900, 64, "\U0001F486", 4.2),
        ("BTY-004", "Shea Body Butter 250 g", "Unrefined shea and cold-pressed jojoba. Fragrance free.", 2200, 180, "\U0001F9FC", 4.6),
        ("BTY-005", "Cedar & Amber Eau de Parfum", "Cedarwood opening over amber and vetiver. 18% concentration.", 8900, 38, "\U0001F490", 4.3),
        ("BTY-006", "Daily Multivitamin (90 ct)", "Third-party tested, one tablet a day, no artificial colours.", 2600, 220, "\U0001F48A", 4.1),
    ],
    "toys-games": [
        ("TOY-001", "Skyline Builder Blocks", "900 pieces and four skyline blueprints. Compatible with major brands.", 7900, 44, "\U0001F9F1", 4.8),
        ("TOY-002", "Nebula Strategy Board Game", "Two to five players, sixty to ninety minutes, heavy on trade routes.", 4900, 58, "\U0001F3B2", 4.6),
        ("TOY-003", "RC Rock Crawler 1:16", "Four-wheel drive with locked differentials and oil-filled shocks.", 8900, 31, "\U0001F699", 4.3),
        ("TOY-004", "1000-Piece Jigsaw: Harbour", "Linen-finish board with a poster reference sheet included.", 2400, 88, "\U0001F9E9", 4.5),
        ("TOY-005", "Plush Companion Bear", "Recycled-fibre filling, machine washable, safety-tested to EN 71.", 1900, 140, "\U0001F9F8", 4.7),
        ("TOY-006", "STEM Robotics Starter Kit", "Servos, sensors and a block-coding app across twelve guided builds.", 12900, 26, "\U0001F916", 4.6),
    ],
    "grocery": [
        ("GRO-001", "Single-Origin Coffee 1 kg", "Washed Ethiopian arabica, roasted to order for filter or espresso.", 3200, 160, "\u2615", 4.7),
        ("GRO-002", "Raw Wildflower Honey 500 g", "Unpasteurised and unfiltered from a single apiary.", 1800, 120, "\U0001F36F", 4.8),
        ("GRO-003", "Dark Chocolate 70% (5-pack)", "Single-estate cacao, stone ground, nothing but cocoa and cane sugar.", 1500, 200, "\U0001F36B", 4.6),
        ("GRO-004", "Extra Virgin Olive Oil 750 ml", "First cold press, harvested in November, in a light-blocking tin.", 2400, 95, "\U0001FAD2", 4.5),
        ("GRO-005", "Sea Salt Kettle Chips (6-pack)", "Kettle-cooked in sunflower oil with nothing else added.", 999, 260, "\U0001F954", 4.2),
        ("GRO-006", "Sencha Green Tea 100 g", "First-flush loose leaf, vacuum sealed within a week of picking.", 1600, 140, "\U0001F375", 4.4),
    ],
    "pet-supplies": [
        ("PET-001", "Orthopaedic Dog Bed (L)", "Memory-foam base with a washable, chew-resistant cover.", 6900, 40, "\U0001F6CF\uFE0F", 4.6),
        ("PET-002", "Self-Cleaning Litter Tray", "Sifting rake on a timer with a sealed waste drawer.", 11900, 18, "\U0001F408", 4.1),
        ("PET-003", "Grain-Free Dog Food 12 kg", "Single-protein salmon recipe with added glucosamine.", 7400, 66, "\U0001F9B4", 4.5),
        ("PET-004", "Interactive Cat Laser Tower", "Randomised patterns on a fifteen-minute auto shut-off.", 3200, 84, "\U0001F431", 4.3),
        ("PET-005", "Reflective Dog Harness", "No-pull front clip with 360-degree reflective piping.", 2900, 130, "\U0001F415", 4.7),
        ("PET-006", "Aquarium Starter Kit 40 L", "Low-iron glass tank with filter, heater and LED hood.", 8900, 21, "\U0001F420", 4.2),
    ],
    "office": [
        ("OFF-001", "Ergonomic Mesh Chair", "Four-way lumbar support, adjustable arms and a synchro-tilt base.", 24900, 27, "\U0001FA91", 4.5),
        ("OFF-002", "Electric Standing Desk 140 cm", "Dual motor, 125 kg capacity and four memory height presets.", 49900, 12, "\U0001F5C4\uFE0F", 4.6),
        ("OFF-003", "A5 Dotted Notebook (3-pack)", "160 gsm fountain-pen-friendly paper that lies completely flat.", 2200, 190, "\U0001F4D3", 4.7),
        ("OFF-004", "Fineliner Pen Set (24)", "0.4 mm water-based pigment ink that will not bleed through.", 1900, 210, "\U0001F58A\uFE0F", 4.6),
        ("OFF-005", "Desk Lamp + Wireless Charging", "Flicker-free CRI 95 light with a 15 W Qi pad in the base.", 5400, 70, "\U0001F4A1", 4.4),
        ("OFF-006", "Cross-Cut Paper Shredder", "P-4 security level, twelve sheets per pass, 20 L bin.", 8900, 24, "\U0001F4C4", 4.0),
        ("OFF-007", "Cable Management Tray", "Under-desk steel tray with a clamp mount - no drilling required.", 2600, 150, "\U0001F4CE", 4.3),
    ],
}


def seed_database() -> None:
    db = get_db()

    admin_password = os.environ.get("SHOP_ADMIN_PASSWORD", DEMO_ADMIN_PASSWORD)
    db.executemany(
        "INSERT INTO users (email, name, password_hash, is_admin) VALUES (?, ?, ?, ?)",
        [
            ("admin@shopsphere.test", "Store Admin", generate_password_hash(admin_password), 1),
            ("demo@shopsphere.test", "Demo Shopper", generate_password_hash(DEMO_SHOPPER_PASSWORD), 0),
        ],
    )

    # Reviewers share one hash - they exist only to make the star ratings real.
    reviewer_hash = generate_password_hash(DEMO_REVIEWER_PASSWORD)
    db.executemany(
        "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
        [
            (f"reviewer{index + 1}@shopsphere.test", name, reviewer_hash)
            for index, name in enumerate(REVIEWERS)
        ],
    )

    db.executemany(
        "INSERT INTO categories (slug, name, icon, tint) VALUES (?, ?, ?, ?)", CATEGORIES
    )

    ids = {
        row["slug"]: row["id"]
        for row in db.execute("SELECT id, slug FROM categories").fetchall()
    }
    rows = [
        (sku, name, desc, price, stock, ids[slug], icon, rating)
        for slug, items in PRODUCTS.items()
        for sku, name, desc, price, stock, icon, rating in items
    ]
    db.executemany(
        "INSERT INTO products"
        " (sku, name, description, price_cents, stock, category_id, icon, rating)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    _seed_sellers(db, ids)
    _seed_reviews(db)
    db.commit()


def _seed_sellers(db, category_ids: dict) -> None:
    seller_hash = generate_password_hash(DEMO_SELLER_PASSWORD)
    rate = 0.10

    for email, person, shop_name, slug, bio, status in SELLERS:
        cursor = db.execute(
            "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
            (email, person, seller_hash),
        )
        db.execute(
            "INSERT INTO sellers (user_id, shop_name, slug, bio, contact_email,"
            " payout_reference, status, commission_rate, reviewed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(cursor.lastrowid), shop_name, slug, bio, email,
                "DEMO-PAYOUT-REF", status, rate,
                None if status == "pending" else "2026-08-01 09:00:00",
            ),
        )

    seller_ids = {
        row["slug"]: row["id"] for row in db.execute("SELECT id, slug FROM sellers")
    }
    listings = [
        (
            f"{slug[:8].upper()}-{index + 1:03d}", name, description, price, stock,
            category_ids[category], seller_ids[slug], icon,
        )
        for slug, items in SELLER_PRODUCTS.items()
        for index, (name, description, price, stock, category, icon) in enumerate(items)
    ]
    db.executemany(
        "INSERT INTO products (sku, name, description, price_cents, stock,"
        " category_id, seller_id, icon) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        listings,
    )
    _seed_options(db)


def _seed_options(db) -> None:
    """Give two demo listings real variants and one a personalisation field."""
    def product_id(name: str):
        row = db.execute("SELECT id FROM products WHERE name = ?", (name,)).fetchone()
        return row["id"] if row else None

    mug = product_id("Speckled Stoneware Mug")
    if mug is not None:
        db.execute(
            "UPDATE products SET option_label = 'Glaze',"
            " personalisation_label = 'Name on the base', personalisation_max = 20"
            " WHERE id = ?",
            (mug,),
        )
        db.executemany(
            "INSERT INTO product_variants (product_id, label, price_cents, stock, position)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (mug, "Oatmeal", None, 6, 0),
                (mug, "Deep sea", None, 5, 1),
                (mug, "Copper red", 3800, 3, 2),
            ],
        )
        refresh_product_stock(db, mug)

    board = product_id("Walnut Chopping Board")
    if board is not None:
        db.execute(
            "UPDATE products SET option_label = 'Size',"
            " personalisation_label = 'Engraving', personalisation_max = 30"
            " WHERE id = ?",
            (board,),
        )
        db.executemany(
            "INSERT INTO product_variants (product_id, label, price_cents, stock, position)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (board, 'Small (30 x 20 cm)', 6900, 4, 0),
                (board, 'Large (40 x 28 cm)', 8900, 5, 1),
            ],
        )
        refresh_product_stock(db, board)


def _split_rating(target: float, count: int) -> list[int]:
    """`count` whole-star scores that average as close to `target` as possible."""
    total = max(count, min(5 * count, round(target * count)))
    base, extra = divmod(total, count)
    return [base + 1] * extra + [base] * (count - extra)


def _seed_reviews(db) -> None:
    """Give every product real reviews, so `rating` is a genuine average."""
    rng = random.Random(20260803)  # fixed seed: the demo data is reproducible
    reviewer_ids = [
        row["id"]
        for row in db.execute(
            "SELECT id FROM users WHERE email LIKE 'reviewer%' ORDER BY id"
        ).fetchall()
    ]
    # Only the house catalogue ships with a target rating; new seller listings
    # start with no reviews, as a real new listing would.
    products = db.execute(
        "SELECT id, rating FROM products WHERE rating > 0 ORDER BY id"
    ).fetchall()

    entries = []
    for product in products:
        # At least three, or small-sample rounding makes everything a flat 5.0.
        count = rng.randint(3, min(6, len(reviewer_ids)))
        scores = _split_rating(product["rating"], count)
        rng.shuffle(scores)
        for author, score in zip(rng.sample(reviewer_ids, count), scores):
            body = rng.choice(REVIEW_BODIES if score >= 4 else LUKEWARM_BODIES)
            entries.append(
                (product["id"], author, score, rng.choice(REVIEW_TITLES), body)
            )

    db.executemany(
        "INSERT INTO reviews (product_id, user_id, rating, title, body)"
        " VALUES (?, ?, ?, ?, ?)",
        entries,
    )
    # Replace the hand-written ratings with the averages the reviews actually give.
    db.execute(
        "UPDATE products SET"
        "  rating = COALESCE((SELECT ROUND(AVG(rating), 2) FROM reviews"
        "                     WHERE product_id = products.id), 0),"
        "  review_count = (SELECT COUNT(*) FROM reviews WHERE product_id = products.id)"
    )
