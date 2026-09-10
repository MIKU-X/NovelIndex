"""Offline regressions for snapshot preservation, preview isolation and Qidian resume."""
import functools
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from scrapers import base, qidian
from scripts.serve_preview import SafeHandler


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / "snapshot.json"
        self.previous = b'{"previous": "valid"}'
        self.target.write_bytes(self.previous)

    def assert_previous_preserved(self):
        self.assertEqual(self.target.read_bytes(), self.previous)
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])

    def test_serialization_failure_preserves_existing_snapshot(self):
        with self.assertRaises(TypeError):
            base.write_json(str(self.target), {"invalid": object()})
        self.assert_previous_preserved()

    def test_locked_destination_preserves_existing_snapshot(self):
        with patch.object(base.os, "replace", side_effect=PermissionError("locked")), patch.object(base.time, "sleep"):
            with self.assertRaises(PermissionError):
                base.write_json(str(self.target), {"new": "data"})
        self.assert_previous_preserved()

    def test_disk_flush_failure_preserves_existing_snapshot(self):
        with patch.object(base.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                base.write_json(str(self.target), {"new": "data"})
        self.assert_previous_preserved()

    def test_transient_lock_retries_and_writes_valid_json(self):
        replace = base.os.replace
        attempts = []

        def transient(src, dst):
            attempts.append(dst)
            if len(attempts) == 1:
                raise PermissionError("temporary lock")
            return replace(src, dst)

        with patch.object(base.os, "replace", side_effect=transient), patch.object(base.time, "sleep"):
            base.write_json(str(self.target), {"new": "数据"})
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), {"new": "数据"})
        self.assertEqual(len(attempts), 2)


class QuietHandler(SafeHandler):
    def log_message(self, *args):
        pass


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        for rel in (
            "index.html", "author.html", "trend.html", "css/style.css", "js/app.js",
            "LICENSE.txt", "NOTICE.txt", "site-info.json", "release-manifest.json",
            "data/latest_qidian_hotsales_ranks.json", "data/trends/qidian_2026-09-08.json",
            "data/author/reader_profile_qidian_hotsales.json", "api/latest.json",
            "config/private.json", ".claude/settings.local.json", ".env",
            "probe_out/response.json", "Scrapling-main/example.html",
            "data/task_state_qidian_hotsales_20260908.json", "data/.cache/private.json",
        ):
            path = cls.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture", encoding="utf-8")
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(QuietHandler, directory=cls.temp.name)
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request_status(self, url, method="GET"):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            conn.request(method, url)
            response = conn.getresponse()
            response.read()
            return response.status
        finally:
            conn.close()

    def test_public_pages_and_data_remain_available(self):
        for url in (
            "/", "/index.html", "/author.html", "/trend.html", "/css/style.css",
            "/js/app.js?v=1", "/data/latest_qidian_hotsales_ranks.json",
            "/LICENSE.txt", "/NOTICE.txt", "/site-info.json", "/release-manifest.json",
            "/data/trends/qidian_2026-09-08.json",
            "/data/author/reader_profile_qidian_hotsales.json", "/api/latest.json",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.request_status(url), 200)

    def test_preview_sends_browser_security_headers(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            conn.request('GET', '/index.html')
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
            self.assertEqual(response.getheader('Referrer-Policy'), 'no-referrer')
            self.assertEqual(response.getheader('X-Frame-Options'), 'DENY')
        finally:
            conn.close()

    def test_private_and_traversal_paths_are_blocked_for_get_and_head(self):
        for method in ("GET", "HEAD"):
            for url in (
                "/config/private.json", "/data/../config/private.json",
                "/data/%2e%2e/.claude/settings.local.json",
                "/data/..%20/config/private.json", "/probe_out/response.json",
                "/Scrapling-main/example.html", "/.env", "/.env%20",
                "/data/task_state_qidian_hotsales_20260908.json",
                "/data/.cache/private.json", "/data/PRIVATE~1.JSO",
                "/data/latest_qidian_hotsales_ranks.json::$DATA", "/data/",
            ):
                with self.subTest(url=url, method=method):
                    self.assertEqual(self.request_status(url, method), 404)

    def test_symlink_cannot_expose_a_private_file(self):
        link = self.root / "data" / "linked.json"
        try:
            link.symlink_to(self.root / "config" / "private.json")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable for this account")
        self.addCleanup(link.unlink)
        self.assertEqual(self.request_status("/data/linked.json"), 404)


class FakeRankPage:
    def __init__(self, second_page="failed"):
        self.second_page = second_page
        self.page_number = 1

    def goto(self, url, **kwargs):
        self.page_number = 2 if "page2/" in url else 1
        if self.page_number == 2 and self.second_page == "failed":
            raise OSError("simulated network failure")

    def wait_for_selector(self, *args, **kwargs):
        pass

    def content(self):
        if self.page_number == 2:
            if self.second_page == "extract_error":
                raise RuntimeError("simulated DOM extraction error")
            return "<html></html>"
        items = "".join(f'<li><h2><a href="/book/{i}/">fixture {i}</a></h2></li>' for i in range(20))
        return f'<div class="book-img-text"><ul>{items}</ul></div>'


class QidianResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.object(base, "OUTPUT_DIR", self.temp.name):
            self.scraper = qidian.QidianScraper()
        for mock in (
            patch.object(qidian, "_rand_sleep"), patch.object(qidian.time, "sleep"),
            patch.object(qidian, "_is_captcha_page", return_value=False),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def scrape_with_page(self, page):
        output, state = (Path(self.temp.name) / name for name in ("snapshot.json", "state.json"))
        with patch.object(self.scraper, "_chan_ids", return_value={"fixture": 1}), patch.object(self.scraper, "_fetch_details"):
            self.scraper.scrape_categories(
                page, {"name": "fixture", "url": "https://example.invalid/rank/"},
                output_file=str(output), state_file=str(state),
            )
        return json.loads(state.read_text(encoding="utf-8"))

    def test_failed_second_page_remains_retryable(self):
        state = self.scrape_with_page(FakeRankPage())
        self.assertEqual(state["completed"], [])
        self.assertEqual(state["raw"], [])

    def test_extraction_failure_remains_retryable(self):
        state = self.scrape_with_page(FakeRankPage("extract_error"))
        self.assertEqual(state["completed"], [])

    def test_real_empty_second_page_completes_a_short_category(self):
        state = self.scrape_with_page(FakeRankPage("empty"))
        self.assertEqual(state["completed"], ["chan_fixture"])
        self.assertEqual(len(state["raw"]), 20)

    def test_details_fill_empty_fields_and_preserve_existing_values(self):
        rows = [
            {"bookId": "1", "author": "", "intro": ""},
            {"bookId": "2", "author": "original author", "intro": "original intro"},
        ]
        with patch.object(self.scraper, "_fetch_detail", return_value=("10万字", "new author", "new intro", False)):
            self.scraper._fetch_details(None, rows, None, None, {}, set())
        self.assertEqual((rows[0]["author"], rows[0]["intro"]), ("new author", "new intro"))
        self.assertEqual((rows[1]["author"], rows[1]["intro"]), ("original author", "original intro"))


if __name__ == "__main__":
    unittest.main()
