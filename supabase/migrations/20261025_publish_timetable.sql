-- Timetable publishing: schedules remain drafts until an admin publishes
-- the complete programme/course/batch/semester/section group.
ALTER TABLE public.timetable_slots
  ADD COLUMN IF NOT EXISTS published boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_timetable_slots_published_group
  ON public.timetable_slots (published, programme, batch, semester, section);
