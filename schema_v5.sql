-- 本周拍攝行程表，由 sync_schedule.py 從 Google Sheet 抓取後 upsert
create table if not exists weekly_schedule (
  date date not null,
  case_name text not null,
  editor text,
  city text,
  district text,
  primary key (date, case_name)
);

create index if not exists weekly_schedule_date_idx on weekly_schedule (date);
