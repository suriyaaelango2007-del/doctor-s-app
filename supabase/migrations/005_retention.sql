-- Retention (spec §14): the nightly job deletes the ElevenLabs conversation (transcript + audio)
-- and our transcript copy after RETENTION_DAYS, and records when it did so.
alter table calls add column data_purged_at timestamptz;
