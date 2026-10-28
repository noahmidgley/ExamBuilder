"""
Flask application factory.

Instead of creating the app at import time, create_app() builds it on demand.
Tests can then create an app with different settings (e.g. a temporary
database) without touching your real data.
"""
from datetime import timedelta

from flask import Flask
from flask_wtf.csrf import CSRFProtect

import config
from app import auth, db, interest, views

csrf = CSRFProtect()


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(config.Config)
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)  # then log in again
    app.config["WTF_CSRF_TIME_LIMIT"] = None  # tokens last as long as the session
    if overrides:
        app.config.update(overrides)

    # Refuse to start without a secret key. Without one, anyone could forge a
    # session cookie and skip the login.
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            "FLASK_SECRET_KEY is not set. Copy .env.example to .env and fill it in."
        )

    config.ensure_data_dirs()
    db.init_db(app.config["DATABASE_PATH"])
    app.teardown_appcontext(db.close_db)

    # CSRF protection: every POST must include a secret token from our own
    # page, so another website can't submit forms on your behalf.
    csrf.init_app(app)

    app.register_blueprint(auth.bp)
    app.register_blueprint(views.bp)
    app.register_blueprint(interest.bp)

    # Runs before every request: not logged in -> login page.
    app.before_request(auth.require_login)

    @app.after_request
    def security_headers(response):
        # Only load scripts/styles/images from this site; never be shown in a frame.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    return app
