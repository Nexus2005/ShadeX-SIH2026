-- Resilient AI: audit hash-chain functions, access helper functions and Row Level Security.
--
-- Request-scoped backend work runs as the `authenticated` role with request.jwt.claims set,
-- so auth.uid() and every policy below apply exactly as they would for a Supabase client.
-- Background jobs run on the privileged connection only after the backend has authorised
-- the triggering request.

create or replace function public.log_audit_event(
  p_action text, p_entity_type text, p_entity_id text default null,
  p_application_id uuid default null, p_institution_id uuid default null,
  p_metadata jsonb default '{}'::jsonb, p_request_id text default null,
  p_ip text default null, p_actor_id uuid default null
) returns uuid
language plpgsql security definer set search_path = public, extensions as $$
declare
  v_actor uuid := coalesce(auth.uid(), p_actor_id);
  v_email text;
  v_role  text;
  v_prev  text;
  v_ts    timestamptz := clock_timestamp();
  v_event uuid := gen_random_uuid();
  v_meta  jsonb := coalesce(p_metadata, '{}'::jsonb);
begin
  perform pg_advisory_xact_lock(7234001);
  select email, role into v_email, v_role from public.profiles where id = v_actor;
  select event_hash into v_prev from public.audit_events order by id desc limit 1;
  insert into public.audit_events (event_id, occurred_at, actor_id, actor_email, actor_role, action, entity_type,
                                   entity_id, application_id, institution_id, metadata, request_id, ip_address,
                                   prev_hash, event_hash)
  values (v_event, v_ts, v_actor, v_email, v_role, p_action, p_entity_type, p_entity_id, p_application_id,
          p_institution_id, v_meta, p_request_id, p_ip, v_prev,
          encode(digest(public.audit_hash_input(v_prev, v_event, v_ts, v_actor, p_action, p_entity_type,
                                                p_entity_id, p_application_id, v_meta), 'sha256'), 'hex'));
  return v_event;
end;
$$;

-- Recomputes every hash in order; returns the rows whose stored hash or link does not match.
create or replace function public.verify_audit_chain()
returns table (audit_id bigint, problem text)
language plpgsql stable security definer set search_path = public, extensions as $$
declare
  r record;
  v_prev text := null;
  v_expected text;
begin
  for r in select * from public.audit_events order by id loop
    if r.prev_hash is distinct from v_prev then
      audit_id := r.id; problem := 'previous-hash link mismatch'; return next;
    end if;
    v_expected := encode(digest(public.audit_hash_input(r.prev_hash, r.event_id, r.occurred_at, r.actor_id, r.action,
                                r.entity_type, r.entity_id, r.application_id, r.metadata), 'sha256'), 'hex');
    if v_expected <> r.event_hash then
      audit_id := r.id; problem := 'event hash mismatch'; return next;
    end if;
    v_prev := r.event_hash;
  end loop;
end;
$$;

-- ---------------------------------------------------------------------------
-- Access helpers (security definer so policies do not recurse through RLS)
-- ---------------------------------------------------------------------------
create or replace function public.current_app_role() returns text
language sql stable security definer set search_path = public as $$
  select role from public.profiles where id = auth.uid() and is_active
$$;

create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce(public.current_app_role() = 'admin', false)
$$;

create or replace function public.is_reviewer_or_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce(public.current_app_role() in ('admin', 'reviewer'), false)
$$;

create or replace function public.is_staff() returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce(public.current_app_role() in ('admin', 'reviewer', 'auditor'), false)
$$;

create or replace function public.is_institution_member(p_institution uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.institution_memberships m
                 join public.profiles p on p.id = m.user_id and p.is_active
                 where m.institution_id = p_institution and m.user_id = auth.uid())
$$;

