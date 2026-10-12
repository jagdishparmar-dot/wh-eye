"""One-off connectivity check for DATABASE_URL and free_locations_mtr."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")

url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
if not url:
    print("FAIL: DATABASE_URL / POSTGRES_URL not set in .env")
    sys.exit(1)

parsed = urlparse(url)
print(
    "Config:",
    f"user={parsed.username or '?'}",
    f"host={parsed.hostname or '?'}",
    f"port={parsed.port or 5432}",
    f"database={(parsed.path or '').lstrip('/') or '?'}",
)

try:
    with psycopg.connect(url, connect_timeout=10, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user")
            meta = cur.fetchone()
            print("OK: connected")
            print("  database:", meta["current_database"])
            print("  user:", meta["current_user"])
            cur.execute("SELECT COUNT(*) AS n FROM public.free_locations_mtr")
            print("  free_locations_mtr rows:", cur.fetchone()["n"])
            cur.execute(
                """
                SELECT warehouse_id, location_id, chamber_code, seq_no, assigned_to
                FROM public.free_locations_mtr
                LIMIT 3
                """
            )
            for row in cur.fetchall():
                print("  sample:", row["location_id"], row.get("chamber_code"))
except Exception as exc:
    print("FAIL:", type(exc).__name__, exc)
    if "does not exist" in str(exc) and parsed.path:
        from urllib.parse import urlunparse

        admin_url = urlunparse(parsed._replace(path="/postgres"))
        try:
            with psycopg.connect(
                admin_url, connect_timeout=10, row_factory=dict_row
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT datname FROM pg_database
                        WHERE datistemplate = false
                        ORDER BY datname
                        """
                    )
                    names = [r["datname"] for r in cur.fetchall()]
            print("Hint: PostgreSQL is up; available databases:", ", ".join(names))
        except Exception as list_exc:
            print("Could not list databases:", list_exc)
    sys.exit(1)
