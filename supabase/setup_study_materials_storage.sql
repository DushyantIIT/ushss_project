-- Run once in Supabase Dashboard → SQL Editor.
-- Creates the private bucket used by Faculty and CR study-material uploads.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'ushss-study-materials',
  'ushss-study-materials',
  false,
  10485760,
  array[
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'text/plain',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'image/png',
    'image/jpeg',
    'application/zip',
    'application/x-zip-compressed',
    'application/octet-stream'
  ]
)
on conflict (id) do update
set name = excluded.name,
    public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

-- Keep the bucket private; the backend serves files with signed URLs.
-- The backend's SUPABASE_SERVICE_ROLE_KEY should be configured in Render.
