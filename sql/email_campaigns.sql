-- USHSS automated email campaigns (Supabase PostgreSQL)
-- Run in Supabase Dashboard -> SQL Editor before deploying the application.
create table if not exists public.email_templates (
  id bigserial primary key,
  name text not null,
  subject text not null,
  body_text text not null default '',
  body_html text not null default '',
  is_active boolean not null default true,
  created_by bigint references public.users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.email_campaigns (
  id bigserial primary key,
  name text not null,
  subject text not null,
  body_text text not null default '',
  body_html text not null default '',
  target_filters jsonb not null default '{}'::jsonb,
  scheduled_at timestamptz not null default now(),
  status text not null default 'scheduled'
    check (status in ('scheduled','sending','sent','failed','cancelled')),
  recipient_count integer not null default 0 check (recipient_count >= 0),
  created_by bigint references public.users(id) on delete set null,
  created_at timestamptz not null default now(),
  sent_at timestamptz
);

create table if not exists public.email_deliveries (
  id bigserial primary key,
  campaign_id bigint not null references public.email_campaigns(id) on delete cascade,
  user_id bigint references public.users(id) on delete set null,
  recipient_email text not null,
  recipient_name text not null default '',
  status text not null default 'pending'
    check (status in ('pending','sending','sent','failed')),
  attempts integer not null default 0 check (attempts >= 0),
  last_error text,
  last_attempt_at timestamptz,
  sent_at timestamptz,
  created_at timestamptz not null default now(),
  unique (campaign_id, user_id)
);

create index if not exists idx_email_campaigns_due
  on public.email_campaigns(status, scheduled_at);
create index if not exists idx_email_deliveries_campaign_status
  on public.email_deliveries(campaign_id, status);
create index if not exists idx_email_deliveries_pending
  on public.email_deliveries(status, created_at);

alter table public.email_templates enable row level security;
alter table public.email_campaigns enable row level security;
alter table public.email_deliveries enable row level security;

-- The application accesses these tables only through its server-side
-- Supabase service-role client. Do not expose campaign or recipient data
-- directly to anon/authenticated browser clients.
revoke all on public.email_templates from anon, authenticated;
revoke all on public.email_campaigns from anon, authenticated;
revoke all on public.email_deliveries from anon, authenticated;
grant all on public.email_templates to service_role;
grant all on public.email_campaigns to service_role;
grant all on public.email_deliveries to service_role;
grant usage, select on all sequences in schema public to service_role;
