-- Safe repair for production Supabase instances missing attendance review workflow column.
ALTER TABLE public.attendance_records
  ADD COLUMN IF NOT EXISTS review_status text NOT NULL DEFAULT 'approved';

CREATE INDEX IF NOT EXISTS idx_records_review
  ON public.attendance_records(session_id, review_status);
