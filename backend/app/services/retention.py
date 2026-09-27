"""Nightly data retention (spec §14, DPDP).

After RETENTION_DAYS:
- calls: delete the ElevenLabs conversation (transcript + any recording) and clear our transcript copy;
- intake forms: clear the raw answers.
The doctor's summary is kept: it is the clinical record for the visit.
"""

import logging
from datetime import datetime, timedelta

import httpx

from app import time_rules
from app.config import get_settings
from app.db import transaction
from app.services import calls

log = logging.getLogger("clinic.retention")


def delete_conversation(conversation_id: str) -> bool:
    """DELETE the conversation at ElevenLabs. 404 counts as already gone."""
    try:
        resp = httpx.delete(f"{calls.API_BASE}/conversations/{conversation_id}", headers=calls._headers(), timeout=30)
    except httpx.HTTPError as exc:
        log.warning("Retention: deleting conversation %s failed: %s", conversation_id, exc)
        return False
    if resp.status_code in (200, 204, 404):
        return True
    log.warning("Retention: deleting conversation %s -> HTTP %s", conversation_id, resp.status_code)
    return False


def purge_old_data(now: datetime | None = None) -> dict[str, int]:
    now = now or time_rules.now_ist()
    cutoff = now - timedelta(days=get_settings().retention_days)

    with transaction() as conn:
        old_calls = conn.execute(
            """
            select id, elevenlabs_conversation_id from calls
            where data_purged_at is null
              and status not in ('QUEUED', 'CALLING')
              and coalesce(ended_at, started_at, created_at) < %s
            """,
            (cutoff,),
        ).fetchall()

    purged_calls = 0
    for c in old_calls:
        conv = c["elevenlabs_conversation_id"]
        if conv and calls.is_configured() and not delete_conversation(conv):
            continue  # try again tomorrow; keep our copy until the remote one is gone
        with transaction() as conn:
            conn.execute("update calls set transcript = null, data_purged_at = %s where id = %s", (now, c["id"]))
        purged_calls += 1

    with transaction() as conn:
        forms = conn.execute(
            "update intake_forms set answers = null where answers is not null and used_at < %s",
            (cutoff,),
        ).rowcount

    result = {"calls": purged_calls, "forms": forms}
    if purged_calls or forms:
        log.info("Retention: purged %s", result)
    return result
