-- Resilient AI: documents, versions, pages, extraction and processing jobs

create table if not exists public.document_categories (
  code                   text primary key,
  name                   text not null,
  description            text,
  expected_fields        jsonb not null default '[]'::jsonb,   -- [{name,label,type,required}]
  classification_keywords jsonb not null default '[]'::jsonb,  -- ["fire", "no objection", ...]
  has_expiry             boolean not null default false,
  is_infrastructure      boolean not null default false,
  sort_order             integer not null default 100
);

create table if not exists public.documents (
  id                        uuid primary key default gen_random_uuid(),
  application_id            uuid not null references public.applications(id) on delete cascade,
  institution_id            uuid not null references public.institutions(id) on delete cascade,
  declared_category_code    text references public.document_categories(code),
  detected_category_code    text references public.document_categories(code),
  classification_confidence numeric(5, 4),
  classification_method     text,
  title                     text not null,
  current_version_id        uuid,
  current_version_no        integer not null default 0,
  status                    text not null default 'uploaded'
                              check (status in ('uploaded', 'queued', 'processing', 'processed',
                                                'failed', 'unreadable', 'deleted')),
  status_detail             text,
  is_deleted                boolean not null default false,
  created_by                uuid references public.profiles(id) on delete set null,
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now()
);
create index if not exists idx_documents_application on public.documents(application_id);
create trigger trg_documents_updated before update on public.documents
  for each row execute function public.set_updated_at();

create table if not exists public.document_versions (
  id                uuid primary key default gen_random_uuid(),
  document_id       uuid not null references public.documents(id) on delete cascade,
  application_id    uuid not null references public.applications(id) on delete cascade,
  version_no        integer not null,
  original_filename text not null,
  mime_type         text not null,
  file_ext          text not null,
  size_bytes        bigint not null check (size_bytes > 0),
  sha256            char(64) not null,
  storage_backend   text not null,
  storage_path      text not null,
  page_count        integer,
  text_layer_chars  integer,
  ocr_used          boolean not null default false,
  mean_ocr_confidence numeric(5, 4),
  quality           jsonb not null default '{}'::jsonb,
  pdf_metadata      jsonb not null default '{}'::jsonb,
  forensics         jsonb not null default '{}'::jsonb,
  phash             text,
  processing_status text not null default 'pending'
                      check (processing_status in ('pending', 'processing', 'processed', 'failed', 'unreadable')),
  processing_error  text,
  processed_at      timestamptz,
  change_note       text,
  uploaded_by       uuid references public.profiles(id) on delete set null,
  uploaded_at       timestamptz not null default now(),
  unique (document_id, version_no)
);
create index if not exists idx_doc_versions_document on public.document_versions(document_id);
create index if not exists idx_doc_versions_sha on public.document_versions(sha256);
create index if not exists idx_doc_versions_app on public.document_versions(application_id);

alter table public.documents
  add constraint fk_documents_current_version
  foreign key (current_version_id) references public.document_versions(id) on delete set null;

create table if not exists public.document_pages (
  id                  uuid primary key default gen_random_uuid(),
  document_version_id uuid not null references public.document_versions(id) on delete cascade,
  page_no             integer not null check (page_no >= 1),
  width               numeric not null,
  height              numeric not null,
  text_source         text not null check (text_source in ('text_layer', 'ocr', 'none')),
  text                text not null default '',
  lines               jsonb not null default '[]'::jsonb,  -- [{text, bbox:[x0,y0,x1,y1] normalised 0..1, conf}]
  ocr_confidence      numeric(5, 4),
  quality             jsonb not null default '{}'::jsonb,
  phash               text,
  unique (document_version_id, page_no)
);

create table if not exists public.document_classifications (
  id                  uuid primary key default gen_random_uuid(),
  document_version_id uuid not null references public.document_versions(id) on delete cascade,
  predicted_category  text references public.document_categories(code),
  confidence          numeric(5, 4),
  method              text not null,          -- keyword_rules | gemini | declared
  model_id            text,
  scores              jsonb not null default '{}'::jsonb,
  rationale           text,
  created_at          timestamptz not null default now()
);
create index if not exists idx_classifications_version on public.document_classifications(document_version_id);

create table if not exists public.extracted_fields (
  id                  uuid primary key default gen_random_uuid(),
  application_id      uuid not null references public.applications(id) on delete cascade,
  document_id         uuid not null references public.documents(id) on delete cascade,
  document_version_id uuid not null references public.document_versions(id) on delete cascade,
  field_name          text not null,
  label               text not null,
  value_type          text not null default 'text' check (value_type in ('text', 'date', 'number', 'identifier', 'list', 'boolean')),
  raw_value           text,
  normalized_value    text,
  confidence          numeric(5, 4),
  page_no             integer,
  bbox                jsonb,                  -- [x0,y0,x1,y1] normalised 0..1
  extraction_method   text not null,          -- text_layer_pattern | ocr_pattern | gemini_structured | manual
  model_id            text,
  status              text not null default 'extracted'
                        check (status in ('extracted', 'low_confidence', 'missing', 'corrected', 'confirmed')),
  corrected_value     text,
  corrected_by        uuid references public.profiles(id) on delete set null,
  corrected_at        timestamptz,
  is_current          boolean not null default true,
  created_at          timestamptz not null default now()
);
create index if not exists idx_fields_app on public.extracted_fields(application_id) where is_current;
create index if not exists idx_fields_document on public.extracted_fields(document_id);

create table if not exists public.field_corrections (
  id                 uuid primary key default gen_random_uuid(),
  extracted_field_id uuid not null references public.extracted_fields(id) on delete cascade,
  old_value          text,
  new_value          text,
  reason             text not null,
  actor_id           uuid references public.profiles(id) on delete set null,
  created_at         timestamptz not null default now()
);

create table if not exists public.processing_jobs (
  id                  uuid primary key default gen_random_uuid(),
  job_type            text not null check (job_type in ('process_document', 'validate_application', 'verify_application')),
  application_id      uuid references public.applications(id) on delete cascade,
  document_id         uuid references public.documents(id) on delete cascade,
  document_version_id uuid references public.document_versions(id) on delete cascade,
  status              text not null default 'queued' check (status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  attempts            integer not null default 0,
  max_attempts        integer not null default 3,
  payload             jsonb not null default '{}'::jsonb,
  result              jsonb not null default '{}'::jsonb,
  stages              jsonb not null default '[]'::jsonb,
  error               text,
  idempotency_key     text unique,
  locked_by           text,
  queued_at           timestamptz not null default now(),
  run_after           timestamptz not null default now(),
  started_at          timestamptz,
  finished_at         timestamptz,
  created_by          uuid references public.profiles(id) on delete set null
);
create index if not exists idx_jobs_queue on public.processing_jobs(status, run_after) where status = 'queued';
create index if not exists idx_jobs_app on public.processing_jobs(application_id, queued_at desc);
