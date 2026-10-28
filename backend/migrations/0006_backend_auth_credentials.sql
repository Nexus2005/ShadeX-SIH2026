-- Resilient AI: backend-managed demo credentials.
-- Backend handles auth (bcrypt + session JWTs) because Supabase Auth anon/service keys
-- were not provisioned for this project. auth_credentials stores server-side password
-- hashes; the migration 0005 RLS policies remain valid for a future Supabase-Auth path.

create table if not exists public.auth_credentials (
  user_id       uuid primary key references public.profiles(id) on delete cascade,
  password_hash text not null,
  created_at    timestamptz not null default now()
);

revoke all on public.auth_credentials from anon;
grant select, insert, update, delete on public.auth_credentials to authenticated;
