# GFG endpoints: what is verified and what is not

Research date: 2026-10-16. GeeksforGeeks has no official API, and the sandbox
that built this repo cannot reach geeksforgeeks.org, so nothing here was
tested against the live site.

`docs/gfg-network-sample.md` is now a blank template for you to fill in from
your own captured request; it is the place to record what you observe.

## Bottom line

**I could not verify any endpoint that lists a logged-in user's past
submissions or returns the source code of a past submission.** Because of
that, `scripts/gfg_sync.py` has **no hardcoded GFG URLs**. The URLs and the
JSON field names come from `.gfg-sync/endpoints.json`, which you fill in from
one captured request (see "How to fill endpoints.json" below). Until that file
exists the script exits with a clear configuration error. It does not guess.

## What the research actually found

| Finding | Evidence | Status |
|---|---|---|
| Browser extensions (CodeHub Sync, GfG-To-GitHub, Code2Git, PushMyCode, CP Sync) sync GFG by running in the browser while you submit | Their READMEs and source | Verified |
| CodeHub Sync intercepts XHR/fetch calls to `practiceapi` endpoints and treats a result as accepted when `view_mode === "correct"` or `sub_status === 1` | CodeHub Sync README ("How It Works > GeeksforGeeks") | Verified (README only; the exact URL path is not stated and its source file was not readable) |
| Code2Git does **not** call an API. It waits for the on-page text "Problem Solved Successfully" or "Correct Answer", then reads code from the Monaco/Ace/CodeMirror editor object | `content_gfg.js`, `content_gfg_main.js` in Code2Git | Verified |
| None of these tools call a "list my past submissions" or "get code of submission N" endpoint | Read all readable source | Verified (absence in the files I could read) |
| Public profile pages show a solved-problems list with difficulty, no code. Unofficial scrapers parse that HTML | arnoob16/GeeksForGeeksAPI, GFG profile page snippets | Verified (HTML only, no source code, old and possibly outdated) |
| Problem page URL pattern `https://www.geeksforgeeks.org/problems/<slug>/1` | CodeHub Sync `background.js` (README fallback link) | Verified, used as default for README links |
| GFG difficulty levels are School, Basic, Easy, Medium, Hard | Several user repos' tables | Verified |
| Cookie / token names needed for authenticated requests | Not found anywhere | **Unverified** |
| Pagination style of any submissions endpoint | Not found | **Unverified** |
| How far back history goes | Not found | **Unverified** |
| Whether full historical backfill is possible | Not found | **Unverified** |

Sources: the CodeHub-Sync, Code2Git, GfG-To-GitHub and GeeksForGeeksAPI
repositories on GitHub, and GFG profile/explore pages surfaced by search.

## Full historical backfill

**Not confirmed.** Whether it works depends entirely on the endpoint you
capture. If the endpoint you capture returns your whole history page by page,
the first run (or `full_sync`) will backfill all of it. If GFG only exposes
recent submissions per problem, only that is what you will get. The script
syncs exactly what the configured endpoint returns and nothing more.

## What the script expects (all `TODO(verify)`)

The script is generic. It needs three kinds of request, each described in
`endpoints.json`:

1. **List request** (paged): returns a JSON array of your submissions. For each
   item it needs: submission id, problem slug, language, a timestamp, and a
   field that says whether it was accepted. Optional: problem title,
   difficulty, problem id, topics.
2. **Code request**: given a submission id, returns the source code (either in
   a JSON field or as the raw response body).
3. **Problem request** (optional): given a problem slug, returns title,
   difficulty, topics and the HTML description. If it returns 403/404 the
   problem is treated as locked/premium and a placeholder README is written.

Assumptions baked into the logic, none verified against GFG:

- `TODO(verify)` The list is ordered newest first (config `order` can be set to
  `oldest_first`). Incremental runs rely on this.
- `TODO(verify)` Submission ids are unique and stable. Numeric ids are compared
  as integers, other ids by equality.
- `TODO(verify)` Pages are numbered and an empty page means the end.
- `TODO(verify)` Code comes back exactly as submitted. If GFG HTML-escapes it,
  set `code_html_unescape` to `true`.
- `TODO(verify)` Which cookies GFG needs. The script sends one secret,
  `GFG_COOKIE_HEADER`, containing the full `Cookie` header value, so you do not
  have to know individual cookie names. An optional second secret,
  `GFG_AUTH_HEADER_VALUE`, is sent under the header name set in
  `auth_header_name`, in case GFG also needs a token header.

## Secrets (read from environment, never printed)

| Env var | Meaning |
|---|---|
| `GFG_COOKIE_HEADER` | Full `Cookie:` header value copied from a logged-in request |
| `GFG_AUTH_HEADER_VALUE` | Optional. Value of a token header, if the captured request has one |

## How to fill endpoints.json

1. Log in at geeksforgeeks.org and open your submissions or profile page with
   DevTools > Network (filter Fetch/XHR) open.
2. Find the request whose response is your submission list. Copy only the URL
   and the JSON shape into `docs/gfg-network-sample.md`. Do not paste cookies or
   tokens.
3. Do the same for a request that returns the code of one submission, and for
   the request that returns a problem's description.
4. Copy `.gfg-sync/endpoints.example.json` to `.gfg-sync/endpoints.json` and
   edit the URLs and the dotted JSON paths to match. Use `{page}`, `{id}` and
   `{slug}` as placeholders in URLs.

Example (the URLs in it are fake placeholders, not GFG endpoints):

```json
{
  "list_url": "https://example.invalid/submissions?page={page}",
  "page_start": 1,
  "order": "newest_first",
  "items_path": "results.submissions",
  "fields": {
    "id": "id",
    "problem_slug": "slug",
    "problem_title": "title",
    "difficulty": "difficulty",
    "language": "language",
    "timestamp": "submitted_at",
    "problem_id": "problem_id",
    "topics": "tags"
  },
  "accepted_field": "status",
  "accepted_values": ["correct"],
  "code_url": "https://example.invalid/submission/{id}",
  "code_format": "json",
  "code_path": "data.code",
  "problem_url": "https://example.invalid/problem/{slug}",
  "problem_fields": {
    "title": "title",
    "difficulty": "difficulty",
    "description_html": "description",
    "topics": "tags"
  }
}
```

All JSON paths are dotted (`a.b.0.c`; numbers index into lists).

## Behaviour notes

- Paging stops when a page is empty, shorter than `page_size`, or contains only ids already seen (endpoint ignoring `{page}`).
- Cookie and auth headers are dropped if a redirect leaves the original host.
