-- Webhooks and the recovery job look calls up by ElevenLabs conversation id.
create unique index calls_conversation_id_idx
  on calls (elevenlabs_conversation_id)
  where elevenlabs_conversation_id is not null;

-- The call worker scans for due retries.
create index calls_retry_idx on calls (status, next_retry_at);
