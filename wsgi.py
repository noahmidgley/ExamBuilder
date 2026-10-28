"""
Entry point.

Local development:   flask --app wsgi run --debug
Production (Render): gunicorn wsgi:app
"""
from app import create_app

app = create_app()
