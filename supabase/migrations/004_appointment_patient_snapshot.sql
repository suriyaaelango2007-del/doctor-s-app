-- Family members often share one phone number. The patients row (keyed by phone) is the
-- contact; each appointment keeps the name, email and language entered for that booking,
-- so a later booking on the same number can't rename earlier appointments.
alter table appointments
  add column patient_name text,
  add column patient_email text,
  add column preferred_language text;

update appointments a
set patient_name = p.name,
    patient_email = p.email,
    preferred_language = p.preferred_language
from patients p
where p.id = a.patient_id;

alter table appointments
  alter column patient_name set not null,
  alter column patient_email set not null,
  alter column preferred_language set not null,
  add constraint appointments_preferred_language_check check (preferred_language in ('ta','en','hi'));
