-- ============================================================
--  Migration: Registration, Email Verification & Approval Workflow
--  Run in: Supabase Dashboard → SQL Editor
--  Safe to run multiple times (every statement is idempotent).
--
--  Does NOT touch/recreate any existing table — only adds the
--  columns, constraint, and indexes this feature needs.
-- ============================================================

-- Existing rows (admin-created / pre-migration accounts) default to
-- 'approved' so today's login behaviour is unaffected.
alter table users add column if not exists status text not null default 'approved';

do $$ begin
  alter table users add constraint chk_users_status
    check (status in ('pending', 'approved', 'rejected'));
exception when duplicate_object then null; end $$;

-- Links a profile row to its Supabase Auth identity (auth.users.id).
-- NULL for legacy/admin-created accounts, which keep using password_hash.
alter table users add column if not exists supabase_uid uuid unique;

-- Approval audit trail.
alter table users add column if not exists approved_by bigint
  references users(id) on delete set null;
alter table users add column if not exists approved_at timestamptz;
alter table users add column if not exists rejection_reason text;

-- Supabase-Auth-backed accounts don't get a local password hash.
alter table users alter column password_hash drop not null;

create index if not exists idx_users_status       on users(status);
create index if not exists idx_users_supabase_uid on users(supabase_uid);

-- Safety net: any unexpected NULL status becomes 'approved' (shouldn't
-- occur given the column default above, but costs nothing to be sure).
update users set status = 'approved' where status is null;
