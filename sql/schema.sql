-- ============================================================
--  USHSS PORTAL — Supabase SQL Schema
--  Run this entire file in: Supabase Dashboard → SQL Editor
-- ============================================================

-- ── Extensions ───────────────────────────────────────────────
create extension if not exists "pgcrypto";

-- ── Enums ────────────────────────────────────────────────────
do $$ begin
  create type role_enum as enum ('student', 'faculty', 'cr', 'admin');
exception when duplicate_object then null; end $$;

do $$ begin
  create type attendance_status as enum ('present', 'absent');
exception when duplicate_object then null; end $$;

-- ============================================================
--  USERS
-- ============================================================
create table if not exists users (
  id            bigserial primary key,
  username      text        not null,
  role          role_enum   not null,
  full_name     text        not null,
  email         text        not null unique,
  password_hash text        not null,
  is_active     boolean     not null default true,
  phone         text,
  enrollment_no text,
  department    text,
  programme     text,
  batch         text,
  designation   text,
  last_login    timestamptz,
  created_at    timestamptz not null default now(),
  unique (username, role)
);

-- ============================================================
--  AUDIT LOG
-- ============================================================
create table if not exists audit_log (
  id      bigserial primary key,
  user_id bigint references users(id) on delete set null,
  action  text        not null,
  detail  text,
  ip      text,
  ts      timestamptz not null default now()
);

-- ============================================================
--  CONTACT MESSAGES
-- ============================================================
create table if not exists contact_messages (
  id           bigserial primary key,
  first_name   text        not null,
  last_name    text        not null,
  email        text        not null,
  subject      text        not null,
  message      text        not null,
  ip_address   text,
  is_read      boolean     not null default false,
  submitted_at timestamptz not null default now()
);

-- ============================================================
--  FACULTY DIRECTORY  (public listing — separate from users)
-- ============================================================
create table if not exists faculty_directory (
  id             bigserial primary key,
  name           text    not null,
  designation    text    not null,
  department     text,
  specialisation text,
  email          text,
  phone          text,
  photo_url      text,
  initials       text,
  bio            text,
  sort_order     int     not null default 100,
  is_active      boolean not null default true
);

-- ============================================================
--  EVENTS
-- ============================================================
create table if not exists events (
  id          bigserial primary key,
  name        text    not null,
  description text,
  event_date  date    not null,
  event_time  text,
  venue       text,
  category    text,
  is_featured boolean not null default false,
  created_at  timestamptz not null default now()
);

-- ============================================================
--  NEWS ITEMS
-- ============================================================
create table if not exists news_items (
  id             bigserial primary key,
  title          text    not null,
  excerpt        text,
  body           text,
  tag            text,
  image_url      text,
  published      boolean not null default true,
  is_featured    boolean not null default false,
  published_date date    not null default current_date,
  venue          text,
  created_at     timestamptz not null default now()
);

-- ============================================================
--  TIMETABLE SLOTS  (admin creates the schedule)
-- ============================================================
create table if not exists timetable_slots (
  id          bigserial primary key,
  subject     text not null,
  day_of_week text not null,   -- 'Monday' … 'Saturday'
  start_time  text not null,   -- 'HH:MM'
  end_time    text not null,
  room        text,
  programme   text not null,
  batch       text not null,
  department  text,
  faculty_id  bigint references users(id) on delete set null,
  created_at  timestamptz not null default now()
);

-- ============================================================
--  ATTENDANCE SESSIONS  (faculty opens per class per date)
-- ============================================================
create table if not exists attendance_sessions (
  id         bigserial primary key,
  slot_id    bigint references timetable_slots(id) on delete cascade,
  faculty_id bigint references users(id) on delete set null,
  date       date    not null,
  is_open    boolean not null default true,
  opened_at  timestamptz not null default now(),
  closed_at  timestamptz,
  unique (slot_id, date)
);

-- ============================================================
--  ATTENDANCE RECORDS  (student marks present in open session)
-- ============================================================
create table if not exists attendance_records (
  id         bigserial primary key,
  session_id bigint references attendance_sessions(id) on delete cascade,
  student_id bigint references users(id) on delete cascade,
  status     attendance_status not null default 'present',
  marked_at  timestamptz not null default now(),
  unique (session_id, student_id)
);

-- ============================================================
--  ROW-LEVEL SECURITY (RLS)
--  We use FastAPI JWT auth so we disable RLS and enforce
--  permissions in the API layer (require_admin / require_faculty
--  / require_student guards). If you later add Supabase Auth,
--  enable RLS per table.
-- ============================================================
alter table users               disable row level security;
alter table audit_log           disable row level security;
alter table contact_messages    disable row level security;
alter table faculty_directory   disable row level security;
alter table events              disable row level security;
alter table news_items          disable row level security;
alter table timetable_slots     disable row level security;
alter table attendance_sessions disable row level security;
alter table attendance_records  disable row level security;

-- ============================================================
--  INDEXES  (speeds up common queries)
-- ============================================================
create index if not exists idx_users_role        on users(role);
create index if not exists idx_users_email       on users(email);
create index if not exists idx_users_programme   on users(programme, batch);
create index if not exists idx_timetable_prog    on timetable_slots(programme, batch);
create index if not exists idx_sessions_slot     on attendance_sessions(slot_id, date);
create index if not exists idx_records_session   on attendance_records(session_id);
create index if not exists idx_records_student   on attendance_records(student_id);
create index if not exists idx_audit_ts          on audit_log(ts desc);