create or replace function public.can_read_institution(p_institution uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select public.is_staff() or public.is_institution_member(p_institution)
$$;

create or replace function public.can_read_application(p_application uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select public.is_staff() or exists (
    select 1 from public.applications a where a.id = p_application and public.is_institution_member(a.institution_id))
$$;

create or replace function public.can_write_application(p_application uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select public.is_reviewer_or_admin() or exists (
    select 1 from public.applications a where a.id = p_application and public.is_institution_member(a.institution_id))
$$;

-- ---------------------------------------------------------------------------
-- Grants: anon gets nothing; authenticated gets table privileges filtered by RLS.
-- ---------------------------------------------------------------------------
revoke all on all tables in schema public from anon;
revoke all on all sequences in schema public from anon;
revoke execute on all functions in schema public from anon, public;
grant usage on schema public to authenticated;
grant select, insert, update, delete on all tables in schema public to authenticated;
grant usage, select on all sequences in schema public to authenticated;
grant execute on all functions in schema public to authenticated;
revoke insert, update, delete, truncate on public.audit_events from authenticated;
revoke update, delete on public.rule_versions from authenticated;

do $$
declare t text;
begin
  foreach t in array array['profiles', 'user_sessions', 'institutions', 'institution_memberships', 'application_categories',
    'applications', 'application_status_history', 'document_categories', 'documents', 'document_versions',
    'document_pages', 'document_classifications', 'extracted_fields', 'field_corrections', 'processing_jobs',
    'compliance_rules', 'rule_versions', 'validation_runs', 'rule_evaluations', 'findings', 'finding_evidence',
    'review_actions', 'verification_providers', 'reference_records', 'verification_attempts',
    'infrastructure_evidence', 'report_records', 'notifications', 'system_settings', 'audit_events']
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('alter table public.%I force row level security', t);
  end loop;
end;
$$;

-- Profiles: users see themselves; staff see everyone; only admins change roles/activation.
create policy profiles_select on public.profiles for select to authenticated
  using (id = auth.uid() or public.is_staff());
create policy profiles_admin_write on public.profiles for all to authenticated
  using (public.is_admin()) with check (public.is_admin());

create policy sessions_own on public.user_sessions for select to authenticated using (user_id = auth.uid() or public.is_admin());

-- Institutions
create policy institutions_select on public.institutions for select to authenticated
  using (public.can_read_institution(id));
create policy institutions_insert on public.institutions for insert to authenticated
  with check (public.is_reviewer_or_admin());
create policy institutions_update on public.institutions for update to authenticated
  using (public.is_reviewer_or_admin() or public.is_institution_member(id))
  with check (public.is_reviewer_or_admin() or public.is_institution_member(id));

create policy memberships_select on public.institution_memberships for select to authenticated
  using (user_id = auth.uid() or public.is_staff());
create policy memberships_admin on public.institution_memberships for all to authenticated
  using (public.is_admin()) with check (public.is_admin());

-- Reference/configuration tables: readable by every signed-in user, writable by admins.
create policy app_categories_read on public.application_categories for select to authenticated using (true);
create policy app_categories_admin on public.application_categories for all to authenticated
  using (public.is_admin()) with check (public.is_admin());
create policy doc_categories_read on public.document_categories for select to authenticated using (true);
create policy doc_categories_admin on public.document_categories for all to authenticated
  using (public.is_admin()) with check (public.is_admin());
create policy rules_read on public.compliance_rules for select to authenticated using (true);
create policy rules_admin on public.compliance_rules for all to authenticated
  using (public.is_admin()) with check (public.is_admin());
create policy rule_versions_read on public.rule_versions for select to authenticated using (true);
create policy rule_versions_insert on public.rule_versions for insert to authenticated with check (public.is_admin());
create policy providers_read on public.verification_providers for select to authenticated using (true);
create policy providers_admin on public.verification_providers for update to authenticated
  using (public.is_admin()) with check (public.is_admin());
create policy settings_read on public.system_settings for select to authenticated using (true);
create policy settings_admin on public.system_settings for all to authenticated
  using (public.is_admin()) with check (public.is_admin());
create policy reference_records_read on public.reference_records for select to authenticated using (public.is_staff());

-- Applications and application-scoped records
create policy applications_select on public.applications for select to authenticated
  using (public.can_read_institution(institution_id));
create policy applications_insert on public.applications for insert to authenticated
  with check (public.is_reviewer_or_admin() or public.is_institution_member(institution_id));
create policy applications_update on public.applications for update to authenticated
  using (public.is_reviewer_or_admin() or public.is_institution_member(institution_id))
  with check (public.is_reviewer_or_admin() or public.is_institution_member(institution_id));

create policy status_history_select on public.application_status_history for select to authenticated
  using (public.can_read_application(application_id));
create policy status_history_insert on public.application_status_history for insert to authenticated
  with check (public.can_write_application(application_id));

create policy documents_select on public.documents for select to authenticated
  using (public.can_read_application(application_id));
create policy documents_insert on public.documents for insert to authenticated
  with check (public.can_write_application(application_id));
create policy documents_update on public.documents for update to authenticated
  using (public.can_write_application(application_id)) with check (public.can_write_application(application_id));

create policy versions_select on public.document_versions for select to authenticated
  using (public.can_read_application(application_id));
create policy versions_insert on public.document_versions for insert to authenticated
  with check (public.can_write_application(application_id));

create policy pages_select on public.document_pages for select to authenticated
  using (exists (select 1 from public.document_versions v where v.id = document_version_id
                 and public.can_read_application(v.application_id)));
create policy classifications_select on public.document_classifications for select to authenticated
  using (exists (select 1 from public.document_versions v where v.id = document_version_id
                 and public.can_read_application(v.application_id)));

create policy fields_select on public.extracted_fields for select to authenticated
  using (public.can_read_application(application_id));
create policy fields_update on public.extracted_fields for update to authenticated
  using (public.can_write_application(application_id)) with check (public.can_write_application(application_id));
create policy corrections_select on public.field_corrections for select to authenticated
  using (exists (select 1 from public.extracted_fields f where f.id = extracted_field_id
                 and public.can_read_application(f.application_id)));
create policy corrections_insert on public.field_corrections for insert to authenticated
  with check (actor_id = auth.uid() and exists (select 1 from public.extracted_fields f where f.id = extracted_field_id
                 and public.can_write_application(f.application_id)));

create policy jobs_select on public.processing_jobs for select to authenticated
  using (application_id is not null and public.can_read_application(application_id));

create policy runs_select on public.validation_runs for select to authenticated
  using (public.can_read_application(application_id));
create policy evaluations_select on public.rule_evaluations for select to authenticated
  using (public.can_read_application(application_id));

-- Findings: institutions only see findings released to them.
create policy findings_select on public.findings for select to authenticated
  using (public.is_staff() or (visible_to_institution and public.is_institution_member(institution_id)));
create policy findings_update on public.findings for update to authenticated
  using (public.is_reviewer_or_admin()) with check (public.is_reviewer_or_admin());
create policy finding_evidence_select on public.finding_evidence for select to authenticated
  using (exists (select 1 from public.findings f where f.id = finding_id));

create policy review_actions_select on public.review_actions for select to authenticated
  using (public.is_staff() or (public.can_read_application(application_id)
         and (finding_id is null or exists (select 1 from public.findings f where f.id = finding_id))));
create policy review_actions_insert on public.review_actions for insert to authenticated
  with check (actor_id = auth.uid() and (public.is_reviewer_or_admin()
              or (action = 'institution_response' and public.can_write_application(application_id))));

create policy verification_select on public.verification_attempts for select to authenticated
  using (public.can_read_application(application_id));
create policy infra_select on public.infrastructure_evidence for select to authenticated
  using (public.can_read_application(application_id));
create policy infra_update on public.infrastructure_evidence for update to authenticated
  using (public.is_reviewer_or_admin()) with check (public.is_reviewer_or_admin());
create policy reports_select on public.report_records for select to authenticated
  using (public.is_staff());

create policy notifications_own on public.notifications for select to authenticated using (user_id = auth.uid());
create policy notifications_mark_read on public.notifications for update to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- Audit trail: administrators and auditors see everything; reviewers see application events.
create policy audit_select on public.audit_events for select to authenticated
  using (public.current_app_role() in ('admin', 'auditor')
         or (public.current_app_role() = 'reviewer' and application_id is not null));

-- Private storage bucket used when the Supabase Storage backend is configured.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('resilient-documents', 'resilient-documents', false, 26214400,
        array['application/pdf', 'image/png', 'image/jpeg'])
on conflict (id) do nothing;
