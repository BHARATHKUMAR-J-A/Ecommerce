"""Click-to-run launcher: builds the database on first run, then serves the shop."""

from __future__ import annotations

import os
import sys
import threading
import webbrowser

from shop import create_app
from shop.db import SchemaOutOfDate, check_schema, init_db
from shop.seed import seed_database

HOST = os.environ.get("SHOP_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHOP_PORT", "5000"))


def main() -> None:
    app = create_app()

    if not os.path.exists(app.config["DATABASE"]):
        with app.app_context():
            init_db()
            seed_database()
        print("Created a fresh database with the demo catalogue.")
    else:
        with app.app_context():
            try:
                check_schema()
            except SchemaOutOfDate as error:
                print(f"\n  {error}\n")
                sys.exit(1)

    url = f"http://{HOST}:{PORT}/"
    print(f"\n  {app.config['STORE_NAME']} is running at {url}")
    print("  Sign in as admin@shopsphere.test / Admin#12345")
    print("  Press Ctrl+C to stop.\n")

    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=HOST, port=PORT, debug=False)


if __name__ == "__main__":
    main()
