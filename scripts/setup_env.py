"""
Create (or update) the .env file.

Run:  python3 scripts/setup_env.py

- Generates a random FLASK_SECRET_KEY if there isn't one already.
- Asks for a username and password, and stores only the password HASH.
  A hash is one-way: the app can check a typed password against it, but
  nobody who reads .env can turn the hash back into your password.
- Optionally stores your Claude API key.
Existing values you don't change are kept.
"""
import getpass
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values
from werkzeug.security import generate_password_hash

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def main():
    values = dict(dotenv_values(ENV_PATH)) if ENV_PATH.exists() else {}

    if not values.get("FLASK_SECRET_KEY"):
        values["FLASK_SECRET_KEY"] = secrets.token_hex(32)
        print("Generated a new FLASK_SECRET_KEY.")

    username = input(f"Username [{values.get('LOGIN_USERNAME', '')}]: ").strip()
    if username:
        values["LOGIN_USERNAME"] = username

    # getpass hides what you type.
    password = getpass.getpass("New password (leave blank to keep current): ")
    if password:
        if len(password) < 10:
            sys.exit("Password must be at least 10 characters. Nothing saved.")
        if password != getpass.getpass("Repeat password: "):
            sys.exit("Passwords didn't match. Nothing saved.")
        # scrypt is a deliberately slow hashing algorithm, which makes
        # guessing passwords by brute force very expensive.
        values["LOGIN_PASSWORD_HASH"] = generate_password_hash(password, method="scrypt")

    api_key = getpass.getpass("Claude API key (leave blank to keep current): ").strip()
    if api_key:
        values["ANTHROPIC_API_KEY"] = api_key

    # "I'm interested" form notifications (optional).
    resend_key = getpass.getpass("Resend API key for interest emails (leave blank to keep current): ").strip()
    if resend_key:
        values["RESEND_API_KEY"] = resend_key
    notify = input(f"Email address to send interest sign-ups to [{values.get('NOTIFY_EMAIL', '')}]: ").strip()
    if notify:
        values["NOTIFY_EMAIL"] = notify

    # On the live server (HTTPS), only send the login cookie over HTTPS.
    live = input("Is this the live website on PythonAnywhere? [y/N]: ").strip().lower()
    if live == "y":
        values["SESSION_COOKIE_SECURE"] = "1"
    elif live == "n":
        values.pop("SESSION_COOKIE_SECURE", None)

    # Single quotes stop the "$" characters in the hash being treated as variables.
    lines = [f"{key}='{value}'" for key, value in values.items() if value is not None]
    ENV_PATH.write_text("\n".join(lines) + "\n")
    ENV_PATH.chmod(0o600)  # only your user account can read the file
    print(f"Saved {ENV_PATH}")


if __name__ == "__main__":
    main()
