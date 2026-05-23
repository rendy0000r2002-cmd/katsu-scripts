# katsu-scripts

NAS-side scripts powering the 原初映像片庫 (video library) system.
Production location: `/volume2/docker-prod/scripts/原初映像片庫/` on Synology NAS.

Initial commit: 2026-05-23 (baseline after a day of bug-fix sprints).

## Components

- `vision_pass_v2.py` — Gemini Vision tagging of NAS video frames
- `rename_after_vision.py` — apply tag results as `<stem>_<place><ext>` rename + DB PATCH
- `vision_v2_daily.sh` — daily 02:00 cron wrapper (runs in `katsu-scripts-v2` container)
- `logs/finisher_script.py` — post-pass aggregation + Telegram push
- `daily_health.py` — daily 09:00 health probe (PostgREST / DB / thumb / Gemini / disk / cron)
- `scan.py`, `scan_nas.py` — file walkers populating videos table
- `refine.py`, `upload.py` — index transform + DB upsert
- `backup_video_library.py` — daily 03:00 backup (Postgres + assets to zip)
- `restore_drill.py` — weekly Sunday 04:30 restore verification
- `cleanup_missing_files.py` — daily 04:00 orphan detection → web confirm flow
- `has_host/` — YOLO-based 有/無 主持人 classification (NAS container)
- `detect_has_host*.py` — PC fallback / drive variant
- `case_locations.json` — case_name → city/district cache (Gemini text lookup)

## Schema

PostgREST + Postgres 16 at `127.0.0.1:3011/rest/v1` (nginx gateway in front).
Tables: `videos`, `case_locations`, `login_logs`, `weekly_schedule`,
`shoot_schedules`, `vision_journal_applied` (idempotency).

## NEVER commit
- `.env` (SUPABASE_SERVICE_ROLE_KEY, GEMINI_API_KEY, LINE token, TG bot token)
- `*service_account.json`, `*credentials*.json`
- `logs/`, `backups/` (operational data, GBs)
- `*.pt` model weights
