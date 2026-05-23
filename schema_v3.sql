-- 把 region 改名成 district 更精確，city 照舊
alter table videos drop column if exists region;
alter table videos add column if not exists city text;
alter table videos add column if not exists district text;

create index if not exists videos_city_idx on videos (city);
create index if not exists videos_district_idx on videos (district);
