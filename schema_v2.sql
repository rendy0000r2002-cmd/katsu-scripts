-- 為 videos 表加地點欄位（讓「台中」「新北」等搜尋命中）
alter table videos add column if not exists city text;
alter table videos add column if not exists region text;

-- 讓 search_text 可以被 city/region 命中（更新時由 tag_regions.py 負責重建）
create index if not exists videos_city_idx on videos (city);
