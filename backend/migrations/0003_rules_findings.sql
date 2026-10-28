-- Resilient AI: versioned compliance rules, validation runs, findings and review actions

create table if not exists public.compliance_rules (
  id                 uuid primary key default gen_random_uuid(),
  rule_code          text not null unique check (rule_code ~ '^[A-Z0-9][A-Z0-9_.-]{2,63}$'),
  name               text not null,
  category           text not null check (category in ('completeness', 'expiry', 'consistency', 'regulatory',
                                                          'authenticity', 'verification', 'infrastructure')),
  current_version_id uuid,
  is_active          boolean not null default true,
  created_by         uuid references public.profiles(id) on delete set null,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create trigger trg_rules_updated before update on public.compliance_rules
  for each row execute function public.set_updated_at();

-- Rule versions are immutable once created: edits create a new version, so historical
-- evaluations keep pointing at the exact definition that produced them.
create table if not exists public.rule_versions (
  id               uuid primary key default gen_random_uuid(),
  rule_id          uuid not null references public.compliance_rules(id) on delete cascade,
  version          integer not null check (version >= 1),
  name             text not null,
  description      text not null,
  definition       jsonb not null,           -- evaluator type + parameters (see app/services/rule_engine.py)
  applies_to       text[] not null default array['EOA'],
  severity         text not null check (severity in ('critical', 'high', 'medium', 'low', 'info')),
  effective_from   date not null,
  effective_to     date,
  source_document  text,
  source_clause    text,
  source_page      text,
  source_quote     text,
  is_demo_rule     boolean not null default false,  -- true = not verified against an official source
  change_note      text,
  created_by       uuid references public.profiles(id) on delete set null,
  created_at       timestamptz not null default now(),
  unique (rule_id, version),
  check (effective_to is null or effective_to >= effective_from)
);
alter table public.compliance_rules
  add constraint fk_rules_current_version foreign key (current_version_id)
  references public.rule_versions(id) on delete set null;

create or replace function public.prevent_rule_version_update() returns trigger
language plpgsql as $$
begin
  raise exception 'rule_versions are immutable; create a new version instead';
end;
$$;
create trigger trg_rule_versions_immutable before update on public.rule_versions
  for each row execute function public.prevent_rule_version_update();

create table if not exists public.validation_runs (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications(id) on delete cascade,
  evaluation_date date not null default current_date,
  status          text not null default 'running' check (status in ('running', 'completed', 'failed')),
  summary         jsonb not null default '{}'::jsonb,
  triggered_by    uuid references public.profiles(id) on delete set null,
  started_at      timestamptz not null default now(),
  finished_at     timestamptz
);
create index if not exists idx_validation_runs_app on public.validation_runs(application_id, started_at desc);

create table if not exists public.rule_evaluations (
  id                uuid primary key default gen_random_uuid(),
  validation_run_id uuid not null references public.validation_runs(id) on delete cascade,
  application_id    uuid not null references public.applications(id) on delete cascade,
  rule_version_id   uuid not null references public.rule_versions(id) on delete restrict,
  outcome           text not null check (outcome in ('pass', 'fail', 'needs_review', 'not_applicable', 'unable_to_evaluate')),
  explanation       text not null,
  inputs            jsonb not null default '{}'::jsonb,
  evidence          jsonb not null default '[]'::jsonb,
  evaluated_at      timestamptz not null default now()
);
create index if not exists idx_rule_evals_run on public.rule_evaluations(validation_run_id);
create index if not exists idx_rule_evals_app on public.rule_evaluations(application_id);

create table if not exists public.findings (
  id                 uuid primary key default gen_random_uuid(),
  application_id     uuid not null references public.applications(id) on delete cascade,
  institution_id     uuid not null references public.institutions(id) on delete cascade,
  validation_run_id  uuid references public.validation_runs(id) on delete set null,
  fingerprint        text not null,
  finding_code       text not null,
  category           text not null check (category in ('completeness', 'expiry', 'consistency', 'regulatory',
                                                         'authenticity', 'verification', 'infrastructure', 'quality')),
  severity           text not null check (severity in ('critical', 'high', 'medium', 'low', 'info')),
  status             text not null default 'open'
                       check (status in ('open', 'under_review', 'confirmed', 'dismissed', 'resolved', 'needs_clarification')),
  title              text not null,
  description        text not null,
  expected_value     text,
  observed_value     text,
  recommended_action text,
  detection_method   text not null,
  confidence         numeric(5, 4),
  rule_version_id    uuid references public.rule_versions(id) on delete set null,
  rule_evaluation_id uuid references public.rule_evaluations(id) on delete set null,
  document_id        uuid references public.documents(id) on delete set null,
  is_simulated       boolean not null default false,
  is_advisory        boolean not null default true,
  visible_to_institution boolean not null default true,
  assigned_to        uuid references public.profiles(id) on delete set null,
  first_detected_at  timestamptz not null default now(),
  last_detected_at   timestamptz not null default now(),
  resolved_at        timestamptz,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  unique (application_id, fingerprint)
);
create index if not exists idx_findings_app on public.findings(application_id);
create index if not exists idx_findings_status on public.findings(status, severity);
create trigger trg_findings_updated before update on public.findings
  for each row execute function public.set_updated_at();

create table if not exists public.finding_evidence (
  id                  uuid primary key default gen_random_uuid(),
  finding_id          uuid not null references public.findings(id) on delete cascade,
  document_id         uuid references public.documents(id) on delete set null,
  document_version_id uuid references public.document_versions(id) on delete set null,
  extracted_field_id  uuid references public.extracted_fields(id) on delete set null,
  page_no             integer,
  bbox                jsonb,
  excerpt             text,
  role                text not null default 'primary' check (role in ('primary', 'source_a', 'source_b', 'reference', 'duplicate_of')),
  label               text,
  created_at          timestamptz not null default now()
);
create index if not exists idx_finding_evidence_finding on public.finding_evidence(finding_id);

create table if not exists public.review_actions (
  id             uuid primary key default gen_random_uuid(),
  application_id uuid not null references public.applications(id) on delete cascade,
  finding_id     uuid references public.findings(id) on delete cascade,
  actor_id       uuid references public.profiles(id) on delete set null,
  action         text not null check (action in ('assign', 'comment', 'start_review', 'confirm', 'dismiss',
                                                 'request_clarification', 'resolve', 'reopen', 'auto_resolve',
                                                 'institution_response', 'application_decision')),
  from_status    text,
  to_status      text,
  reason_code    text,
  comment        text,
  created_at     timestamptz not null default now()
);
create index if not exists idx_review_actions_finding on public.review_actions(finding_id, created_at);
create index if not exists idx_review_actions_app on public.review_actions(application_id, created_at);
