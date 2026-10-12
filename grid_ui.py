"""Warehouse Eye — chamber grid, blocked bins, and free locations from PostgreSQL."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from psycopg.rows import dict_row
from werkzeug.middleware.proxy_fix import ProxyFix

APP_DIR = Path(__file__).resolve().parent
LAYOUT_PATH = APP_DIR / "grid-layout.json"
BLOCKED_PATH = APP_DIR / "blocked_bin.json"

FREE_LOCATIONS_SQL = """
SELECT warehouse_id, location_id, chamber_code, seq_no, assigned_to
FROM public.free_locations_mtr
"""

logger = logging.getLogger(__name__)

LOCATION_KEY_RE = re.compile(r"^([A-Za-z])(\d+)")

_free_cache: tuple[float, set[str], str | None] | None = None

COLOR_STYLES: dict[str, dict[str, str]] = {
    "border-green-700/50 bg-green-900/20": {
        "border": "rgba(34, 197, 94, 0.35)",
        "background": "rgba(20, 83, 45, 0.12)",
        "header": "#4ade80",
        "accent": "#22c55e",
    },
    "border-sky-700/50 bg-sky-900/20": {
        "border": "rgba(56, 189, 248, 0.35)",
        "background": "rgba(12, 74, 110, 0.12)",
        "header": "#38bdf8",
        "accent": "#0ea5e9",
    },
    "border-indigo-700/50 bg-indigo-900/20": {
        "border": "rgba(129, 140, 248, 0.35)",
        "background": "rgba(49, 46, 129, 0.12)",
        "header": "#a5b4fc",
        "accent": "#6366f1",
    },
}

DEFAULT_STYLE = COLOR_STYLES["border-green-700/50 bg-green-900/20"]


def configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def load_layout() -> list[dict]:
    with LAYOUT_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def grid_key_from_location_id(location_id: str) -> str | None:
    match = LOCATION_KEY_RE.match(location_id.strip())
    if not match:
        return None
    prefix, number = match.group(1).upper(), int(match.group(2))
    return f"{prefix}{number}"


def load_blocked_grid_keys() -> set[str]:
    if not BLOCKED_PATH.is_file():
        return set()
    with BLOCKED_PATH.open(encoding="utf-8") as f:
        entries = json.load(f)
    keys: set[str] = set()
    for entry in entries:
        location_id = entry.get("location_id")
        if not location_id:
            continue
        key = grid_key_from_location_id(str(location_id))
        if key:
            keys.add(key)
    return keys


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")


def _fetch_free_grid_keys_from_db() -> tuple[set[str], str | None]:
    url = database_url()
    if not url:
        return set(), "DATABASE_URL is not configured."

    keys: set[str] = set()
    try:
        with psycopg.connect(
            url,
            row_factory=dict_row,
            connect_timeout=int(os.environ.get("DB_CONNECT_TIMEOUT", "10")),
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(FREE_LOCATIONS_SQL)
                for row in cur:
                    location_id = row.get("location_id")
                    if not location_id:
                        continue
                    key = grid_key_from_location_id(str(location_id))
                    if key:
                        keys.add(key)
    except Exception as exc:
        logger.exception("Failed to query free_locations_mtr")
        return set(), f"Database error: {exc}"

    return keys, None


def load_free_grid_keys_from_db() -> tuple[set[str], str | None]:
    global _free_cache
    ttl = int(os.environ.get("FREE_LOCATIONS_CACHE_SECONDS", "300"))
    now = time.monotonic()
    if _free_cache is not None and now - _free_cache[0] < ttl:
        return _free_cache[1], _free_cache[2]

    keys, err = _fetch_free_grid_keys_from_db()
    _free_cache = (now, keys, err)
    return keys, err


def cells_for_row(row: dict) -> list[str]:
    prefix = row["prefix"]
    start, end = int(row["start"]), int(row["end"])
    numbers = range(start, end + 1) if start <= end else range(start, end - 1, -1)
    labels = [f"{prefix}{n}" for n in numbers]
    if row.get("direction") == "rtl":
        labels.reverse()
    return labels


def enrich_layout(
    groups: list[dict],
    blocked_keys: set[str],
    free_keys: set[str],
) -> list[dict]:
    enriched: list[dict] = []
    for group in groups:
        style = COLOR_STYLES.get(group.get("colorClass", ""), DEFAULT_STYLE)
        blocks_out: list[dict] = []
        slot_count = 0
        blocked_count = 0
        free_count = 0
        for block in group.get("blocks", []):
            rows_out = []
            for row in block.get("rows", []):
                cells = []
                for label in cells_for_row(row):
                    blocked = label in blocked_keys
                    free = label in free_keys and not blocked
                    slot_count += 1
                    if blocked:
                        blocked_count += 1
                    elif free:
                        free_count += 1
                    cells.append(
                        {
                            "label": label,
                            "blocked": blocked,
                            "free": free,
                        }
                    )
                rows_out.append(
                    {
                        "direction": row.get("direction", "ltr"),
                        "cells": cells,
                    }
                )
            blocks_out.append({"rows": rows_out})
        enriched.append(
            {
                "id": group["id"],
                "style": style,
                "blocks": blocks_out,
                "slot_count": slot_count,
                "blocked_count": blocked_count,
                "free_count": free_count,
            }
        )
    return enriched


def count_keys_on_grid(groups: list[dict], keys: set[str]) -> int:
    on_grid = 0
    for group in groups:
        for block in group.get("blocks", []):
            for row in block.get("rows", []):
                for label in cells_for_row(row):
                    if label in keys:
                        on_grid += 1
    return on_grid


def count_total_slots(groups: list[dict]) -> int:
    total = 0
    for group in groups:
        for block in group.get("blocks", []):
            for row in block.get("rows", []):
                total += len(cells_for_row(row))
    return total


def count_free_on_grid(
    groups: list[dict], free_keys: set[str], blocked_keys: set[str]
) -> int:
    return count_keys_on_grid(groups, free_keys - blocked_keys)


def auth_disabled() -> bool:
    return os.environ.get("AUTH_DISABLED", "").lower() in ("1", "true", "yes")


def session_cookie_name() -> str:
    return os.environ.get("SESSION_COOKIE_NAME", "__session")


def has_valid_session_cookie(cookies) -> bool:
    value = cookies.get(session_cookie_name())
    return bool(value and str(value).strip())


def auth_login_url() -> str:
    return os.environ.get("AUTH_LOGIN_URL", "https://intoship.cloud").strip()


def create_app() -> Flask:
    configure_logging()
    env_file = APP_DIR / ".env"
    if env_file.is_file():
        load_dotenv(env_file)

    app = Flask(__name__)

    if os.environ.get("TRUST_PROXY", "1").lower() in ("1", "true", "yes"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    @app.before_request
    def require_session():
        if auth_disabled():
            return None
        if request.endpoint in ("health", "static"):
            return None
        if has_valid_session_cookie(request.cookies):
            return None
        return (
            render_template(
                "unauthorized.html",
                login_url=auth_login_url(),
                auth_domain="intoship.cloud",
                app_host="wh-eye.intoship.cloud",
            ),
            401,
        )

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    @app.get("/")
    def index():
        layout = load_layout()
        blocked_keys = load_blocked_grid_keys()
        free_keys, free_error = load_free_grid_keys_from_db()
        groups = enrich_layout(layout, blocked_keys, free_keys)
        blocked_on_grid = count_keys_on_grid(layout, blocked_keys)
        free_on_grid = count_free_on_grid(layout, free_keys, blocked_keys)
        total_slots = count_total_slots(layout)
        other_slots = total_slots - blocked_on_grid - free_on_grid
        return render_template(
            "grid.html",
            groups=groups,
            blocked_total=len(blocked_keys),
            blocked_on_grid=blocked_on_grid,
            free_total=len(free_keys),
            free_on_grid=free_on_grid,
            total_slots=total_slots,
            other_slots=other_slots,
            free_error=free_error,
        )

    return app


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    port = int(os.environ.get("PORT", "5000"))
    create_app().run(debug=debug, host="0.0.0.0", port=port)
