-- 舊版標記：subpath 含「舊」資料夾的成品，預設不顯示
alter table videos add column if not exists is_old boolean not null default false;

create index if not exists videos_is_old_idx on videos (is_old);
