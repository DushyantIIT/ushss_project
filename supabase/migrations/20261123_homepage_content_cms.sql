-- Admin-managed public homepage content with private drafts and explicit publishing.
create table if not exists public.homepage_content (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique check (slug = 'home'),
  draft_content jsonb not null default '{}'::jsonb,
  published_content jsonb not null default '{}'::jsonb,
  updated_by bigint,
  published_by bigint,
  updated_at timestamptz not null default now(),
  published_at timestamptz
);
alter table public.homepage_content enable row level security;
revoke all on table public.homepage_content from anon, authenticated;
grant all on table public.homepage_content to service_role;
