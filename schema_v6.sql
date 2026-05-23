-- 加入影片解析度欄位 + 直式/橫式 generated column
-- width/height 以 rotation metadata 校正後的實際顯示尺寸為準（ffprobe / Drive API 都會套 rotation）

alter table videos add column if not exists width int;
alter table videos add column if not exists height int;

-- 直式=true, 橫式=false, 方形/未知=null
alter table videos add column if not exists is_vertical boolean
  generated always as (
    case
      when width is null or height is null then null
      when height > width then true
      when width > height then false
      else null
    end
  ) stored;

create index if not exists videos_is_vertical_idx on videos (is_vertical) where is_vertical is not null;
