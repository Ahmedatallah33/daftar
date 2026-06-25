-- Daftar Sprint 3A-2A — Account Foundation (security hardening)
-- Development project only.
--
-- Clears the database security-advisor warnings:
--   * handle_new_user() is trigger-only and must not be a callable RPC.
--   * The membership helpers move into a non-exposed `private` schema so they
--     are not reachable via /rest/v1/rpc while still powering RLS policies.

-- handle_new_user fires from the auth.users trigger regardless of EXECUTE
-- privilege; remove the public RPC surface entirely.
revoke execute on function public.handle_new_user() from anon, authenticated, public;

-- Private schema for RLS helpers — not added to the exposed API schemas.
create schema if not exists private;
revoke all on schema private from anon, authenticated, public;
grant usage on schema private to authenticated;

create or replace function private.is_workspace_member(p_workspace_id uuid)
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

create or replace function private.is_workspace_owner(p_workspace_id uuid)
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

revoke execute on function private.is_workspace_member(uuid) from anon, public;
revoke execute on function private.is_workspace_owner(uuid) from anon, public;
grant execute on function private.is_workspace_member(uuid) to authenticated;
grant execute on function private.is_workspace_owner(uuid) to authenticated;

-- Repoint every workspace-scoped policy at the private helpers.
drop policy workspaces_select_member on public.workspaces;
drop policy workspaces_update_owner on public.workspaces;
drop policy workspaces_delete_owner on public.workspaces;

drop policy workspace_members_select_member on public.workspace_members;
drop policy workspace_members_insert_owner on public.workspace_members;
drop policy workspace_members_update_owner on public.workspace_members;
drop policy workspace_members_delete_owner on public.workspace_members;

drop policy devices_select_member on public.devices;
drop policy devices_insert_self on public.devices;
drop policy devices_update_self on public.devices;
drop policy devices_delete_self on public.devices;

drop policy consents_select_member on public.consents;
drop policy consents_insert_self on public.consents;
drop policy consents_update_self on public.consents;
drop policy consents_delete_self on public.consents;

drop policy account_events_select_member on public.account_events;
drop policy account_events_insert_self on public.account_events;

drop function public.is_workspace_member(uuid);
drop function public.is_workspace_owner(uuid);

create policy workspaces_select_member on public.workspaces
  for select to authenticated using (private.is_workspace_member(id));
create policy workspaces_update_owner on public.workspaces
  for update to authenticated using (private.is_workspace_owner(id)) with check (private.is_workspace_owner(id));
create policy workspaces_delete_owner on public.workspaces
  for delete to authenticated using (private.is_workspace_owner(id));

create policy workspace_members_select_member on public.workspace_members
  for select to authenticated using (private.is_workspace_member(workspace_id));
create policy workspace_members_insert_owner on public.workspace_members
  for insert to authenticated with check (private.is_workspace_owner(workspace_id));
create policy workspace_members_update_owner on public.workspace_members
  for update to authenticated using (private.is_workspace_owner(workspace_id)) with check (private.is_workspace_owner(workspace_id));
create policy workspace_members_delete_owner on public.workspace_members
  for delete to authenticated using (private.is_workspace_owner(workspace_id));

create policy devices_select_member on public.devices
  for select to authenticated using (private.is_workspace_member(workspace_id));
create policy devices_insert_self on public.devices
  for insert to authenticated with check (private.is_workspace_member(workspace_id) and user_id = (select auth.uid()));
create policy devices_update_self on public.devices
  for update to authenticated using (private.is_workspace_member(workspace_id) and user_id = (select auth.uid())) with check (private.is_workspace_member(workspace_id) and user_id = (select auth.uid()));
create policy devices_delete_self on public.devices
  for delete to authenticated using (private.is_workspace_member(workspace_id) and user_id = (select auth.uid()));

create policy consents_select_member on public.consents
  for select to authenticated using (private.is_workspace_member(workspace_id));
create policy consents_insert_self on public.consents
  for insert to authenticated with check (private.is_workspace_member(workspace_id) and user_id = (select auth.uid()));
create policy consents_update_self on public.consents
  for update to authenticated using (private.is_workspace_member(workspace_id) and user_id = (select auth.uid())) with check (private.is_workspace_member(workspace_id) and user_id = (select auth.uid()));
create policy consents_delete_self on public.consents
  for delete to authenticated using (private.is_workspace_member(workspace_id) and user_id = (select auth.uid()));

create policy account_events_select_member on public.account_events
  for select to authenticated using (private.is_workspace_member(workspace_id));
create policy account_events_insert_self on public.account_events
  for insert to authenticated with check (private.is_workspace_member(workspace_id) and user_id = (select auth.uid()));
