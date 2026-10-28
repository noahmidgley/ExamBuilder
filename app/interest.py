"""
The public "I'm interested" form - the only page you can see without logging in.

Visitors leave their name and education level (and an optional message).
Each submission is:
  1. saved in the database (so nothing is lost if email fails), and
  2. emailed to you with Resend, an email API that works over HTTPS.
     Render's free plan blocks normal SMTP email, so we can't use Gmail's
     SMTP server directly.

Because anyone can reach this page, it has spam protection:
  - CSRF token (like every form on the site),
  - a hidden "honeypot" field that people never see but bots fill in,
  - a limit of 3 submissions per hour from the same IP address,
  - length limits on every field.
"""
import html
from datetime import datetime, timedelta, timezone

import requests
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app.db import get_db

bp = Blueprint("interest", __name__)

EDUCATION_LEVELS = [
    "GCSE (Years 10–11)",
    "A-level / sixth form (Years 12–13)",
    "College (BTEC, T-level, etc.)",
    "University",
    "Teacher or tutor",
    "Other",
]
MAX_PER_HOUR = 3


def client_ip():
    # On Render the app sits behind a proxy, which puts the visitor's real IP
    # first in X-Forwarded-For.
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() or request.remote_addr


def too_many_recent(ip):
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    count = get_db().execute(
        "SELECT COUNT(*) FROM interest WHERE ip_address = ? AND created_at > ?", (ip, since)
    ).fetchone()[0]
    return count >= MAX_PER_HOUR


def send_email(name, education, message):
    """Email the submission to you. Returns True if Resend accepted it."""
    key, to = current_app.config.get("RESEND_API_KEY"), current_app.config.get("NOTIFY_EMAIL")
    if not key or not to:
        return False
    body = (
        f"<p><b>Name:</b> {html.escape(name)}</p>"
        f"<p><b>Education level:</b> {html.escape(education)}</p>"
        + (f"<p><b>Message:</b><br>{html.escape(message)}</p>" if message else "")
    )
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "from": current_app.config["NOTIFY_FROM"],
                "to": [to],
                "subject": f"ExamBuilder: {name} is interested",
                "html": body,
            },
            timeout=10,
        )
        return response.ok
    except requests.RequestException:
        return False


@bp.route("/interest", methods=["GET", "POST"])
def form():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        education = request.form.get("education", "")
        message = request.form.get("message", "").strip()

        # Honeypot: a real person never sees or fills in this field.
        if request.form.get("website"):
            return redirect(url_for("interest.thanks"))

        ip = client_ip()
        if not name or len(name) > 100:
            flash("Please enter your name (up to 100 characters).", "error")
        elif education not in EDUCATION_LEVELS:
            flash("Please choose your education level.", "error")
        elif len(message) > 1000:
            flash("Please keep your message under 1000 characters.", "error")
        elif too_many_recent(ip):
            flash("Thanks - we've already had a few messages from you. Please try again later.", "error")
        else:
            db = get_db()
            with db:
                row_id = db.execute(
                    "INSERT INTO interest (name, education, message, ip_address) VALUES (?, ?, ?, ?)",
                    (name, education, message or None, ip),
                ).lastrowid
            if send_email(name, education, message):
                with db:
                    db.execute("UPDATE interest SET emailed = 1 WHERE id = ?", (row_id,))
            return redirect(url_for("interest.thanks"))
        return render_template("interest.html", levels=EDUCATION_LEVELS, form=request.form), 400

    return render_template("interest.html", levels=EDUCATION_LEVELS, form={})


@bp.route("/interest/thanks")
def thanks():
    return render_template("interest_thanks.html")
