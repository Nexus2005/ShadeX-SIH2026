-- Resilient AI: verification adapters, reference records, infrastructure evidence,
-- reports, notifications, settings and the append-only audit trail.

create table if not exists public.verification_providers (
  code                          text primary key,
  name                          text not null,
  provider_type                 text not null check (provider_type in ('simulated', 'official')),
  description                   text,
  supported_document_categories text[] not null default '{}',
  is_enabled                    boolean not null default true,
  availability                  text not null default 'available'
                                  check (availability in ('available', 'unavailable', 'not_configured')),
  config                        jsonb not null default '{}'::jsonb,   -- non-secret settings only
  last_checked_at               timestamptz,
  updated_at                    timestamptz not null default now()
);

-- Synthetic reference data used by simulated providers and the simulated AICTE records adapter.
create table if not exists public.reference_records (
  id             uuid primary key default gen_random_uuid(),
  provider_code  text not null references public.verification_providers(code) on delete cascade,
  institution_id uuid references public.institutions(id) on delete cascade,
  record_type    text not null,
  reference_no   text,
  data           jsonb not null,
  valid_from     date,
  valid_to       date,
  is_synthetic   boolean not null default true,
  created_at     timestamptz not null default now()
);
create index if not exists idx_reference_records_lookup on public.reference_records(provider_code, reference_no);
create index if not exists idx_reference_records_inst on public.reference_records(institution_id, record_type);

create table if not exists public.verification_attempts (
  id               uuid primary key default gen_random_uuid(),
  application_id   uuid not null references public.applications(id) on delete cascade,
  document_id      uuid references public.documents(id) on delete set null,
  provider_code    text not null references public.verification_providers(code),
  reference_no     text,
  status           text not null check (status in ('verified', 'mismatch', 'expired', 'pending', 'unavailable',
                                                  'not_supported', 'manual_review_required')),
  is_simulated     boolean not null,
  request_summary  jsonb not null default '{}'::jsonb,
  response_payload jsonb not null default '{}'::jsonb,
  compared_fields  jsonb not null default '[]'::jsonb,
  error_detail     text,
  latency_ms       integer,
  requested_by     uuid references public.profiles(id) on delete set null,
  requested_at     timestamptz not null default now(),
  responded_at     timestamptz
);
create index if not exists idx_verification_app on public.verification_attempts(application_id, requested_at desc);

create table if not exists public.infrastructure_evidence (
  id                uuid primary key default gen_random_uuid(),
  application_id    uuid not null references public.applications(id) on delete cascade,
  document_id       uuid not null unique references public.documents(id) on delete cascade,
  evidence_type     text not null default 'other'
                      check (evidence_type in ('lab_photo', 'classroom_photo', 'library_photo', 'floor_plan',
                                               'building_certificate', 'other')),
  declared_label    text,
  declared_quantity integer check (declared_quantity is null or declared_quantity >= 0),
  analysis          jsonb not null default '{}'::jsonb,
  status            text not null default 'pending'
                      check (status in ('pending', 'analysed', 'needs_more_evidence', 'inspection_recommended', 'accepted')),
  reviewer_comment  text,
  reviewed_by       uuid references public.profiles(id) on delete set null,
  reviewed_at       timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create trigger trg_infra_updated before update on public.infrastructure_evidence
  for each row execute function public.set_updated_at();

create table if not exists public.report_records (
  id                 uuid primary key default gen_random_uuid(),
  application_id     uuid not null references public.applications(id) on delete cascade,
  reference_code     text not null unique,
  report_type        text not null default 'scrutiny',
  storage_backend    text not null,
  storage_path       text not null,
  sha256             char(64) not null,
  size_bytes         bigint not null,
  summary            jsonb not null default '{}'::jsonb,
  includes_simulated boolean not null default false,
  generated_by       uuid references public.profiles(id) on delete set null,
  generated_at       timestamptz not null default now()
);
create index if not exists idx_reports_app on public.report_records(application_id, generated_at desc);

create table if not exists public.notifications (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references public.profiles(id) on delete cascade,
  application_id uuid references public.applications(id) on delete cascade,
  kind           text not null,
  severity       text not null default 'info' check (severity in ('critical', 'high', 'medium', 'low', 'info')),
  title          text not null,
  body           text,
  link           text,
  dedupe_key     text,
  is_read        boolean not null default false,
  created_at     timestamptz not null default now(),
  unique (user_id, dedupe_key)
);
create index if not exists idx_notifications_user on public.notifications(user_id, is_read, created_at desc);

create table if not exists public.system_settings (
  key         text primary key,
  value       jsonb not null,
  description text,
  updated_by  uuid references public.profiles(id) on delete set null,
  updated_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Audit trail: append-only at the application/database-role level, with a SHA-256
-- hash chain so that edits made by a privileged database owner are detectable via
-- public.verify_audit_chain(). This is tamper-evident, not cryptographically immutable.
-- actor_id deliberately has no foreign key so audit rows never need to be updated.
-- ---------------------------------------------------------------------------
create table if not exists public.audit_events (
  id             bigint generated always as identity primary key,
  event_id       uuid not null unique default gen_random_uuid(),
  occurred_at    timestamptz not null default clock_timestamp(),
  actor_id       uuid,
  actor_email    text,
  actor_role     text,
  action         text not null,
  entity_type    text not null,
  entity_id      text,
  application_id uuid,
  institution_id uuid,
  metadata       jsonb not null default '{}'::jsonb,
  request_id     text,
  ip_address     text,
  prev_hash      char(64),
  event_hash     char(64) not null
);
create index if not exists idx_audit_app on public.audit_events(application_id, id desc);
create index if not exists idx_audit_entity on public.audit_events(entity_type, entity_id);
create index if not exists idx_audit_actor on public.audit_events(actor_id, id desc);
create index if not exists idx_audit_time on public.audit_events(occurred_at desc);

create or replace function public.audit_hash_input(
  p_prev text, p_event uuid, p_ts timestamptz, p_actor uuid, p_action text,
  p_entity_type text, p_entity_id text, p_application uuid, p_metadata jsonb
) returns text language sql stable as $$
  select coalesce(p_prev, 'GENESIS') || '|' || p_event::text || '|' ||
         to_char(p_ts at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '|' ||
         coalesce(p_actor::text, 'system') || '|' || p_action || '|' || p_entity_type || '|' ||
         coalesce(p_entity_id, '') || '|' || coalesce(p_application::text, '') || '|' ||
         coalesce(p_metadata, '{}'::jsonb)::text
$$;

create or replace function public.prevent_audit_mutation() returns trigger
language plpgsql as $$
begin
  raise exception 'audit_events is append-only';
end;
$$;
create trigger trg_audit_no_update before update or delete on public.audit_events
  for each row execute function public.prevent_audit_mutation();
create trigger trg_audit_no_truncate before truncate on public.audit_events
  for each statement execute function public.prevent_audit_mutation();
