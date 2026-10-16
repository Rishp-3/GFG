# Setup

GFG has no official API, so you must give the script one captured request
shape. Do this once.

## 1. Capture the endpoints
1. Log in at geeksforgeeks.org. Open DevTools > Network > Fetch/XHR.
2. Open your submissions/profile page and find the request that returns your
   submission list. Then open one submission and find the request that returns
   its code. Optionally open a problem page and find the request that returns
   its description.
3. Write only URLs and JSON shapes in `docs/gfg-network-sample.md` (template
   included). **Never paste cookies or tokens there.**

## 2. Create the config
```
cp .gfg-sync/endpoints.example.json .gfg-sync/endpoints.json
```
Edit URLs and dotted JSON paths. Placeholders: `{page}`, `{id}`, `{slug}`.
Set `page_size` if the list endpoint has a fixed page size (stops paging early).
Remove `problem_url` if there is no problem-description endpoint.

## 3. Add secrets and variables (GitHub > Settings > Secrets and variables > Actions)
| Name | Kind | Meaning |
| --- | --- | --- |
| `GFG_COOKIE_HEADER` | secret, required | Full `Cookie:` header value of a logged-in request |
| `GFG_AUTH_HEADER_VALUE` | secret, optional | Token header value (name goes in `auth_header_name`) |
| `GFG_LANGUAGES` | variable | Comma list, default `java` |
| `REQUEST_DELAY` | variable | Seconds between requests, default `1.5` |
| `CREATE_MAIN_STUB` | variable | `true`/`false`, default `true` |
| `ARCHIVE_ALL_ACCEPTED` | variable | `true`/`false`, default `true` |

## 4. Run
Actions > "GFG Sync" > Run workflow. Tick `full_sync` for the first run to
backfill everything the endpoint returns. After that it runs daily.

Locally: `GFG_COOKIE_HEADER='...' python scripts/gfg_sync.py`

## Notes
- Cookies expire. When the job fails with HTTP 401/403, paste fresh cookies.
  GFG may also block GitHub runner IPs; if so, run the script locally.
- If you add a language to `GFG_LANGUAGES`, older submissions in that language
  are only picked up by a `full_sync` run.
- If a problem README says "description not available", it is retried
  automatically on the next run that syncs that problem. READMEs you edit are
  never overwritten.
- The sync never edits `Main.java`. It only deletes a stale `Solution.<ext>`
  when the newest accepted language for a problem changes.
- Failed runs still commit work finished before the failure, then the job is
  marked failed.
