"""Apply supabase/migrations/*.sql in order, once each.

Usage (from backend/):  uv run python -m scripts.migrate
"""

import sys
from pathlib import Path

import psycopg

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def migrate(conninfo: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(conninfo, prepare_threshold=None) as conn:
        conn.execute(
            """
            create table if not exists public.schema_migrations (
              name text primary key,
              applied_at timestamptz not null default now()
            )
            """
        )
        conn.execute("alter table public.schema_migrations enable row level security")
        conn.commit()
        done = {r[0] for r in conn.execute("select name from public.schema_migrations")}

        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in done:
                continue
            print(f"Applying {path.name} ...")
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("insert into public.schema_migrations (name) values (%s)", (path.name,))
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    applied = migrate(get_settings().database_url)
    print(f"Applied {len(applied)} migration(s)." if applied else "Database is up to date.")
    sys.exit(0)
