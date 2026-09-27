-- Telephony moved from a SIP trunk to Twilio (spec §6, §8).
alter table calls rename column sip_call_id to twilio_call_sid;
