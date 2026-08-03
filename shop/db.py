"""SQLite connection handling and schema bootstrap."""

from __future__ import annotations

import sqlite3

import click
from flask import Flask, current_app, g

# Raise this whenever schema.sql changes shape.
SCHEMA_VERSION = 3


class SchemaOutOfDate(RuntimeError):
    pass


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            detect_types=sqlite3.PARSE_DECLTYPES,
            timeout=current_app.config["DB_TIMEOUT_SECONDS"],
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        # WAL lets reads continue during a checkout write instead of erroring.
        g.db.execute("PRAGMA journal_mode = WAL")
        g.db.execute("PRAGMA synchronous = NORMAL")
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    with current_app.open_resource("schema.sql") as handle:
        db.executescript(handle.read().decode("utf-8"))
    db.execute("INSERT INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,))
    db.commit()


def check_schema() -> None:
    """Raise if the database on disk predates the current schema.sql."""
    db = get_db()
    try:
        row = db.execute("SELECT version FROM schema_meta").fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is None or row["version"] != SCHEMA_VERSION:
        found = "pre-versioning" if row is None else row["version"]
        raise SchemaOutOfDate(
            f"Database schema is {found}, this code needs {SCHEMA_VERSION}. "
            f"Delete {current_app.config['DATABASE']} and run again, "
            f"or use: flask --app shop init-db"
        )


@click.command("init-db")
@click.option("--demo/--no-demo", default=True, help="Load the demo catalogue.")
def init_db_command(demo: bool) -> None:
    """Drop everything and recreate the database."""
    from .seed import seed_database

    init_db()
    if demo:
        seed_database()
    click.echo("Database initialised at " + current_app.config["DATABASE"])


def init_app(app: Flask) -> None:
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
