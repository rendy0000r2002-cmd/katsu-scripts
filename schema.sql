-- 原初映像片庫 schema
-- 貼到 Supabase Dashboard → SQL Editor → Run 一次即可

create extension if not exists pg_trgm;

-- 影片索引表
create table if not exists videos (
  drive_file_id text primary key,
  rel_path text not null,
  filename text not null,
  ext text not null,
  size_bytes bigint not null default 0,
  mtime timestamptz,
  drive_web_link text,
  source text not null default 'drive',          -- drive | nas
  nas_share_url text,                             -- Synology 分享連結（NAS 檔用）

  channel_folder text,
  channel_name text,
  channel_order int,

  case_folder text,
  case_name text,
  case_date date,
  category_folder text,
  category text,
  subpath text,
  tags text[],

  search_text text,
  indexed_at timestamptz not null default now()
);

create index if not exists videos_channel_name_idx on videos (channel_name);
create index if not exists videos_category_idx on videos (category);
create index if not exists videos_case_name_idx on videos (case_name);
create index if not exists videos_mtime_idx on videos (mtime desc nulls last);
create index if not exists videos_search_trgm_idx on videos using gin (search_text gin_trgm_ops);

-- 登入 / 行為紀錄
create table if not exists login_logs (
  id bigserial primary key,
  email text not null,
  name text,
  ip inet,
  user_agent text,
  event text not null,                            -- login | search | download | preview
  detail jsonb,
  created_at timestamptz not null default now()
);

create index if not exists login_logs_email_idx on login_logs (email, created_at desc);
create index if not exists login_logs_event_idx on login_logs (event, created_at desc);

-- RLS：service_role 自動繞過；後端用 service_role key，前端查 videos 以 anon key + policy
alter table videos enable row level security;
alter table login_logs enable row level security;

-- videos 允許任何登入用戶讀（前端會拿 anon key 查；之後若要更嚴格再加 email allowlist）
drop policy if exists "videos read" on videos;
create policy "videos read" on videos for select using (true);

-- login_logs 只讓後端（service_role）寫，anon 不能碰
-- 不加 select policy 等同沒人能讀（service_role 照樣繞過）
