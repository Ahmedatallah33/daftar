-- Daftar Sprint 3A-2A — Account Foundation (schema)
-- Development project only (daftar-development / thzwrbicieyilufasfoo).
--
-- Scope: provider-neutral, multi-tenant account spine. Tables are keyed on
-- workspace_id so future business tables (students, guardians, groups,
-- sessions, attendance, plans, invoices, payments, operating profiles) can
-- attach to the same tenant boundary and reuse the workspace RLS helpers.
-- No business tables are created here.

-- gen_random_uuid() is built into PostgreSQL 17; no extension required.

-- ---------------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- profiles — one row per authenticated user
-- ---------------------------------------------------------------------------
create table public.profiles (
  id           uuid primary key references auth.users (id) on delete cascade,
  display_name text,
  locale       text not null default 'ar',
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create trigger profiles_set_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- workspaces — the tenant boundary; every user owns at least one
-- ---------------------------------------------------------------------------
create table public.workspaces (
  id         uuid primary key default gen_random_uuid(),
  owner_id   uuid not null references auth.users (id) on delete cascade,
  name       text not null default 'My Workspace',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index workspaces_owner_id_idx on public.workspaces (owner_id);

create trigger workspaces_set_updated_at
  before update on public.workspaces
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- workspace_members — membership + role within a workspace
-- ---------------------------------------------------------------------------
create table public.workspace_members (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces (id) on delete cascade,
  user_id      uuid not null references auth.users (id) on delete cascade,
  role         text not null default 'member'
                 check (role in ('owner', 'admin', 'member')),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (workspace_id, user_id)
);

create index workspace_members_workspace_id_idx on public.workspace_members (workspace_id);
create index workspace_members_user_id_idx on public.workspace_members (user_id);

create trigger workspace_members_set_updated_at
  before update on public.workspace_members
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- devices — registered installations bound to a workspace + user
-- ---------------------------------------------------------------------------
create table public.devices (
  id                uuid primary key default gen_random_uuid(),
  workspace_id      uuid not null references public.workspaces (id) on delete cascade,
  user_id           uuid not null references auth.users (id) on delete cascade,
  installation_uuid uuid not null,
  display_name      text,
  last_seen_at      timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (workspace_id, installation_uuid)
);

create index devices_workspace_id_idx on public.devices (workspace_id);
create index devices_user_id_idx on public.devices (user_id);

create trigger devices_set_updated_at
  before update on public.devices
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- consents — per-user consent records scoped to a workspace
-- ---------------------------------------------------------------------------
create table public.consents (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces (id) on delete cascade,
  user_id      uuid not null references auth.users (id) on delete cascade,
  consent_type text not null,
  granted      boolean not null default false,
  version      text not null default 'v1',
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create index consents_workspace_id_idx on public.consents (workspace_id);
create index consents_user_id_idx on public.consents (user_id);

create trigger consents_set_updated_at
  before update on public.consents
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- account_events — append-only audit trail (no updated_at by design)
-- ---------------------------------------------------------------------------
create table public.account_events (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces (id) on delete cascade,
  user_id      uuid not null references auth.users (id) on delete cascade,
  event_type   text not null,
  metadata     jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now()
);

create index account_events_workspace_id_idx on public.account_events (workspace_id);
create index account_events_user_id_idx on public.account_events (user_id);
create index account_events_created_at_idx on public.account_events (created_at);
