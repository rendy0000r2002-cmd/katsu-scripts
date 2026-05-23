-- v9: shoot_schedules table for /shoot-schedule app
create extension if not exists pgcrypto;
create table if not exists shoot_schedules (
  id uuid primary key default gen_random_uuid(),
  data jsonb not null,
  owner_email text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists shoot_schedules_owner_idx on shoot_schedules (owner_email);
create index if not exists shoot_schedules_updated_idx on shoot_schedules (updated_at desc);
grant select, insert, update, delete on shoot_schedules to service_role;
grant select on shoot_schedules to anon, authenticated;

-- v9: fuzzy_search_cases RPC for search/suggest pg_trgm fallback
create or replace function fuzzy_search_cases(q text, threshold float default 0.03)
returns table(case_name text, sim float)
language sql stable as $$
  select distinct on (v.case_name)
    v.case_name,
    similarity(v.case_name, q) as sim
  from videos v
  where v.case_name is not null
    and similarity(v.case_name, q) >= threshold
  order by v.case_name, sim desc
  limit 50;
$$;
grant execute on function fuzzy_search_cases(text, float) to anon, authenticated, service_role;
