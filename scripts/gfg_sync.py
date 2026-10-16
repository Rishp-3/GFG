#!/usr/bin/env python3
"""Sync accepted GeeksforGeeks submissions into this repository.

Python standard library only. GFG has no official API, so the request URLs and
JSON field names are read from ``.gfg-sync/endpoints.json`` (see
docs/GFG_ENDPOINTS.md). Nothing about GFG's private endpoints is hardcoded.

Environment (secrets are never printed or logged):
  GFG_COOKIE_HEADER       full Cookie header value from a logged-in session
  GFG_AUTH_HEADER_VALUE   optional token header value (header name in config)
  GFG_LANGUAGES           comma separated, default "java"
  FULL_SYNC               "true" ignores the incremental marker
  REQUEST_DELAY           seconds between requests, default 1.5
  CREATE_MAIN_STUB        default "true" (Java only, exclusive-create)
  ARCHIVE_ALL_ACCEPTED    default "true"
  GFG_ENDPOINTS_FILE      default ".gfg-sync/endpoints.json"
  GFG_ENDPOINTS_JSON      inline JSON, overrides the file
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

START = "<!-- GFG-SYNC:START -->"
END = "<!-- GFG-SYNC:END -->"
STATE_PATH = ".gfg-sync/state.json"
DIFFICULTY_ORDER = ["school", "basic", "easy", "medium", "hard"]
SKIP_TOP_DIRS = {"docs", "scripts", "tests", "node_modules"}
MAX_RETRIES = 5
SENSITIVE_HEADERS = {"cookie", "authorization"}
EXTENSIONS = {
    "java": "java", "cpp": "cpp", "c++": "cpp", "c": "c", "python": "py",
    "python3": "py", "javascript": "js", "typescript": "ts", "csharp": "cs",
    "c#": "cs", "go": "go", "golang": "go", "kotlin": "kt", "rust": "rs",
    "ruby": "rb", "swift": "swift", "php": "php", "scala": "scala",
}

MAIN_STUB = (
    "// Local test harness stub created by gfg-sync.\n"
    "// Safe to edit: gfg-sync never overwrites or deletes this file.\n"
    "public class Main {\n"
    "    public static void main(String[] args) {\n"
    "        // TODO: call your Solution here\n"
    "    }\n"
    "}\n"
)
PLACEHOLDER_DESC = (
    "_The problem description is not available (the problem may be premium or "
    "locked, or it could not be fetched). Your accepted solution is still saved "
    "in this folder._"
)


class SyncError(Exception):
    pass


class AuthError(SyncError):
    pass


class ConfigError(SyncError):
    pass


# --------------------------------------------------------------------------
# HTTP layer (tests patch http_request)
# --------------------------------------------------------------------------

class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Drop credentials when a redirect leaves the original host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            old_host = urllib.parse.urlsplit(req.full_url).netloc.lower()
            new_host = urllib.parse.urlsplit(newurl).netloc.lower()
            if old_host != new_host:
                for name in list(new.headers):
                    if name.lower() in SENSITIVE_HEADERS:
                        del new.headers[name]
                for name in list(new.unredirected_hdrs):
                    if name.lower() in SENSITIVE_HEADERS:
                        del new.unredirected_hdrs[name]
        return new


_OPENER = urllib.request.build_opener(_SafeRedirect)


def http_request(url, headers, timeout=60, data=None):
    """One HTTP request (GET, or POST when ``data`` bytes are given).

    Returns (status, body_bytes). Network errors raise.
    """
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:
            body = b""
        return exc.code, body


class Client:
    def __init__(self, cfg, env):
        try:
            self.delay = float(env.get("REQUEST_DELAY") or 1.5)
        except ValueError:
            self.delay = 1.5
        headers = {"User-Agent": "gfg-sync/1.0 (+personal backup script)"}
        headers.update({str(k): str(v) for k, v in (cfg.get("headers") or {}).items()})
        cookie = env.get("GFG_COOKIE_HEADER", "").strip()
        if cookie:
            headers["Cookie"] = cookie
        auth_name = (cfg.get("auth_header_name") or "").strip()
        auth_value = env.get("GFG_AUTH_HEADER_VALUE", "").strip()
        if auth_name and auth_value:
            headers[auth_name] = auth_value
            SENSITIVE_HEADERS.add(auth_name.lower())
        self._headers = headers
        self._first = True

    def get(self, url, soft=(), body=None):
        """GET (or JSON POST when ``body`` is a dict) with polite delay and
        retry/backoff. Returns (status, body).

        Statuses listed in ``soft`` are returned instead of raised.
        """
        for attempt in range(MAX_RETRIES + 1):
            if not self._first and self.delay > 0:
                time.sleep(self.delay)
            self._first = False
            try:
                if body is None:
                    status, resp = http_request(url, self._headers)
                else:
                    hdrs = dict(self._headers)
                    hdrs["Content-Type"] = "application/json"
                    status, resp = http_request(
                        url, hdrs, data=json.dumps(body).encode("utf-8"))
            except (urllib.error.URLError, TimeoutError, OSError):
                status, resp = 0, b""
            if status in soft:
                return status, resp
            if status == 200:
                return status, resp
            if status in (401, 403):
                raise AuthError(
                    "GFG returned HTTP %d. Your login cookies have probably "
                    "expired, or GitHub's runner IP is blocked by GFG. Copy "
                    "fresh cookies into the GFG_COOKIE_HEADER secret (see "
                    "SETUP.md)." % status
                )
            if status == 0 or status == 429 or status >= 500:
                if attempt == MAX_RETRIES:
                    raise SyncError(
                        "GFG request kept failing (HTTP %s) after %d retries."
                        % (status or "network error", MAX_RETRIES)
                    )
                time.sleep(min(60, 2 ** (attempt + 1)))
                continue
            raise SyncError("Unexpected HTTP %d from GFG." % status)
        raise SyncError("unreachable")


# --------------------------------------------------------------------------
# Config / helpers
# --------------------------------------------------------------------------

def dig(obj, path):
    if path in (None, ""):
        return obj
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        else:
            return None
    return cur


def load_endpoints(root, env):
    raw = env.get("GFG_ENDPOINTS_JSON")
    if not raw:
        rel = env.get("GFG_ENDPOINTS_FILE") or ".gfg-sync/endpoints.json"
        path = root / rel
        if not path.is_file():
            raise ConfigError(
                "No endpoint config found at %s. GFG has no official API and "
                "its submission endpoints could not be verified, so they are "
                "not hardcoded. Copy .gfg-sync/endpoints.example.json to "
                ".gfg-sync/endpoints.json and fill it in from a captured "
                "request (see docs/GFG_ENDPOINTS.md and SETUP.md)." % rel
            )
        raw = path.read_text(encoding="utf-8")
    try:
        cfg = json.loads(raw)
    except ValueError as exc:
        raise ConfigError("endpoints config is not valid JSON: %s" % exc)
    two_stage = bool(cfg.get("profile_url"))
    required = ["items_path", "fields", "accepted_field", "accepted_values",
                "code_url", "code_path"]
    required += ["problem_list_url", "profile_items_path"] if two_stage else ["list_url"]
    for key in required:
        if key not in cfg:
            raise ConfigError("endpoints config is missing '%s'." % key)
    for key in ("id", "problem_slug", "language", "timestamp"):
        if not cfg["fields"].get(key):
            raise ConfigError("endpoints config 'fields' is missing '%s'." % key)
    cfg.setdefault("page_start", 1)
    cfg.setdefault("max_pages", 1000)
    cfg.setdefault("order", "newest_first")
    cfg.setdefault("code_format", "json")
    cfg.setdefault("code_html_unescape", False)
    cfg.setdefault("problem_fields", {})
    cfg.setdefault("problem_page_url", "https://www.geeksforgeeks.org/problems/{slug}/1")
    return cfg


def truthy(value, default):
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def parse_ts(value, naive_offset_minutes=0):
    """Return epoch seconds (float) or None.

    Strings without a timezone are read as UTC plus ``naive_offset_minutes``
    (e.g. 330 for IST).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)?", value.strip())
    ):
        x = float(value)
        return x / 1000.0 if x > 1e11 else x
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(minutes=naive_offset_minutes)))
        return dt.timestamp()
    return None


