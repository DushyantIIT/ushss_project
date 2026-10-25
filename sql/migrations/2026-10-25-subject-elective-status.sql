-- Per-subject elective classification.
-- Run in Supabase SQL Editor before deploying this feature.
alter table public.subjects_offered add column if not exists is_elective boolean not null default false;
update public.subjects_offered set is_elective = true where lower(trim(coalesce(paper_category, ''))) = 'elective';
