-- USHSS shared rate limiting.
-- The backend calls this function using the server-only Supabase service-role client.
-- No browser role can read or mutate rate-limit counters.

create table if not exists public.ushss_rate_limit_buckets (
    key_hash text primary key check (length(key_hash) = 64),
    bucket_started_at timestamptz not null,
    hits integer not null default 0 check (hits >= 0)
);

alter table public.ushss_rate_limit_buckets enable row level security;
create index if not exists ushss_rate_limit_buckets_started_idx
    on public.ushss_rate_limit_buckets (bucket_started_at);
revoke all on table public.ushss_rate_limit_buckets from public, anon, authenticated;
grant all on table public.ushss_rate_limit_buckets to service_role;

create or replace function public.ushss_rate_limit(
    p_key_hash text,
    p_max_calls integer,
    p_window_seconds integer,
    p_increment boolean default true,
    p_reset boolean default false
)
returns table (
    allowed boolean,
    hit_count integer,
    retry_after_seconds integer
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $function$
declare
    v_now timestamptz := clock_timestamp();
    v_started timestamptz;
    v_hits integer;
begin
    if p_key_hash is null or p_key_hash !~ '^[0-9a-f]{64}$' then
        raise exception 'Invalid rate-limit key';
    end if;
    if p_max_calls is null or p_max_calls < 1
       or p_window_seconds is null or p_window_seconds < 1
       or p_window_seconds > 86400 then
        raise exception 'Invalid rate-limit bounds';
    end if;

    if p_reset then
        delete from public.ushss_rate_limit_buckets
        where key_hash = p_key_hash;
        return query select true, 0, 0;
        return;
    end if;

    insert into public.ushss_rate_limit_buckets as buckets
        (key_hash, bucket_started_at, hits)
    values
        (p_key_hash, v_now, case when p_increment then 1 else 0 end)
    on conflict (key_hash) do update
    set
        bucket_started_at = case
            when buckets.bucket_started_at <= v_now - make_interval(secs => p_window_seconds)
            then v_now else buckets.bucket_started_at end,
        hits = case
            when buckets.bucket_started_at <= v_now - make_interval(secs => p_window_seconds)
            then case when p_increment then 1 else 0 end
            else buckets.hits + case when p_increment then 1 else 0 end
        end
    returning bucket_started_at, hits into v_started, v_hits;

    if random() < 0.005 then
        delete from public.ushss_rate_limit_buckets
        where bucket_started_at < v_now - interval '1 day';
    end if;

    return query
    select
        (v_hits <= p_max_calls),
        v_hits,
        greatest(
            1,
            ceil(extract(epoch from (
                v_started + make_interval(secs => p_window_seconds) - v_now
            )))::integer
        );
end;
$function$;

revoke all on function public.ushss_rate_limit(text, integer, integer, boolean, boolean)
    from public, anon, authenticated;
grant execute on function public.ushss_rate_limit(text, integer, integer, boolean, boolean)
    to service_role;
