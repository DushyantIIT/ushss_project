-- USHSS interconnected chat schema
-- Dedicated tables for interconnected chat; legacy public.chat_messages remains untouched.
create table if not exists public.chat_conversations (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('direct','class')),
  title text,
  programme text,
  batch text,
  semester text,
  section text,
  created_by text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.chat_members (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.chat_conversations(id) on delete cascade,
  user_id text not null,
  joined_at timestamptz not null default now(),
  last_read_at timestamptz,
  unique (conversation_id, user_id)
);
create index if not exists chat_members_user_id_idx on public.chat_members(user_id);
create index if not exists chat_members_conversation_idx on public.chat_members(conversation_id);

create table if not exists public.interconnected_chat_messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.chat_conversations(id) on delete cascade,
  sender_id text not null,
  body text not null check (char_length(body) between 1 and 4000),
  created_at timestamptz not null default now()
);
create index if not exists interconnected_chat_messages_conversation_created_idx on public.interconnected_chat_messages(conversation_id, created_at desc);

alter table public.chat_conversations enable row level security;
alter table public.chat_members enable row level security;
alter table public.interconnected_chat_messages enable row level security;
-- No client-role policies: all access goes through the FastAPI server using its service role.
