-- v8: case_locations table for admin/locations page
create table if not exists case_locations (
  case_name text primary key,
  city text,
  district text,
  is_non_building boolean not null default false,
  reason text,
  source text,
  updated_by text,
  updated_at timestamptz not null default now()
);
create index if not exists case_locations_city_idx on case_locations (city);
create index if not exists case_locations_is_non_building_idx on case_locations (is_non_building);

grant select, insert, update, delete on case_locations to service_role;
grant select on case_locations to anon, authenticated;

