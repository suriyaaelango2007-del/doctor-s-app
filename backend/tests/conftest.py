"""Test setup: a throwaway Postgres (docker-compose.test.yml), never Supabase.

Override with TEST_DATABASE_URL if you run Postgres elsewhere.
"""

import os
from datetime import date, datetime, time

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:54329/clinic_test")

# Must be set before app.config is imported anywhere.
# Real env vars beat backend/.env, so this also stops tests from picking up real keys.
os.environ.update(
    DATABASE_URL=TEST_DB,
    SUPABASE_URL="https://test-project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY="",
    SUPABASE_JWT_SECRET="",
    EMAIL_API_KEY="",
    ENVIRONMENT="test",
    SCHEDULER_ENABLED="false",
    TIME_OFFSET_MINUTES="0",
)

import psycopg  # noqa: E402
import pytest  # noqa: E402

from app import db  # noqa: E402
from app.config import IST  # noqa: E402
from scripts.migrate import migrate  # noqa: E402

TABLES = [
    "notifications", "intake_forms", "summaries", "calls", "appointments",
    "patients", "slots", "schedule_templates", "doctors",
]


@pytest.fixture(scope="session", autouse=True)
def database():
    try:
        conn = psycopg.connect(TEST_DB, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as exc:
        pytest.exit(
            f"Test Postgres not reachable ({exc}).\n"
            "Start it with: docker compose -f docker-compose.test.yml up -d",
            returncode=1,
        )
    with conn:
        # Fresh schema each session; stub Supabase's auth.users.
        conn.execute("drop schema if exists public cascade")
        conn.execute("create schema public")
        conn.execute("drop schema if exists auth cascade")
        conn.execute("create schema auth")
        conn.execute("create table auth.users (id uuid primary key, email text)")
    migrate(TEST_DB)
    db.open_pool(TEST_DB)
    yield
    db.close_pool()


@pytest.fixture(autouse=True)
def clean_tables(database):
    with db.transaction() as conn:
        conn.execute(f"truncate {', '.join(TABLES)}, auth.users cascade")
    yield


def ist(d: date, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm, ss), tzinfo=IST)


# A fixed "today" (a Wednesday), so tomorrow is a Thursday (weekday 3).
TODAY = date(2026, 9, 23)
TOMORROW = date(2026, 9, 24)


@pytest.fixture
def doctor():
    with db.transaction() as conn:
        user_id = conn.execute(
            "insert into auth.users (id, email) values (gen_random_uuid(), 'doc@test.in') returning id"
        ).fetchone()["id"]
        doc = conn.execute(
            """
            insert into doctors (auth_user_id, name, specialty, email, clinic_name)
            values (%s, 'Dr. Kumar', 'dermatology', 'doc@test.in', 'Kumar Skin Clinic')
            returning *
            """,
            (user_id,),
        ).fetchone()
        for wd in range(7):
            conn.execute(
                """
                insert into schedule_templates (doctor_id, weekday, start_time, end_time, slot_minutes)
                values (%s, %s, '10:00', '11:00', 15)
                """,
                (doc["id"], wd),
            )
    return doc


@pytest.fixture
def slots(doctor):
    """Tomorrow's four slots (10:00, 10:15, 10:30, 10:45)."""
    from app.services import booking

    booking.generate_slots_for(TOMORROW)
    with db.transaction() as conn:
        return conn.execute(
            "select * from slots where date = %s order by start_time", (TOMORROW,)
        ).fetchall()
