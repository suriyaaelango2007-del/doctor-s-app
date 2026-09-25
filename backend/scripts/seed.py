"""Create (or update) the clinic's doctor and weekly schedule, then generate tomorrow's slots.

First create the doctor's login in Supabase: Dashboard -> Authentication -> Users -> Add user
(email + password, auto-confirm). Then run, from backend/:

  uv run python -m scripts.seed --email doctor@example.com --name "Dr. Kumar" \
      --clinic "Kumar Skin Clinic" --specialty dermatology \
      --hours 10:00-13:00 --hours 17:00-20:00 --days 0-5 --slot-minutes 15
"""

import argparse
from datetime import time

from app.config import get_settings
from app.db import close_pool, open_pool, transaction
from app.services import booking


def parse_hours(s: str) -> tuple[time, time]:
    start, end = s.split("-")
    return time.fromisoformat(start), time.fromisoformat(end)


def parse_days(s: str) -> list[int]:
    if "-" in s:
        a, b = (int(x) for x in s.split("-"))
        return list(range(a, b + 1))
    return [int(x) for x in s.split(",")]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--email", required=True, help="Doctor's Supabase Auth email")
    p.add_argument("--name", required=True)
    p.add_argument("--clinic", required=True)
    p.add_argument("--specialty", default="dermatology")
    p.add_argument("--phone")
    p.add_argument("--open-time", default="09:00", help="Clinic opening time (form links expire then)")
    p.add_argument("--hours", action="append", help="HH:MM-HH:MM, repeatable. Default 10:00-13:00")
    p.add_argument("--days", default="0-5", help="Weekdays, 0=Mon. '0-5' or '0,2,4'. Default Mon-Sat")
    p.add_argument("--slot-minutes", type=int, default=15)
    args = p.parse_args()

    hours = [parse_hours(h) for h in (args.hours or ["10:00-13:00"])]
    days = parse_days(args.days)

    open_pool(get_settings().database_url)
    try:
        with transaction() as conn:
            user = conn.execute(
                "select id from auth.users where lower(email) = lower(%s)", (args.email,)
            ).fetchone()
            if user is None:
                raise SystemExit(
                    f"No Supabase Auth user with email {args.email}. "
                    "Create it in Dashboard -> Authentication -> Users first."
                )

            doctor = conn.execute("select id from doctors order by created_at limit 1").fetchone()
            values = (user["id"], args.name, args.specialty, args.email, args.phone, args.clinic, args.open_time)
            if doctor:
                conn.execute(
                    """
                    update doctors set auth_user_id=%s, name=%s, specialty=%s, email=%s,
                      phone=%s, clinic_name=%s, clinic_open_time=%s
                    where id=%s
                    """,
                    (*values, doctor["id"]),
                )
                doctor_id = doctor["id"]
                print(f"Updated doctor {doctor_id}")
            else:
                doctor_id = conn.execute(
                    """
                    insert into doctors (auth_user_id, name, specialty, email, phone, clinic_name, clinic_open_time)
                    values (%s, %s, %s, %s, %s, %s, %s) returning id
                    """,
                    values,
                ).fetchone()["id"]
                print(f"Created doctor {doctor_id}")

            # Replace the weekly schedule. Existing slots are left alone.
            conn.execute("delete from schedule_templates where doctor_id = %s", (doctor_id,))
            for day in days:
                for start, end in hours:
                    conn.execute(
                        """
                        insert into schedule_templates (doctor_id, weekday, start_time, end_time, slot_minutes)
                        values (%s, %s, %s, %s, %s)
                        """,
                        (doctor_id, day, start, end, args.slot_minutes),
                    )
            print(f"Schedule: days {days}, hours {[f'{s:%H:%M}-{e:%H:%M}' for s, e in hours]}, "
                  f"{args.slot_minutes}-min slots")

        n = booking.generate_tomorrows_slots()
        print(f"Generated {n} slot(s) for tomorrow.")
    finally:
        close_pool()


if __name__ == "__main__":
    main()
