-- Clinic AI Intake — initial schema (spec §6)
create extension if not exists pgcrypto;

create table doctors (
  id uuid primary key default gen_random_uuid(),
  auth_user_id uuid unique references auth.users(id),
  name text not null,
  specialty text not null,
  email text not null,
  phone text,
  clinic_name text not null,
  clinic_open_time time not null default '09:00',
  created_at timestamptz not null default now()
);

create table schedule_templates (
  id uuid primary key default gen_random_uuid(),
  doctor_id uuid not null references doctors(id) on delete cascade,
  weekday smallint not null check (weekday between 0 and 6),  -- 0 = Monday
  start_time time not null,
  end_time time not null,
  slot_minutes int not null default 15,
  active boolean not null default true,
  check (end_time > start_time),
  check (slot_minutes > 0)
);

create table slots (
  id uuid primary key default gen_random_uuid(),
  doctor_id uuid not null references doctors(id) on delete cascade,
  date date not null,
  start_time time not null,
  end_time time not null,
  blocked boolean not null default false,       -- doctor can block a slot
  unique (doctor_id, date, start_time)
);

create index slots_date_idx on slots (date);

create table patients (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  phone text not null unique,                    -- E.164, e.g. +919876543210
  email text not null,
  preferred_language text not null default 'en' check (preferred_language in ('ta','en','hi')),
  created_at timestamptz not null default now()
);

create table appointments (
  id uuid primary key default gen_random_uuid(),
  slot_id uuid not null references slots(id),
  patient_id uuid not null references patients(id),
  status text not null default 'PENDING'
    check (status in ('PENDING','CONFIRMED','REJECTED','AUTO_CANCELLED')),
  consent_ai_call boolean not null,
  consent_at timestamptz not null,
  reject_reason text,
  created_at timestamptz not null default now(),
  decided_at timestamptz
);

-- One active booking per slot (prevents double booking)
create unique index one_active_booking_per_slot
  on appointments (slot_id)
  where status in ('PENDING','CONFIRMED');

create index appointments_status_idx on appointments (status);

create table calls (
  id uuid primary key default gen_random_uuid(),
  appointment_id uuid not null references appointments(id) on delete cascade,
  status text not null default 'QUEUED'
    check (status in ('QUEUED','CALLING','COMPLETED','NO_ANSWER','FAILED','FORM_SENT','FORM_SUBMITTED')),
  attempt int not null default 0,
  elevenlabs_conversation_id text,
  sip_call_id text,
  started_at timestamptz,
  ended_at timestamptz,
  duration_seconds int,
  transcript jsonb,
  failure_reason text,
  next_retry_at timestamptz,
  created_at timestamptz not null default now()
);

create index calls_appointment_idx on calls (appointment_id);
create index calls_status_idx on calls (status);

create table summaries (
  id uuid primary key default gen_random_uuid(),
  appointment_id uuid not null unique references appointments(id) on delete cascade,
  source text not null check (source in ('CALL','FORM')),
  summary jsonb not null,
  compliance_flag boolean not null default false,   -- true if AI may have given advice
  compliance_notes text,
  created_at timestamptz not null default now()
);

create table intake_forms (
  id uuid primary key default gen_random_uuid(),
  appointment_id uuid not null unique references appointments(id) on delete cascade,
  token_hash text not null unique,                   -- SHA-256 of token, never the raw token
  expires_at timestamptz not null,
  used_at timestamptz,
  answers jsonb
);

create table notifications (
  id uuid primary key default gen_random_uuid(),
  appointment_id uuid references appointments(id) on delete cascade,
  type text not null check (type in (
    'BOOKING_RECEIVED','NEW_BOOKING','DOCTOR_REMINDER','CONFIRMED',
    'REJECTED','AUTO_CANCELLED','INTAKE_FORM','SUMMARY_READY'
  )),
  recipient text not null,
  status text not null default 'SENT' check (status in ('SENT','FAILED')),
  provider_message_id text,
  error text,
  sent_at timestamptz not null default now()
);

create index notifications_appointment_idx on notifications (appointment_id);

-- RLS on everything, no policies: only the backend (service role / direct
-- Postgres connection) can touch these tables. The frontend never reads them.
alter table doctors            enable row level security;
alter table schedule_templates enable row level security;
alter table slots              enable row level security;
alter table patients           enable row level security;
alter table appointments       enable row level security;
alter table calls              enable row level security;
alter table summaries          enable row level security;
alter table intake_forms       enable row level security;
alter table notifications      enable row level security;