def ts_label(epoch, raw):
    if epoch is not None:
        return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", str(raw or "")).strip("-")
    return cleaned or "unknown"


def slugify(text):
    return re.sub(r"[^a-z0-9._-]+", "-", str(text or "").lower()).strip("-.")


def clean_dirname(text):
    cleaned = re.sub(r'[\\/:*?"<>|]', "", str(text or "")).strip()
    return cleaned or "Unclassified"


def id_num(sid):
    return int(sid) if re.fullmatch(r"\d+", str(sid)) else None


def reached_last(sid, last_id):
    a, b = id_num(sid), id_num(last_id)
    if a is not None and b is not None:
        return a <= b
    return str(sid) == str(last_id)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_if_changed(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def create_exclusive(path, data):
    """Create a file only if it does not exist. Never overwrites."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "xb") as handle:
            handle.write(data)
        return True
    except FileExistsError:
        return False


# --------------------------------------------------------------------------
# HTML -> Markdown
# --------------------------------------------------------------------------

class _Md(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.lists = []
        self.pre = 0
        self.skip = 0
        self.hrefs = []
        self.row = None
        self.first_row = True

    def _w(self, text):
        self.out.append(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("p", "div", "section", "article", "blockquote"):
            self._w("\n\n")
        elif tag == "br":
            self._w("  \n")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._w("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in ("strong", "b"):
            self._w("**")
        elif tag in ("em", "i"):
            self._w("*")
        elif tag == "code" and not self.pre:
            self._w("`")
        elif tag == "pre":
            self._w("\n\n```\n")
            self.pre += 1
        elif tag in ("ul", "ol"):
            self.lists.append([tag, 0])
            self._w("\n")
        elif tag == "li":
            indent = "  " * max(0, len(self.lists) - 1)
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                marker = "%d. " % self.lists[-1][1]
            else:
                marker = "- "
            self._w("\n" + indent + marker)
        elif tag == "a":
            self.hrefs.append(a.get("href") or "")
            self._w("[")
        elif tag == "img":
            self._w("![%s](%s)" % (a.get("alt") or "", a.get("src") or ""))
        elif tag == "sup":
            self._w("^")
        elif tag == "sub":
            self._w("_")
        elif tag == "table":
            self.first_row = True
        elif tag == "tr":
            self.row = 0
            self._w("\n| ")
        elif tag in ("td", "th") and self.row is not None:
            self.row += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("p", "div", "section", "article", "blockquote"):
            self._w("\n\n")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._w("\n\n")
        elif tag in ("strong", "b"):
            self._w("**")
        elif tag in ("em", "i"):
            self._w("*")
        elif tag == "code" and not self.pre:
            self._w("`")
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            self._w("\n```\n\n")
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self._w("\n")
        elif tag == "a":
            href = self.hrefs.pop() if self.hrefs else ""
            self._w("](%s)" % href)
        elif tag in ("td", "th"):
            self._w(" | ")
        elif tag == "tr":
            if self.first_row and self.row:
                self._w("\n|" + " --- |" * self.row)
                self.first_row = False
            self.row = None

    def handle_data(self, data):
        if self.skip:
            return
        if self.pre:
            self._w(data)
        else:
            self._w(re.sub(r"\s+", " ", data))


def html_to_md(source):
    if not source:
        return ""
    parser = _Md()
    parser.feed(str(source))
    parser.close()
    text = "".join(parser.out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------
# GFG access
# --------------------------------------------------------------------------

def normalize(item, cfg):
    fields = cfg["fields"]

    def g(key):
        path = fields.get(key)
        return dig(item, path) if path else None

    sid = g("id")
    if sid is None or str(sid) == "":
        return None
    raw_ts = g("timestamp")
    accepted_values = {str(v).lower() for v in cfg["accepted_values"]}
    status = dig(item, cfg["accepted_field"])
    topics = g("topics")
    if isinstance(topics, str):
        topics = [t.strip() for t in topics.split(",") if t.strip()]
    if not isinstance(topics, list):
        topics = []
    return {
        "id": str(sid),
        "slug": slugify(g("problem_slug")),
        "title": g("problem_title"),
        "difficulty": g("difficulty"),
        "language": str(g("language") or "").strip(),
        "raw_ts": raw_ts,
        "epoch": parse_ts(raw_ts, int(cfg.get("timestamp_utc_offset_minutes") or 0)),
        "problem_id": g("problem_id"),
        "topics": [str(t) for t in topics],
        "accepted": str(status).lower() in accepted_values,
    }


def list_two_stage(client, cfg):
    """GFG flow: profile request lists solved problems, then one request per
    problem lists that problem's submissions (with submission ids)."""
    method = (cfg.get("profile_method") or "POST").upper()
    body = cfg.get("profile_body") if method == "POST" else None
    _, raw = client.get(cfg["profile_url"], body=body if body is not None else ({} if method == "POST" else None))
    try:
        data = json.loads(raw)
    except ValueError:
        raise SyncError("Profile response was not JSON.")
    tree = dig(data, cfg["profile_items_path"])
    if not isinstance(tree, dict):
        raise SyncError("Profile response has no object at '%s'. Check "
                        "profile_items_path in endpoints.json."
                        % cfg["profile_items_path"])
    problems = []
    for difficulty, group in tree.items():
        if not isinstance(group, dict):
            continue
        for pid, info in group.items():
            if isinstance(info, dict) and info.get("slug"):
                problems.append((difficulty, pid, info))
    log("Profile lists %d solved problem(s)." % len(problems))
    records, seen = [], set()
    for difficulty, pid, info in sorted(problems, key=lambda t: t[2]["slug"]):
        slug = info["slug"]
        url = cfg["problem_list_url"].replace("{slug}", urllib.parse.quote(slug, safe=""))
        status, raw = client.get(url, soft=(404,))
        if status != 200:
            log("Skipping %s (HTTP %d)." % (slug, status))
            continue
        try:
            sub = json.loads(raw)
        except ValueError:
            raise SyncError("Submission list for %s was not JSON." % slug)
        items = dig(sub, cfg["items_path"])
        if not isinstance(items, list):
            raise SyncError("Submission list for %s has no list at '%s'. "
                            "Check items_path in endpoints.json."
                            % (slug, cfg["items_path"]))
        for item in items:
            if not isinstance(item, dict):
                continue
            item = dict(item)
            item["_slug"] = slug
            item["_title"] = info.get("pname")
            item["_difficulty"] = difficulty
            item["_problem_id"] = pid
            rec = normalize(item, cfg)
            if rec is None or rec["id"] in seen:
                continue
            seen.add(rec["id"])
            records.append(rec)
    return records


def list_submissions(client, cfg, last_id, full):
    if cfg.get("profile_url"):
        # Every run re-lists all problems; already-synced ids are skipped later.
        return list_two_stage(client, cfg)
    records = []
    seen_ids = set()
    page = cfg["page_start"]
    for _ in range(int(cfg["max_pages"])):
        url = cfg["list_url"].replace("{page}", str(page))
        _, body = client.get(url)
        try:
            data = json.loads(body)
        except ValueError:
            raise SyncError("Submission list response was not JSON.")
        items = dig(data, cfg["items_path"])
        if not isinstance(items, list):
            raise SyncError(
                "Submission list response has no list at '%s'. Check "
                "items_path in endpoints.json." % cfg["items_path"]
            )
        if not items:
            break
        stop = False
        new_on_page = 0
        for item in items:
            rec = normalize(item, cfg)
            if rec is None:
                continue
            if rec["id"] in seen_ids:
                continue
            seen_ids.add(rec["id"])
            new_on_page += 1
            if not full and last_id is not None and reached_last(rec["id"], last_id):
                if cfg["order"] == "oldest_first":
                    continue
                stop = True
                break
            records.append(rec)
        if stop:
            break
        if new_on_page == 0:
            # Page repeated what we already saw: the endpoint is probably
            # ignoring {page}. Stop instead of looping up to max_pages.
            break
        size = cfg.get("page_size")
        if size and len(items) < int(size):
            break
        page += 1
    return records


def fetch_code(client, cfg, sid):
    url = cfg["code_url"].replace("{id}", urllib.parse.quote(str(sid), safe=""))
    _, body = client.get(url)
    if cfg["code_format"] == "raw":
        code = body
    else:
        try:
            data = json.loads(body)
        except ValueError:
            raise SyncError("Code response for a submission was not JSON.")
        value = dig(data, cfg["code_path"])
        if not isinstance(value, str):
            raise SyncError(
                "Code response has no string at '%s'. Check code_path in "
                "endpoints.json." % cfg["code_path"]
            )
        if cfg["code_html_unescape"]:
            value = html.unescape(value)
        code = value.encode("utf-8")
    return code


def fetch_problem(client, cfg, slug):
    """Return dict(title, difficulty, topics, description_html) or None."""
    if not cfg.get("problem_url"):
        return None
    url = cfg["problem_url"].replace("{slug}", urllib.parse.quote(slug, safe=""))
    status, body = client.get(url, soft=(403, 404))
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    pf = cfg["problem_fields"]

    def g(key):
        return dig(data, pf.get(key)) if pf.get(key) else None

    topics = g("topics")
    if isinstance(topics, str):
        topics = [t.strip() for t in topics.split(",") if t.strip()]
    return {
        "title": g("title"),
        "difficulty": g("difficulty"),
        "topics": [str(t) for t in topics] if isinstance(topics, list) else [],
        "description_html": g("description_html") or "",
    }


# --------------------------------------------------------------------------
# Repo layout
# --------------------------------------------------------------------------

def find_existing_folder(root, slug):
    for top in sorted(root.iterdir()):
        if not top.is_dir() or top.name.startswith(".") or top.name in SKIP_TOP_DIRS:
            continue
        if (top / slug).is_dir():
            return "%s/%s" % (top.name, slug)
    return None


def ext_for(language):
    key = language.lower()
    return EXTENSIONS.get(key) or (re.sub(r"[^a-z0-9]", "", key) or "txt")


def order_key(rec):
    num = id_num(rec["id"])
    return (rec["epoch"] if rec["epoch"] is not None else -1.0,
            num if num is not None else -1, rec["id"])


def build_problem_readme(slug, info, cfg, desc_md):
    url = cfg["problem_page_url"].replace("{slug}", slug)
    lines = ["# %s" % info["title"], "",
             "**Link:** [GeeksforGeeks](%s)  " % url,
             "**Difficulty:** %s  " % (info["difficulty"] or "Unknown")]
    if info["topics"]:
        lines.append("**Topics:** %s  " % ", ".join(info["topics"]))
    lines += ["", "## Problem Description", "", desc_md or PLACEHOLDER_DESC, ""]
    return "\n".join(lines)


def difficulty_sort_key(name):
    low = name.lower()
    if low in DIFFICULTY_ORDER:
        return (0, DIFFICULTY_ORDER.index(low), name)
    return (1, 0, name)


def esc(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def esc_link(text):
    return esc(text).replace("[", "\\[").replace("]", "\\]")


def render_block(state):
    by_diff = {}
    for slug, p in state["problems"].items():
        if not p.get("solution"):
            continue
        by_diff.setdefault(p.get("difficulty_dir") or "Unclassified", []).append((slug, p))
    diffs = sorted(by_diff, key=difficulty_sort_key)
    total = sum(len(v) for v in by_diff.values())
    out = [START, "## GeeksforGeeks Progress", "",
           "| Difficulty | Solved |", "| --- | ---: |"]
    for d in diffs:
        out.append("| %s | %d |" % (esc(d), len(by_diff[d])))
    out += ["| **Total** | **%d** |" % total, ""]
    for d in diffs:
        out += ["### %s" % d, "",
                "| # | Title | Topics | Solution | First solved |",
                "| ---: | --- | --- | --- | --- |"]
        rows = sorted(by_diff[d], key=lambda sp: (
            sp[1].get("first_epoch") if sp[1].get("first_epoch") is not None else 1e18, sp[0]))
        for slug, p in rows:
            number = p.get("problem_id") or "-"
            folder = urllib.parse.quote(p["folder"])
            sol = urllib.parse.quote("%s/%s" % (p["folder"], p["solution"]))
            first = p.get("first_epoch")
            date = (datetime.fromtimestamp(first, timezone.utc).strftime("%Y-%m-%d")
                    if first is not None else "n/a")
            out.append("| %s | [%s](%s/) | %s | [%s](%s) | %s |" % (
                esc(number), esc_link(p.get("title") or slug), folder,
                esc(", ".join(p.get("topics") or [])), esc_link(p["solution"]), sol, date))
        out.append("")
    out.append(END)
    return "\n".join(out)


def apply_root_readme(root, block):
    path = root / "README.md"
    if not path.exists():
        data = ("# GeeksforGeeks Solutions\n\n%s\n" % block).encode("utf-8")
        return write_if_changed(path, data)
    text = path.read_bytes().decode("utf-8")
    s, e = text.find(START), text.find(END)
    if s != -1 and e != -1 and e > s:
        new = text[:s] + block + text[e + len(END):]
    else:
        sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        new = text + sep + block + "\n"
    return write_if_changed(path, new.encode("utf-8"))


# --------------------------------------------------------------------------
# Main sync
# --------------------------------------------------------------------------

def load_state(root):
    path = root / STATE_PATH
    if path.is_file():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            raise SyncError("%s is not valid JSON; fix or delete it." % STATE_PATH)
        state.setdefault("problems", {})
        return state
    return {"version": 1, "last_submission_id": None, "last_submission_epoch": None,
            "problems": {}}


def save_state(root, state):
    text = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return write_if_changed(root / STATE_PATH, text.encode("utf-8"))


def log(msg):
    print(msg, flush=True)


def sync(root, env, client=None):
    root = Path(root)
    cfg = load_endpoints(root, env)
    langs = {x.strip().lower() for x in (env.get("GFG_LANGUAGES") or "java").split(",") if x.strip()}
    full = truthy(env.get("FULL_SYNC"), False)
    make_stub = truthy(env.get("CREATE_MAIN_STUB"), True)
    archive = truthy(env.get("ARCHIVE_ALL_ACCEPTED"), True)
    client = client or Client(cfg, env)
    state = load_state(root)
    last_id = None if full else state.get("last_submission_id")

    log("Listing submissions (%s)..." % ("full" if last_id is None else "incremental"))
    records = list_submissions(client, cfg, last_id, full)
    log("Found %d submission(s) to look at." % len(records))

    wanted = [r for r in records
              if r["accepted"] and r["slug"] and r["language"].lower() in langs]
    groups = {}
    for rec in wanted:
        groups.setdefault(rec["slug"], []).append(rec)

    for slug in sorted(groups):
        subs = sorted(groups[slug], key=order_key)
        p = state["problems"].setdefault(slug, {"ids": [], "hashes": []})
        subs = [s for s in subs if s["id"] not in p["ids"]]
        if not subs:
            continue
        newest = subs[-1]
        list_title = next((s["title"] for s in reversed(subs) if s["title"]), None)
        list_diff = next((s["difficulty"] for s in reversed(subs) if s["difficulty"]), None)
        list_topics = next((s["topics"] for s in reversed(subs) if s["topics"]), [])
        list_pid = next((s["problem_id"] for s in reversed(subs) if s["problem_id"]), None)

        folder = p.get("folder")
        if not folder or not (root / folder).is_dir():
            folder = find_existing_folder(root, slug)
        readme_path = (root / folder / "README.md") if folder else None
        readme_missing = readme_path is None or not readme_path.exists()
        readme_is_placeholder = False
        if not readme_missing:
            try:
                readme_is_placeholder = PLACEHOLDER_DESC in readme_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                pass
        details = None
        if readme_missing or readme_is_placeholder or not p.get("title"):
            details = fetch_problem(client, cfg, slug)
        title = (details or {}).get("title") or list_title or p.get("title") or slug
        difficulty = (details or {}).get("difficulty") or list_diff or p.get("difficulty")
        topics = (details or {}).get("topics") or list_topics or p.get("topics") or []
        if not folder:
            folder = "%s/%s" % (clean_dirname(difficulty), slug)
        pdir = root / folder
        pdir.mkdir(parents=True, exist_ok=True)

        ext = ext_for(newest["language"])
        for rec in subs:
            code = fetch_code(client, cfg, rec["id"])
            digest = sha256(code)
            if digest not in p["hashes"]:
                if archive:
                    name = "%s_%s.%s" % (ts_label(rec["epoch"], rec["raw_ts"]),
                                         re.sub(r"[^A-Za-z0-9_-]", "_", rec["id"]),
                                         ext_for(rec["language"]))
                    write_if_changed(pdir / "submissions" / name, code)
                p["hashes"].append(digest)
            p["ids"].append(rec["id"])
            if rec is newest:
                newest_code = code
            if rec["epoch"] is not None and (p.get("first_epoch") is None
                                             or rec["epoch"] < p["first_epoch"]):
                p["first_epoch"] = rec["epoch"]

        prev = p.get("latest_key")
        key = list(order_key(newest))
        if prev is None or tuple(key) > tuple(prev) or not (pdir / ("Solution." + ext)).exists():
            write_if_changed(pdir / ("Solution." + ext), newest_code)
            old = p.get("solution")
            if old and old != "Solution." + ext and re.fullmatch(r"Solution\.\w+", old):
                try:
                    (pdir / old).unlink()
                except OSError:
                    pass
            p["latest_key"] = key
            p["solution"] = "Solution." + ext

        info = {"title": title, "difficulty": difficulty, "topics": topics}
        desc_md = html_to_md((details or {}).get("description_html"))
        readme_bytes = build_problem_readme(slug, info, cfg, desc_md).encode("utf-8")
        if readme_is_placeholder and desc_md:
            # Only our own auto-generated placeholder is ever replaced.
            write_if_changed(pdir / "README.md", readme_bytes)
        else:
            create_exclusive(pdir / "README.md", readme_bytes)
        if make_stub and ext == "java":
            create_exclusive(pdir / "Main.java", MAIN_STUB.encode("utf-8"))

        p.update({"title": title, "difficulty": difficulty, "topics": topics,
                  "folder": folder, "difficulty_dir": folder.split("/")[0],
                  "problem_id": list_pid if list_pid is not None else p.get("problem_id")})
        log("Synced %s (%d new)." % (slug, len(subs)))
        # Persist progress so a later failure does not lose finished work.
        # last_submission_id is untouched here, so a re-run is still safe.
        save_state(root, state)

    # Marker for the next incremental run: newest id across every listed item.
    if records:
        if all(id_num(r["id"]) is not None for r in records):
            top = max(records, key=lambda r: id_num(r["id"]))
        elif cfg["order"] == "oldest_first":
            top = records[-1]
        else:
            top = records[0]
        state["last_submission_id"] = top["id"]
        state["last_submission_epoch"] = top["epoch"]

    apply_root_readme(root, render_block(state))
    # State is saved only now, after everything above succeeded.
    save_state(root, state)
    log("Done.")
    return state


def main(argv=None, env=None):
    parser = argparse.ArgumentParser(description="Sync accepted GFG submissions.")
    parser.add_argument("--root", default=".", help="repository root")
    args = parser.parse_args(argv)
    try:
        sync(Path(args.root), dict(os.environ if env is None else env))
    except ConfigError as exc:
        print("CONFIG ERROR: %s" % exc, file=sys.stderr)
        return 2
    except SyncError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
