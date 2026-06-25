-- Daftar Sprint 3A-2A — Account Foundation (auto-provisioning)
-- Development project only.
--
-- On every new auth user (including Email OTP signups), automatically:
--   1. create the profile,
--   2. create one initial workspace owned by that user,
--   3. record the creator as the workspace 'owner' member,
--   4. append an account_provisioned event.
-- Runs as SECURITY DEFINER so it bypasses RLS during signup.

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_workspace_id uuid;
begin
  insert into public.profiles (id, display_name)
  values (
    new.id,
    coalesce(
      nullif(new.raw_user_meta_data ->> 'display_name', ''),
      split_part(coalesce(new.email, ''), '@', 1)
    )
  );

  insert into public.workspaces (owner_id, name)
  values (new.id, 'My Workspace')
  returning id into v_workspace_id;

  insert into public.workspace_members (workspace_id, user_id, role)
  values (v_workspace_id, new.id, 'owner');

  insert into public.account_events (workspace_id, user_id, event_type, metadata)
  values (
    v_workspace_id,
    new.id,
    'account_provisioned',
    jsonb_build_object('source', 'auth_signup')
  );

  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
