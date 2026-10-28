"""
Login, logout and the "you must be logged in" rule.

Security choices
----------------
- Passwords are checked against a scrypt hash (never stored in plain text).
- hmac.compare_digest compares the username in constant time, so an attacker
  can't learn anything from how long the check takes.
- After 5 wrong passwords in a row, login is locked for 15 minutes. Attempts
  are stored in the database so restarting the server doesn't reset them.
  While locked we don't even check the password, so guessing is pointless.
- On successful login the old session is cleared (prevents "session fixation").
- require_login() runs before EVERY request, so a new page can't be
  accidentally left public. Only the login page and the CSS are exempt.
"""
import hmac
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app.db import get_db

bp = Blueprint("auth", __name__)

MAX_FAILURES = 5
LOCKOUT = timedelta(minutes=15)
# Endpoints you can reach without logging in.
PUBLIC_ENDPOINTS = {"auth.login", "static", "interest.form", "interest.thanks"}


# --------------------------------------------------------------------------
# Password storage
# --------------------------------------------------------------------------

def password_hash():
    """The current password hash: one set on the Settings page wins over .env."""
    row = get_db().execute("SELECT value FROM settings WHERE key = 'password_hash'").fetchone()
    return row["value"] if row else current_app.config.get("LOGIN_PASSWORD_HASH")


def set_password(new_password):
    with get_db() as db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('password_hash', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (generate_password_hash(new_password, method="scrypt"),),
        )


def check_credentials(username, password):
    expected_user = current_app.config.get("LOGIN_USERNAME") or ""
    stored_hash = password_hash()
    if not expected_user or not stored_hash:
        return False
    user_ok = hmac.compare_digest(username.encode(), expected_user.encode())
    # Always run the (slow) hash check, even if the username was wrong, so
    # both cases take the same time.
    pass_ok = check_password_hash(stored_hash, password)
    return user_ok and pass_ok


# --------------------------------------------------------------------------
# Lockout
# --------------------------------------------------------------------------

def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def locked_until():
    """If locked, the time the lock ends; otherwise None.

    Counts failed attempts since the last successful login. Once there are
    MAX_FAILURES of them, you're locked out for LOCKOUT after the last one.
    """
    db = get_db()
    last_success = db.execute(
        "SELECT MAX(attempted_at) FROM login_attempts WHERE success = 1"
    ).fetchone()[0] or "0000"
    failures = db.execute(
        "SELECT attempted_at FROM login_attempts WHERE success = 0 AND attempted_at > ? "
        "ORDER BY attempted_at DESC LIMIT ?",
        (last_success, MAX_FAILURES),
    ).fetchall()
    if len(failures) < MAX_FAILURES:
        return None
    ends = datetime.fromisoformat(failures[0]["attempted_at"]) + LOCKOUT
    return ends if ends > now_utc() else None


def record_attempt(success):
    with get_db() as db:
        db.execute(
            "INSERT INTO login_attempts (attempted_at, ip_address, success) VALUES (?, ?, ?)",
            (now_utc().isoformat(sep=" ", timespec="seconds"), request.remote_addr, int(success)),
        )


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("main.home"))

    if request.method == "POST":
        lock_end = locked_until()
        if lock_end:
            minutes = max(1, round((lock_end - now_utc()).total_seconds() / 60))
            flash(f"Too many failed attempts. Try again in {minutes} minute(s).", "error")
        elif check_credentials(request.form.get("username", ""), request.form.get("password", "")):
            record_attempt(True)
            session.clear()
            session.permanent = True  # lasts PERMANENT_SESSION_LIFETIME
            session["logged_in"] = True
            return redirect(safe_next(request.args.get("next")))
        else:
            record_attempt(False)
            flash("Wrong username or password.", "error")

    configured = bool(current_app.config.get("LOGIN_USERNAME") and password_hash())
    return render_template("login.html", configured=configured)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


def safe_next(target):
    """Only allow redirects back to our own pages (not to another website)."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("main.home")


def require_login():
    """Registered with app.before_request: runs before every request."""
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if not session.get("logged_in"):
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
    return None
