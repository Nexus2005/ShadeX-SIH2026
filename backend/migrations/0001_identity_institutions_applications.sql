-- Resilient AI: identity, institutions and applications
-- All timestamps are timestamptz (stored in UTC). UUID primary keys throughout.

create extension if not exists pgcrypto;

create or replace function public.set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- Profiles: one row per Supabase Auth user (auth.users). Role lives here and is
-- only changeable by administrators (enforced by RLS + backend).
-- ---------------------------------------------------------------------------
create table if not exists public.profiles (
  id            uuid primary key references auth.users(id) on delete cascade,
  email         text not null unique,
  full_name     text not null,
  role          text not null check (role in ('admin', 'reviewer', 'institution', 'auditor')),
  is_active     boolean not null default true,
  is_demo_account boolean not null default false,
  last_login_at timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create trigger trg_profiles_updated before update on public.profiles
  for each row execute function public.set_updated_at();

create table if not exists public.user_sessions (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references public.profiles(id) on delete cascade,
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null,
  revoked_at  timestamptz,
  ip_address  text,
  user_agent  text
);
create index if not exists idx_user_sessions_user on public.user_sessions(user_id);

-- ---------------------------------------------------------------------------
-- Institutions (synthetic for the demo unless an authorized integration exists)
-- ---------------------------------------------------------------------------
create table if not exists public.institutions (
  id                 uuid primary key default gen_random_uuid(),
  name               text not null check (length(trim(name)) between 3 and 200),
  aicte_pid          text not null unique check (aicte_pid ~ '^[A-Za-z0-9-]{3,32}$'),
  institution_type   text not null default 'Engineering and Technology',
  trust_society_name text,
  address_line       text,
  city               text,
  district           text,
  state              text,
  pincode            text check (pincode is null or pincode ~ '^[0-9]{6}$'),
  contact_email      text,
  contact_phone      text,
  established_year   integer check (established_year is null or established_year between 1800 and 2100),
  is_synthetic       boolean not null default true,
  created_by         uuid references public.profiles(id) on delete set null,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create trigger trg_institutions_updated before update on public.institutions
  for each row execute function public.set_updated_at();

create table if not exists public.institution_memberships (
  id             uuid primary key default gen_random_uuid(),
  institution_id uuid not null references public.institutions(id) on delete cascade,
  user_id        uuid not null references public.profiles(id) on delete cascade,
  member_role    text not null default 'representative' check (member_role in ('representative', 'head')),
  created_at     timestamptz not null default now(),
  unique (institution_id, user_id)
);
create index if not exists idx_memberships_user on public.institution_memberships(user_id);

-- ---------------------------------------------------------------------------
-- Application categories and applications
-- ---------------------------------------------------------------------------
create table if not exists public.application_categories (
  code             text primary key,
  name             text not null,
  description      text,
  is_enabled       boolean not null default false,
  source_reference text,
  sort_order       integer not null default 100
);

create sequence if not exists public.application_reference_seq start 1001;

create table if not exists public.applications (
  id                   uuid primary key default gen_random_uuid(),
  reference_no         text not null unique
                         default ('RAI-' || to_char(now(), 'YYYY') || '-' || lpad(nextval('public.application_reference_seq')::text, 6, '0')),
  institution_id       uuid not null references public.institutions(id) on delete restrict,
  category_code        text not null references public.application_categories(code),
  academic_year        text not null check (academic_year ~ '^[0-9]{4}-[0-9]{2}$'),
  title                text not null,
  description          text,
  status               text not null default 'draft'
                         check (status in ('draft', 'submitted', 'processing', 'needs_correction',
                                           'under_review', 'reviewed', 'closed')),
  declared_data        jsonb not null default '{}'::jsonb,
  priority             text not null default 'normal' check (priority in ('low', 'normal', 'high', 'urgent')),
  assigned_reviewer_id uuid references public.profiles(id) on delete set null,
  submitted_at         timestamptz,
  last_processed_at    timestamptz,
  last_validated_at    timestamptz,
  risk_summary         jsonb not null default '{}'::jsonb,
  is_synthetic         boolean not null default true,
  created_by           uuid references public.profiles(id) on delete set null,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);
create index if not exists idx_applications_institution on public.applications(institution_id);
create index if not exists idx_applications_status on public.applications(status);
create index if not exists idx_applications_reviewer on public.applications(assigned_reviewer_id);
create trigger trg_applications_updated before update on public.applications
  for each row execute function public.set_updated_at();

create table if not exists public.application_status_history (
  id             uuid primary key default gen_random_uuid(),
  application_id uuid not null references public.applications(id) on delete cascade,
  from_status    text,
  to_status      text not null,
  actor_id       uuid references public.profiles(id) on delete set null,
  reason         text,
  created_at     timestamptz not null default now()
);
create index if not exists idx_status_history_app on public.application_status_history(application_id, created_at);
