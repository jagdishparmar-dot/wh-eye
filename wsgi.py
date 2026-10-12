"""WSGI entrypoint for Gunicorn."""

from grid_ui import create_app

app = create_app()
