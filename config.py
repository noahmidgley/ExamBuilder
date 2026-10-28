"""
Central configuration.

All settings come from environment variables, which python-dotenv loads from
the .env file. Keeping secrets out of the code means the code can safely go on
GitHub while the .env file stays on your machine (or in Render's settings).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Folder this file lives in = the project root.
BASE_DIR = Path(__file__).resolve().parent

# Read .env into os.environ (does nothing if the file doesn't exist,
# e.g. on Render where variables are set in the dashboard instead).
load_dotenv(BASE_DIR / ".env")

# DATA_DIR is the root for everything that changes at runtime: the database,
# the downloaded PDFs and the cropped images. Locally it's the project folder.
# On Render we'll point it at the persistent disk so data survives redeploys.
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR))

PAPERS_DIR = DATA_DIR / "papers"
GEOGRAPHY_PAPERS_DIR = PAPERS_DIR / "geography"
CS_PAPERS_DIR = PAPERS_DIR / "computer-science"

# Cropped images live OUTSIDE the Flask "static" folder, so Flask never serves
# them automatically. They are only sent through a route that checks login.
STORAGE_DIR = DATA_DIR / "storage"
QUESTION_IMAGES_DIR = STORAGE_DIR / "questions"
MARKSCHEME_IMAGES_DIR = STORAGE_DIR / "markschemes"
# Rendered pages of the Geography inserts (the figures questions refer to).
INSERT_IMAGES_DIR = STORAGE_DIR / "inserts"

DATABASE_PATH = DATA_DIR / "instance" / "exambuilder.db"


class Config:
    """Settings Flask reads via app.config.from_object(Config)."""

    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
    LOGIN_USERNAME = os.environ.get("LOGIN_USERNAME")
    LOGIN_PASSWORD_HASH = os.environ.get("LOGIN_PASSWORD_HASH")
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
    # "I'm interested" form: emails are sent with Resend (an email API that
    # works over HTTPS - Render's free plan blocks normal SMTP email).
    RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
    NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL")  # where submissions are sent
    NOTIFY_FROM = os.environ.get("NOTIFY_FROM", "ExamBuilder <onboarding@resend.dev>")

    DATABASE_PATH = DATABASE_PATH

    # Cookie hardening: JavaScript can't read the session cookie, and it isn't
    # sent on cross-site requests.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Only send the cookie over HTTPS. Off by default so it works on
    # http://localhost; set SESSION_COOKIE_SECURE=1 on Render.
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE") == "1"


def ensure_data_dirs():
    """Create the data folders if they don't exist yet (e.g. a fresh disk)."""
    for folder in (
        GEOGRAPHY_PAPERS_DIR,
        CS_PAPERS_DIR,
        QUESTION_IMAGES_DIR,
        MARKSCHEME_IMAGES_DIR,
        INSERT_IMAGES_DIR,
        DATABASE_PATH.parent,
    ):
        folder.mkdir(parents=True, exist_ok=True)
