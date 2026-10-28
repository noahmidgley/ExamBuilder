"""
Database helpers.

Flask handles each web request separately. We open one SQLite connection per
request, store it on Flask's `g` object (a per-request scratch space), and close
it when the request finishes.
"""
import sqlite3
from pathlib import Path

from flask import current_app, g

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path):
    """Open a connection with the settings we always want.

    Kept separate from get_db() so command-line scripts (downloader, importer)
    can use the same settings outside a web request.
    """
    conn = sqlite3.connect(db_path)
    # Rows behave like dicts: row["marks"] instead of row[5].
    conn.row_factory = sqlite3.Row
    # SQLite ignores foreign keys unless you switch them on per connection.
    # Needed for ON DELETE CASCADE to work.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """Return this request's connection, opening it on first use."""
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE_PATH"])
    return g.db


def close_db(exception=None):
    """Close the connection at the end of the request (registered in create_app)."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(db_path):
    """Create any missing tables. Safe to run repeatedly."""
    conn = connect(db_path)
    with conn:
        conn.executescript(SCHEMA_PATH.read_text())
    conn.close()
