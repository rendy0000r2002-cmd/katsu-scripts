-- v7: codec + proxy_path columns (added 2026-05-15 ad-hoc, now archived)
alter table videos add column if not exists codec text;
alter table videos add column if not exists proxy_path text;

