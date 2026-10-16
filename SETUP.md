# Setup

`.gfg-sync/endpoints.json` is already filled in from real captured GFG requests
(handle `rishp_3`). You only need to add your login cookie as a secret.

## 1. Add the cookie secret (GitHub > Settings > Secrets and variables > Actions)
1. In Chrome, log in at geeksforgeeks.org, open DevTools > Network > Fetch/XHR,
   reload a problem page, click any `practiceapi.geeksforgeeks.org` request and
   copy the **Cookie** request header value.
2. Create a repository secret named `GFG_COOKIE_HEADER` and paste it there.
   **Never paste cookies in chat, issues or commits.**

Optional variables: `GFG_LANGUAGES` (default `java`), `REQUEST_DELAY` (default
`1.5`), `CREATE_MAIN_STUB`, `ARCHIVE_ALL_ACCEPTED` (both default `true`).

## 2. Run
Actions > "GFG Sync" > Run workflow (tick `full_sync` the first time). After
that it runs daily.

Locally: `GFG_COOKIE_HEADER='...' python scripts/gfg_sync.py`

## How it works
1. POST `.../api/v1/user/problems/submissions/` lists all your solved problems.
2. For each problem, GET `.../problems/<slug>/submissions/user/` lists its
   submissions (with ids).
3. For each accepted submission not seen before, GET
   `.../problems/submissions/<id>/` returns `user_code`.
4. Files are written to `<Difficulty>/<slug>/Solution.<ext>`.

About 150 problems means about 150 list requests on every run, plus one request
per new submission. At the default 1.5 s delay that is a few minutes.

## Notes
- Cookies expire. On HTTP 401/403 the job fails; paste a fresh cookie. GFG may
  also block GitHub runner IPs; if so, run the script locally.
- Adding a language to `GFG_LANGUAGES` picks up older submissions on the next
  run (every run re-lists everything).
- Problem descriptions are not fetched (no verified endpoint). README files get
  a link to the problem plus a placeholder text.
- Submission times are read as IST (`timestamp_utc_offset_minutes: 330`).
- The sync never edits `Main.java`. It only deletes a stale `Solution.<ext>`
  when the newest accepted language for a problem changes.
- Failed runs still commit work finished before the failure, then the job fails.
