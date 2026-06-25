-- Daftar Sprint 3A-2A — Account Foundation (RLS + policies)
-- Development project only.
--
-- Proves tenant isolation: a member of Workspace A cannot read, insert,
-- update, or delete data belonging to Workspace B. Workspace membership is
-- resolved through SECURITY DEFINER helpers so the workspace_members policies
-- do not recurse on themselves.

-- ---------------------------------------------------------------------------
-- Membership helpers (SECURITY DEFINER, locked search_path)
-- ---------------------------------------------------------------------------
create or replace function public.is_workspace_member(p_workspace_id uuid)
returns boolean
language sql
security definer
stable
set search_path = ''
as $$
  select exists (
    select 1
    from public.workspace_members m
    where m.workspace_id = p_workspace_id
      and m.user_id = (select auth.uid())
  );
$$;

create or replace function public.is_workspace_owner(p_workspace_id uuid)
returns boolean
language sql
security definer
stable
set search_path = ''
as $$
  select exists (
    select 1
    from public.workspaces w
    where w.id = p_workspace_id
      and w.owner_id = (select auth.uid())
  );
$$;

revoke execute on function public.is_workspace_member(uuid) from anon, public;
revoke execute on function public.is_workspace_owner(uuid) from anon, public;
grant execute on function public.is_workspace_member(uuid) to authenticated;
grant execute on function public.is_workspace_owner(uuid) to authenticated;

-- ---------------------------------------------------------------------------
-- Enable RLS on all user-facing tables
-- ---------------------------------------------------------------------------
alter table public.profiles          enable row level security;
alter table public.workspaces        enable row level security;
alter table public.workspace_members enable row level security;
alter table public.devices           enable row level security;
alter table public.consents          enable row level security;
alter table public.account_events    enable row level security;

-- Table privileges: authenticated only. account_events is append-only
-- (select + insert), enforced both by grant and by absence of update/delete
-- policies. anon receives no access to account tables.
grant select, insert, update, delete
  on public.profiles, public.workspaces, public.workspace_members,
     public.devices, public.consents
  to authenticated;
grant select, insert on public.account_events to authenticated;

-- ---------------------------------------------------------------------------
-- profiles — self-scoped (id = auth.uid())
-- ---------------------------------------------------------------------------
create policy profiles_select_self on public.profiles
  for select to authenticated
  using (id = (select auth.uid()));

create policy profiles_insert_self on public.profiles
  for insert to authenticated
  with check (id = (select auth.uid()));

create policy profiles_update_self on public.profiles
  for update to authenticated
  using (id = (select auth.uid()))
  with check (id = (select auth.uid()));

create policy profiles_delete_self on public.profiles
  for delete to authenticated
  using (id = (select auth.uid()));

-- ---------------------------------------------------------------------------
-- workspaces — readable by members, mutable only by the owner
-- ---------------------------------------------------------------------------
create policy workspaces_select_member on public.workspaces
  for select to authenticated
  using (public.is_workspace_member(id));

create policy workspaces_insert_owner on public.workspaces
  for insert to authenticated
  with check (owner_id = (select auth.uid()));

create policy workspaces_update_owner on public.workspaces
  for update to authenticated
  using (public.is_workspace_owner(id))
  with check (public.is_workspace_owner(id));

create policy workspaces_delete_owner on public.workspaces
  for delete to authenticated
  using (public.is_workspace_owner(id));

-- ---------------------------------------------------------------------------
-- workspace_members — visible to members, managed by the owner
-- ---------------------------------------------------------------------------
create policy workspace_members_select_member on public.workspace_members
  for select to authenticated
  using (public.is_workspace_member(workspace_id));

create policy workspace_members_insert_owner on public.workspace_members
  for insert to authenticated
  with check (public.is_workspace_owner(workspace_id));

create policy workspace_members_update_owner on public.workspace_members
  for update to authenticated
  using (public.is_workspace_owner(workspace_id))
  with check (public.is_workspace_owner(workspace_id));

create policy workspace_members_delete_owner on public.workspace_members
  for delete to authenticated
  using (public.is_workspace_owner(workspace_id));

-- ---------------------------------------------------------------------------
-- devices — scoped to workspace membership; writes limited to the owning user
-- ---------------------------------------------------------------------------
create policy devices_select_member on public.devices
  for select to authenticated
  using (public.is_workspace_member(workspace_id));

create policy devices_insert_self on public.devices
  for insert to authenticated
  with check (
    public.is_workspace_member(workspace_id)
    and user_id = (select auth.uid())
  );

create policy devices_update_self on public.devices
  for update to authenticated
  using (
    public.is_workspace_member(workspace_id)
    and user_id = (select auth.uid())
  )
  with check (
    public.is_workspace_member(workspace_id)
    and user_id = (select auth.uid())
  );

create policy devices_delete_self on public.devices
  for delete to authenticated
  using (
    public.is_workspace_member(workspace_id)
    and user_id = (select auth.uid())
  );

-- ---------------------------------------------------------------------------
-- consents — scoped to workspace membership; writes limited to the owning user
-- ---------------------------------------------------------------------------
create policy consents_select_member on public.consents
  for select to authenticated
  using (public.is_workspace_member(workspace_id));

create policy consents_insert_self on public.consents
  for insert to authenticated
  with check (
    public.is_workspace_member(workspace_id)
    and user_id = (select auth.uid())
  );

create policy consents_update_self on public.consents
  for update to authenticated
  using (
    public.is_workspace_member(workspace_id)
    and user_id = (select auth.uid())
  )
  with check (
    public.is_workspace_member(workspace_id)
    and user_id = (select auth.uid())
  );

create policy consents_delete_self on public.consents
  for delete to authenticated
  using (
    public.is_workspace_member(workspace_id)
    and user_id = (select auth.uid())
  );

-- ---------------------------------------------------------------------------
-- account_events — append-only: members read, owning user inserts.
-- No update/delete policy exists, so those operations are denied.
-- ---------------------------------------------------------------------------
create policy account_events_select_member on public.account_events
  for select to authenticated
  using (public.is_workspace_member(workspace_id));

create policy account_events_insert_self on public.account_events
  for insert to authenticated
  with check (
    public.is_workspace_member(workspace_id)
    and user_id = (select auth.uid())
  );
