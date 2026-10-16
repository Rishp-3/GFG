"""Mocked tests for scripts/gfg_sync.py (no network)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gfg_sync  # noqa: E402

LIST = "https://gfg.test/subs?page="
CODE = "https://gfg.test/code/"
PROB = "https://gfg.test/problem/"

# The shape below is an ASSUMED shape (TODO(verify)), see docs/GFG_ENDPOINTS.md.
CFG = {
    "list_url": LIST + "{page}",
    "page_start": 1,
    "order": "newest_first",
    "items_path": "results.submissions",
    "fields": {
        "id": "id", "problem_slug": "slug", "problem_title": "title",
        "difficulty": "difficulty", "language": "language",
        "timestamp": "submitted_at", "problem_id": "problem_id", "topics": "tags",
    },
    "accepted_field": "status",
    "accepted_values": ["correct"],
    "code_url": CODE + "{id}",
    "code_format": "json",
    "code_path": "data.code",
    "problem_url": PROB + "{slug}",
    "problem_fields": {"title": "title", "difficulty": "difficulty",
                       "description_html": "description", "topics": "tags"},
}


class Fake:
    """Stands in for http_request."""

    def __init__(self):
        self.subs = []
        self.page_size = 2
        self.codes = {}
        self.problems = {}
        self.calls = []
        self.list_status_queue = []
        self.code_status = 200

    def add(self, sid, slug="two-sum", lang="Java", status="correct", code=None,
            difficulty="Easy", title="Two Sum",
            desc="<p>Find <b>two</b> numbers.</p><ul><li>a</li></ul>"):
        self.subs.insert(0, {
            "id": sid, "slug": slug, "title": title, "difficulty": difficulty,
            "language": lang, "submitted_at": "2025-01-02T03:%02d:05Z" % sid,
            "status": status, "problem_id": 100 + sid, "tags": ["Array", "Hash"],
        })
        self.codes[str(sid)] = code if code is not None else "class Solution { /* %d */ }\n" % sid
        self.problems.setdefault(slug, {"title": title, "difficulty": difficulty,
                                        "description": desc, "tags": ["Array", "Hash"]})

    def __call__(self, url, headers, timeout=60):
        self.calls.append(url)
        if url.startswith(LIST):
            if self.list_status_queue:
                status = self.list_status_queue.pop(0)
                if status != 200:
                    return status, b""
            page = int(url[len(LIST):])
            chunk = self.subs[(page - 1) * self.page_size: page * self.page_size]
            return 200, json.dumps({"results": {"submissions": chunk}}).encode()
        if url.startswith(CODE):
            if self.code_status != 200:
                return self.code_status, b""
            return 200, json.dumps({"data": {"code": self.codes[url[len(CODE):]]}}).encode()
        if url.startswith(PROB):
            info = self.problems.get(url[len(PROB):])
            if info is None:
                return 404, b""
            if isinstance(info, int):
                return info, b""
            return 200, json.dumps(info).encode()
        return 500, b""

    def calls_to(self, prefix):
        return [c for c in self.calls if c.startswith(prefix)]


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".gfg-sync").mkdir()
        (self.root / ".gfg-sync" / "endpoints.json").write_text(json.dumps(CFG))
        self.fake = Fake()
        http_patch = mock.patch.object(gfg_sync, "http_request", self.fake)
        http_patch.start()
        self.addCleanup(http_patch.stop)
        sleep_patch = mock.patch.object(gfg_sync.time, "sleep")
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        self.env = {"REQUEST_DELAY": "0", "GFG_COOKIE_HEADER": "secretcookie=supersecret"}

    def run_sync(self, **extra):
        env = dict(self.env)
        env.update(extra)
        return gfg_sync.sync(self.root, env)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in sorted(self.root.rglob("*")) if p.is_file()}


class TestOutputFiles(SyncTestCase):
    def test_solution_is_byte_exact_with_crlf(self):
        code = "class Solution {\r\n    int f() { return 1; }\r\n}\r\n  \r\n"
        self.fake.add(1, code=code)
        self.run_sync()
        data = (self.root / "Easy" / "two-sum" / "Solution.java").read_bytes()
        self.assertEqual(data, code.encode("utf-8"))
        self.assertIn(b"\r\n", data)

    def test_latest_submission_becomes_solution(self):
        self.fake.add(1, code="old\n")
        self.fake.add(2, code="new\n")
        self.run_sync()
        self.assertEqual((self.root / "Easy" / "two-sum" / "Solution.java").read_bytes(), b"new\n")

    def test_existing_main_untouched_and_folder_reused(self):
        main = self.root / "Basic" / "two-sum" / "Main.java"
        main.parent.mkdir(parents=True)
        main.write_bytes(b"MY MAIN\r\n")
        self.fake.add(1, difficulty="Easy")
        self.run_sync()
        self.assertEqual(main.read_bytes(), b"MY MAIN\r\n")
        self.assertFalse((self.root / "Easy" / "two-sum").exists())
        self.assertTrue((self.root / "Basic" / "two-sum" / "Solution.java").exists())

    def test_main_stub_created_only_when_missing(self):
        self.fake.add(1)
        self.run_sync()
        stub = self.root / "Easy" / "two-sum" / "Main.java"
        self.assertTrue(stub.exists())
        self.assertIn(b"public class Main", stub.read_bytes())
        stub.write_bytes(b"EDITED\n")
        self.fake.add(2)
        self.run_sync()
        self.assertEqual(stub.read_bytes(), b"EDITED\n")

    def test_main_stub_toggle_off(self):
        self.fake.add(1)
        self.run_sync(CREATE_MAIN_STUB="false")
        self.assertFalse((self.root / "Easy" / "two-sum" / "Main.java").exists())

    def test_create_exclusive_never_overwrites(self):
        path = self.root / "x.txt"
        self.assertTrue(gfg_sync.create_exclusive(path, b"one"))
        self.assertFalse(gfg_sync.create_exclusive(path, b"two"))
        self.assertEqual(path.read_bytes(), b"one")

    def test_problem_readme_created_with_markdown(self):
        self.fake.add(1)
        self.run_sync()
        text = (self.root / "Easy" / "two-sum" / "README.md").read_text()
        self.assertIn("# Two Sum", text)
        self.assertIn("https://www.geeksforgeeks.org/problems/two-sum/1", text)
        self.assertIn("**Difficulty:** Easy", text)
        self.assertIn("Array, Hash", text)
        self.assertIn("**two**", text)
        self.assertIn("- a", text)

    def test_problem_readme_only_if_missing(self):
        readme = self.root / "Easy" / "two-sum" / "README.md"
        readme.parent.mkdir(parents=True)
        readme.write_bytes(b"custom notes\r\n")
        self.fake.add(1)
        self.run_sync()
        self.assertEqual(readme.read_bytes(), b"custom notes\r\n")

    def test_premium_problem_gets_placeholder_but_solution_saved(self):
        self.fake.add(1)
        self.fake.problems["two-sum"] = 403
        self.run_sync()
        folder = self.root / "Easy" / "two-sum"
        self.assertIn("not available", (folder / "README.md").read_text())
        self.assertTrue((folder / "Solution.java").exists())

    def test_language_and_status_filters(self):
        self.fake.add(1, slug="cpp-only", lang="C++")
        self.fake.add(2, slug="wrong", status="wrong")
        self.fake.add(3, slug="good")
        self.run_sync()
        self.assertTrue((self.root / "Easy" / "good").exists())
        self.assertFalse((self.root / "Easy" / "cpp-only").exists())
        self.assertFalse((self.root / "Easy" / "wrong").exists())

    def test_languages_env_selects_other_language(self):
        self.fake.add(1, slug="cpp-prob", lang="C++")
        self.run_sync(GFG_LANGUAGES="java,c++")
        folder = self.root / "Easy" / "cpp-prob"
        self.assertTrue((folder / "Solution.cpp").exists())
        self.assertFalse((folder / "Main.java").exists())

    def test_archive_dedupes_by_sha256(self):
        self.fake.add(5, code="same\n")
        self.fake.add(6, code="same\n")
        self.fake.add(7, code="different\n")
        self.run_sync()
        files = sorted(p.name for p in (self.root / "Easy" / "two-sum" / "submissions").iterdir())
        self.assertEqual(files, ["20250102T030505Z_5.java", "20250102T030705Z_7.java"])

    def test_archive_toggle_off(self):
        self.fake.add(1)
        self.run_sync(ARCHIVE_ALL_ACCEPTED="false")
        self.assertFalse((self.root / "Easy" / "two-sum" / "submissions").exists())


class TestRootReadme(SyncTestCase):
    def test_created_with_markers_and_tables(self):
        self.fake.add(1)
        self.fake.add(2, slug="other", difficulty="Hard", title="Other")
        self.run_sync()
        text = (self.root / "README.md").read_text()
        self.assertIn(gfg_sync.START, text)
        self.assertIn(gfg_sync.END, text)
        self.assertIn("| Easy | 1 |", text)
        self.assertIn("| Hard | 1 |", text)
        self.assertIn("| **Total** | **2** |", text)
        self.assertIn("[Two Sum](Easy/two-sum/)", text)
        self.assertIn("(Easy/two-sum/Solution.java)", text)
        self.assertIn("2025-01-02", text)
        self.assertNotIn("ast synced", text)
        self.assertLess(text.index("### Easy"), text.index("### Hard"))

    def test_only_block_between_markers_is_regenerated(self):
        before = "# My notes\r\nkeep me\r\n\r\n"
        after = "\r\n\r\nfooter stays\r\n"
        (self.root / "README.md").write_bytes(
            (before + gfg_sync.START + "\nSTALE\n" + gfg_sync.END + after).encode())
        self.fake.add(1)
        self.run_sync()
        text = (self.root / "README.md").read_bytes().decode()
        self.assertTrue(text.startswith(before))
        self.assertTrue(text.endswith(after))
        self.assertNotIn("STALE", text)
        self.assertIn("| Easy | 1 |", text)

    def test_readme_without_markers_gets_block_appended(self):
        original = "# Existing\n\nsome text\n"
        (self.root / "README.md").write_text(original)
        self.fake.add(1)
        self.run_sync()
        text = (self.root / "README.md").read_text()
        self.assertTrue(text.startswith(original))
        self.assertEqual(text.count(gfg_sync.START), 1)
        self.assertGreater(text.index(gfg_sync.START), len(original) - 1)


class TestIdempotentAndIncremental(SyncTestCase):
    def test_second_run_makes_zero_changes(self):
        self.fake.add(1)
        self.fake.add(2, slug="other", difficulty="Medium", title="Other")
        self.run_sync()
        first = self.snapshot()
        self.fake.calls.clear()
        self.run_sync()
        self.assertEqual(self.snapshot(), first)
        self.assertEqual(self.fake.calls_to(CODE), [])

    def test_full_sync_twice_also_makes_zero_changes(self):
        self.fake.add(1)
        self.run_sync()
        first = self.snapshot()
        self.run_sync(FULL_SYNC="true")
        self.assertEqual(self.snapshot(), first)

    def test_incremental_stops_at_last_synced_id(self):
        self.fake.add(1, slug="a")
        self.fake.add(2, slug="b")
        self.fake.add(3, slug="c")
        self.run_sync()
        state = json.loads((self.root / ".gfg-sync" / "state.json").read_text())
        self.assertEqual(state["last_submission_id"], "3")
        self.fake.add(4, slug="d")
        self.fake.calls.clear()
        self.run_sync()
        self.assertEqual(self.fake.calls_to(LIST), [LIST + "1"])
        self.assertEqual(self.fake.calls_to(CODE), [CODE + "4"])
        self.assertTrue((self.root / "Easy" / "d" / "Solution.java").exists())

    def test_first_run_is_full_backfill(self):
        for i in range(1, 6):
            self.fake.add(i, slug="p%d" % i)
        self.run_sync()
        for i in range(1, 6):
            self.assertTrue((self.root / "Easy" / ("p%d" % i) / "Solution.java").exists())


class TestErrors(SyncTestCase):
    def test_auth_error_is_clear_and_state_not_saved(self):
        self.fake.add(1)
        self.fake.list_status_queue = [401]
        with self.assertRaises(gfg_sync.AuthError) as ctx:
            self.run_sync()
        self.assertIn("cookies", str(ctx.exception))
        self.assertNotIn("supersecret", str(ctx.exception))
        self.assertFalse((self.root / ".gfg-sync" / "state.json").exists())

    def test_retries_on_429_then_succeeds(self):
        self.fake.add(1)
        self.fake.list_status_queue = [429, 503, 200]
        self.run_sync()
        self.assertTrue((self.root / "Easy" / "two-sum" / "Solution.java").exists())
        self.assertEqual(len(self.fake.calls_to(LIST)), 4)  # 3 attempts + the empty last page

    def test_state_not_saved_when_run_fails(self):
        self.fake.add(1)
        self.fake.code_status = 500
        with self.assertRaises(gfg_sync.SyncError):
            self.run_sync()
        self.assertFalse((self.root / ".gfg-sync" / "state.json").exists())

    def test_missing_endpoint_config_is_a_config_error(self):
        (self.root / ".gfg-sync" / "endpoints.json").unlink()
        with self.assertRaises(gfg_sync.ConfigError):
            self.run_sync()
        self.assertEqual(gfg_sync.main(["--root", str(self.root)], env=self.env), 2)

    def test_cookie_sent_but_never_logged(self):
        seen = []
        real = self.fake

        def spy(url, headers, timeout=60):
            seen.append(dict(headers))
            return real(url, headers, timeout)

        self.fake.add(1)
        with mock.patch.object(gfg_sync, "http_request", spy), \
                mock.patch("builtins.print") as printed:
            self.run_sync()
        self.assertEqual(seen[0]["Cookie"], "secretcookie=supersecret")
        for call in printed.call_args_list:
            self.assertNotIn("supersecret", " ".join(map(str, call.args)))


class TestHtmlToMd(unittest.TestCase):
    def test_basic_conversion(self):
        md = gfg_sync.html_to_md(
            "<h3>Title</h3><p>Use <code>x</code> and <a href='http://a.b'>link</a></p>"
            "<pre>a = 1\nb = 2</pre><ol><li>one</li><li>two</li></ol><script>bad()</script>")
        self.assertIn("### Title", md)
        self.assertIn("`x`", md)
        self.assertIn("[link](http://a.b)", md)
        self.assertIn("```\na = 1\nb = 2\n```", md)
        self.assertIn("1. one", md)
        self.assertIn("2. two", md)
        self.assertNotIn("bad()", md)

    def test_empty(self):
        self.assertEqual(gfg_sync.html_to_md(""), "")
        self.assertEqual(gfg_sync.html_to_md(None), "")


if __name__ == "__main__":
    unittest.main()
