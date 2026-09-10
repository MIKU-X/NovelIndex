import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

from scrapers import base, qidian
from scrapers.qidian_state import CHINA_TZ, QidianState, observed_now
from scripts import runtime_state
from scripts.runtime_state import LockUnavailable, exclusive_lock
from scripts.snapshot_utils import parse_metric, snapshot_complete, snapshot_quality


class RuntimeTests(unittest.TestCase):
    def test_explicit_offline_build_does_not_load_or_create_ai_endpoints(self):
        from scripts import build_latest as build

        with patch.object(sys, 'argv', ['build', '--rank-type', 'qidian_hotsales', '--no-ai']), \
             patch.object(build, 'load_ai_endpoints') as endpoints, patch.object(build, 'AIClient') as client, \
             patch.object(build, 'process_one_rank_type', return_value=True) as process, \
             patch.object(build.os, 'makedirs'):
            build.main.__wrapped__()
        endpoints.assert_not_called()
        client.assert_not_called()
        self.assertIsNone(process.call_args.kwargs['shared_ai_client'])

    def test_pipeline_forwards_offline_option_to_each_platform_rank(self):
        import run_all

        with patch.object(sys, 'argv', ['run_all', '--no-scrape', '--platform', 'qidian', '--no-ai']), \
             patch.object(run_all, 'run_cmd', return_value=True) as command, \
             patch.object(run_all.subprocess, 'run'):
            run_all.main.__wrapped__()
        self.assertEqual(command.call_count, 4)
        for call in command.call_args_list:
            self.assertIn('--no-ai', call.args[0])

    def test_live_smoke_respects_existing_cooldown_without_opening_browser(self):
        import smoke_qidian_patchright as smoke
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = QidianState(root / 'qidian.sqlite3')
            state.defer(3600, 'fixture cooldown')
            state.close()
            with patch.object(runtime_state, 'RUNTIME_DIR', root), patch.object(smoke, 'OUTPUT_DIR', root / 'reports'), \
                 patch.object(base, 'OUTPUT_DIR', str(root / 'data')), \
                 patch.object(smoke, 'load_environment'), patch.object(sys, 'argv', ['smoke']), \
                 patch.object(smoke.QidianScraper, '_sync_playwright') as browser:
                self.assertEqual(smoke.main(), 75)
                browser.assert_not_called()
            report = json.loads(next((root / 'reports').glob('*.json')).read_text(encoding='utf-8'))
            self.assertFalse(report['ok'])
            self.assertEqual(report['navigation_counts']['rank_requests'], 0)
            self.assertGreater(report['cooldown_seconds_remaining'], 3500)

    def test_interrupted_scheduler_reaps_child_before_releasing_its_lock(self):
        import run_all

        def interrupted_output():
            raise KeyboardInterrupt
            yield b""

        child = Mock()
        child.stdout = Mock()
        child.stdout.__iter__ = Mock(return_value=interrupted_output())
        child.poll.return_value = None
        with patch.object(run_all.os, "makedirs"), patch("builtins.open", mock_open()), \
             patch.object(run_all.subprocess, "Popen", return_value=child):
            with self.assertRaises(KeyboardInterrupt):
                run_all.run_cmd(["fixture"], "fixture")
        child.kill.assert_called_once()
        child.wait.assert_called_once_with(timeout=10)
        child.stdout.close.assert_called_once()

    def test_rank_only_smoke_does_not_request_detail_or_change_snapshot(self):
        import smoke_qidian_patchright as smoke

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'data'
            data.mkdir()
            snapshot = data / 'existing.json'
            snapshot.write_bytes(b'{"fixture": true}')
            page, browser = Mock(), Mock(version='fixture')
            page.goto.return_value = SimpleNamespace(status=200)
            page.content.return_value = '<html>fixture</html>'
            books = [{'bookId': '1', 'bookName': 'fixture', 'thumbUrl': 'https://example.invalid/1.jpg'}]
            with patch.object(runtime_state, 'RUNTIME_DIR', root / '.runtime'), \
                 patch.object(base, 'OUTPUT_DIR', str(data)), patch.object(smoke, 'OUTPUT_DIR', root / 'reports'), \
                 patch.object(smoke, 'load_environment'), patch.object(sys, 'argv', ['smoke', '--rank-only']), \
                 patch.object(qidian.QidianScraper, '_sync_playwright',
                              return_value=Mock(__module__='patchright.sync_api', return_value=nullcontext(object()))), \
                 patch.object(qidian.QidianScraper, 'launch_browser', return_value=(browser, None, page)), \
                 patch.object(smoke, '_is_captcha_page', return_value=False), \
                 patch.object(smoke, 'parse_rank', return_value=books), patch.object(smoke, 'parse_detail') as detail:
                self.assertEqual(smoke.main(), 0)
            page.goto.assert_called_once()
            detail.assert_not_called()
            browser.close.assert_called_once()
            self.assertEqual({p.name: p.read_bytes() for p in data.iterdir()}, {'existing.json': b'{"fixture": true}'})
            report = json.loads(next((root / 'reports').glob('*.json')).read_text(encoding='utf-8'))
            self.assertTrue(report['ok'])
            self.assertEqual(report['navigation_counts']['rank_requests'], 1)
            self.assertEqual(report['navigation_counts']['detail_requests'], 0)

    def test_live_lock_cannot_be_stolen_and_releases_after_process_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.lock"
            code = (
                "import sys; from scripts.runtime_state import exclusive_lock; "
                "guard=exclusive_lock(sys.argv[1]); guard.__enter__(); "
                "print('ready',flush=True); sys.stdin.readline()"
            )
            child = subprocess.Popen([sys.executable, "-c", code, str(path)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                with self.assertRaises(LockUnavailable), exclusive_lock(path):
                    self.fail("live lock was stolen")
                child.terminate()
                child.wait(timeout=5)
                with exclusive_lock(path):
                    self.assertTrue(path.exists())
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)


class QidianPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for item in (
            patch.object(base, "OUTPUT_DIR", str(self.root / "data")),
            patch.object(runtime_state, "RUNTIME_DIR", self.root / ".runtime"),
            patch.object(qidian, "_rand_sleep"), patch.object(qidian.time, "sleep"),
            patch.dict(os.environ, {"QD_DETAIL_TTL_HOURS": "24", "QD_DETAIL_MAX_REQUESTS": "0"}),
        ):
            item.start()
            self.addCleanup(item.stop)
        self.scraper = qidian.QidianScraper()

    def attach_state(self):
        state = QidianState(self.root / ".runtime" / "qidian.sqlite3")
        self.addCleanup(state.close)
        self.scraper._state = state
        return state

    def test_all_lists_precede_details_and_same_day_rerun_uses_saved_progress(self):
        events = []
        ranks = [{"slug": f"qidian_fixture{i}", "name": f"fixture{i}", "url": f"https://example.invalid/{i}/"}
                 for i in range(4)]

        def scrape_category(*args):
            events.append("list")
            return [{"bookId": str(len(events)), "bookName": "fixture", "author": "A", "intro": "I"}]

        def fetch_detail(*args):
            self.assertEqual(events[:4], ["list"] * 4)
            self.assertEqual(len(list((self.root / "data").glob("qidian_*_ranks_*.json"))), 4)
            events.append("detail")
            return "10万字", "A", "I", False

        browser = Mock()
        with patch.object(qidian, "iter_platform_ranks", return_value=ranks), \
             patch.object(qidian, "snapshot_complete", return_value=True), \
             patch.object(self.scraper, "_chan_ids", return_value={"fixture": 1}), \
             patch.object(self.scraper, "_sync_playwright", return_value=lambda: nullcontext(object())), \
             patch.object(self.scraper, "launch_browser", return_value=(browser, None, object())), \
             patch.object(self.scraper, "_scrape_rank_category", side_effect=scrape_category) as list_mock, \
             patch.object(self.scraper, "_fetch_detail", side_effect=fetch_detail) as detail_mock:
            self.scraper.run()
            self.assertEqual(events, ["list"] * 4 + ["detail"] * 4)
            list_mock.reset_mock()
            detail_mock.reset_mock()
            self.scraper.run()
            list_mock.assert_not_called()
            detail_mock.assert_not_called()
        for path in (self.root / "data").glob("qidian_*_ranks_*.json"):
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["quality"]["rank_status"], "complete")
            self.assertEqual(snapshot["quality"]["metrics_missing"], 0)
            self.assertTrue(snapshot["categories"][0]["books"][0]["word_count_observed_at"])

    def test_cross_rank_book_details_are_fetched_once(self):
        rows = [{"bookId": "42", "author": "A", "intro": "I"} for _ in range(2)]
        with patch.object(self.scraper, "_fetch_detail", return_value=("12万字", "A", "I", False)) as fetch:
            self.scraper._fetch_details(None, rows, None, None, {}, set())
        fetch.assert_called_once()
        self.assertEqual([r["wordCount"] for r in rows], ["12万字", "12万字"])

    def test_fresh_cross_run_cache_avoids_requests_and_keeps_observation(self):
        state = self.attach_state()
        observed = observed_now()
        state.put("42", {"wordCount": "12万字", "author": "A", "intro": "I", "_word_count_observed_at": observed})
        rows = [{"bookId": "42"}]
        with patch.object(self.scraper, "_fetch_detail") as fetch:
            self.scraper._fetch_details(None, rows, None, None, {}, set())
        fetch.assert_not_called()
        self.assertEqual(rows[0]["_word_count_observed_at"], observed)
        self.assertEqual(rows[0]["_word_count_source"], "cache")
        self.assertEqual(self.scraper._stats["cache_hits"], 1)

    def test_expired_cache_is_not_mislabeled_when_budget_stops_refresh(self):
        state = self.attach_state()
        observed = (datetime.now(CHINA_TZ) - timedelta(hours=25)).isoformat()
        state.put("42", {"wordCount": "12万字", "author": "A", "intro": "I", "_word_count_observed_at": observed})
        rows = [{"bookId": "42"}]
        with patch.object(self.scraper, "_fetch_detail", side_effect=qidian.DetailBudgetReached("budget")) as fetch:
            self.scraper._fetch_details(None, rows, None, None, {}, set())
        fetch.assert_called_once()
        self.assertEqual(rows[0]["_word_count_observed_at"], observed)
        self.assertEqual(rows[0]["_word_count_source"], "cache")

    def test_missing_details_are_prioritized_over_expired_values(self):
        rows = [
            {"bookId": "1", "wordCount": "1万字", "author": "A", "intro": "I", "_word_count_observed_at": "2020-01-01"},
            {"bookId": "2", "author": "A", "intro": "I"},
        ]
        with patch.object(self.scraper, "_fetch_detail", return_value=("2万字", "A", "I", False)) as fetch:
            self.scraper._fetch_details(None, rows, None, None, {}, set())
        self.assertEqual([c.args[2] for c in fetch.call_args_list], ["2", "1"])

    def test_detail_budget_counts_navigations_and_leaves_pending_values_empty(self):
        self.scraper.max_detail_requests = 1
        page = Mock()
        page.goto.return_value = SimpleNamespace(status=200)
        page.content.return_value = '<div class="count"><em>10万字</em></div><div class="book-meta"><span class="author">A</span></div><div class="book-info"><p class="intro">I</p></div>'
        rows = [{"bookId": "1"}, {"bookId": "2"}]
        self.scraper._fetch_details(page, rows, None, None, {}, set())
        self.assertEqual(page.goto.call_count, 1)
        self.assertEqual(self.scraper._stats["detail_requests"], 1)
        self.assertNotIn("wordCount", rows[1])

    def test_captcha_cooldown_survives_new_scraper_and_skips_browser(self):
        state = self.attach_state()
        rows = [{"bookId": str(i)} for i in range(3)]
        with patch.object(self.scraper, "_fetch_detail", return_value=("", "", "", True)):
            with self.assertRaises(qidian.IpBlockedError):
                self.scraper._fetch_details(None, rows, None, None, {}, set())
        self.assertGreater(state.remaining_cooldown()[0], 3500)
        other = qidian.QidianScraper()
        with patch.object(other, "_sync_playwright") as factory, self.assertRaises(SystemExit) as result:
            other.run()
        self.assertEqual(result.exception.code, 75)
        factory.assert_not_called()

    def test_http_retry_after_and_network_disconnect_stop_the_platform(self):
        state = self.attach_state()
        page = Mock()
        page.goto.return_value = SimpleNamespace(status=429, headers={"retry-after": "7200"})
        with self.assertRaises(qidian.IpBlockedError):
            self.scraper._navigate(page, "https://example.invalid/")
        self.assertGreater(state.remaining_cooldown()[0], 7100)
        page.goto.side_effect = RuntimeError("net::ERR_INTERNET_DISCONNECTED")
        with self.assertRaises(qidian.NetworkUnavailable):
            self.scraper._navigate(page, "https://example.invalid/")
        self.assertGreater(state.remaining_cooldown()[0], 7100)  # Never shorten an active cooldown.

    def test_denied_network_stops_remaining_categories_and_preserves_progress(self):
        ranks = [{'slug': f'qidian_fixture{i}', 'name': f'fixture{i}', 'url': 'https://example.invalid/'}
                 for i in range(2)]
        browser, page = Mock(), Mock()
        page.goto.side_effect = [SimpleNamespace(status=200), Exception('Page.goto: net::ERR_NETWORK_ACCESS_DENIED')]
        rows = [{'bookId': '1', 'bookName': 'fixture', 'author': 'A', 'intro': 'I'}]
        with patch.object(qidian, 'iter_platform_ranks', return_value=ranks), \
             patch.object(self.scraper, '_chan_ids', return_value={'first': 1, 'second': 2, 'third': 3}), \
             patch.object(self.scraper, '_sync_playwright', return_value=lambda: nullcontext(object())), \
             patch.object(self.scraper, 'launch_browser', return_value=(browser, None, page)), \
             patch.object(qidian, 'PER_CATE', 1), patch.object(qidian, 'parse_rank', return_value=rows), \
             patch.object(qidian, '_is_captcha_page', return_value=False), \
             patch.object(self.scraper, '_fetch_detail') as detail, self.assertRaises(SystemExit) as result:
            self.scraper.run()
        self.assertEqual(result.exception.code, 75)
        self.assertEqual(page.goto.call_count, 2)
        detail.assert_not_called()
        browser.close.assert_called_once()
        snapshots = list((self.root / 'data').glob('qidian_*_ranks_*.json'))
        self.assertEqual(len(snapshots), 1)
        snapshot = json.loads(snapshots[0].read_text(encoding='utf-8'))
        self.assertEqual([c['name'] for c in snapshot['categories'] if c['books']], ['first'])
        self.assertEqual(snapshot['quality']['completed_categories'], 1)
        self.assertEqual(snapshot['quality']['rank_status'], 'partial')
        state_file = next((self.root / 'data').glob('task_state_*.json'))
        saved = json.loads(state_file.read_text(encoding='utf-8'))
        self.assertEqual(saved['completed'], ['chan_first'])
        status = json.loads((self.root / '.runtime/qidian_status.json').read_text(encoding='utf-8'))
        self.assertIn('ERR_NETWORK_ACCESS_DENIED', status['stop_reason'])
        self.assertEqual(status['stats']['rank_requests'], 2)
        self.assertGreater(status['cooldown_seconds_remaining'], 290)

    def test_denied_network_is_not_retried_for_details(self):
        self.attach_state()
        page = Mock()
        page.goto.side_effect = Exception('Page.goto: net::ERR_NETWORK_ACCESS_DENIED')
        rows = [{'bookId': str(i)} for i in range(3)]
        with self.assertRaises(qidian.NetworkUnavailable):
            self.scraper._fetch_details(page, rows, None, None, {}, set())
        page.goto.assert_called_once()
        self.assertTrue(all('wordCount' not in row for row in rows))

    def test_page_timeout_still_uses_bounded_retries(self):
        state = self.attach_state()
        page = Mock()
        page.goto.side_effect = TimeoutError('Page.goto: Timeout 60000ms exceeded')
        self.assertEqual(self.scraper._scrape_rank_category(page, 'https://example.invalid/', 1, 'first'), [])
        self.assertEqual(page.goto.call_count, 3)
        self.assertEqual(state.remaining_cooldown()[0], 0)


class FanqieNetworkTests(unittest.TestCase):
    def test_network_failure_at_entry_stops_all_ranks_without_writing_empty_data(self):
        import scrape_fanqie_ranks as fanqie

        for error in ('ERR_NETWORK_ACCESS_DENIED', 'ERR_INTERNET_DISCONNECTED'):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                data = root / 'data'
                data.mkdir()
                saved = data / 'existing.json'
                saved.write_bytes(b'{"fixture": true}')
                browser, page = Mock(), Mock()
                page.goto.side_effect = Exception(f'Page.goto: net::{error}')
                with patch.object(base, 'OUTPUT_DIR', str(data)), patch.object(fanqie, 'OUTPUT_DIR', str(data)), \
                     patch.object(base.BaseScraper, '_sync_playwright', return_value=lambda: nullcontext(object())), \
                     patch.object(base.BaseScraper, 'launch_browser', return_value=(browser, None, page)), \
                     patch.object(fanqie, '_warn_char_sequence_gaps'), patch.object(fanqie.time, 'sleep') as sleep:
                    self.assertFalse(fanqie.run_scraper.__wrapped__())
                page.goto.assert_called_once()
                sleep.assert_not_called()
                browser.close.assert_called_once()
                self.assertEqual({p.name: p.read_bytes() for p in data.iterdir()}, {'existing.json': b'{"fixture": true}'})

    def test_denied_category_switch_keeps_completed_category_and_stops(self):
        import scrape_fanqie_ranks as fanqie

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rank = fanqie.RANK_CONFIGS[0]
            date = datetime.now().strftime('%Y%m%d')
            snapshot = root / f"{rank['prefix']}_ranks_{date}.json"
            state = root / f"task_state_{rank['prefix']}_{date}.json"
            snapshot.write_text(json.dumps({'categories': [{'name': 'first', 'books': [{'title': 'fixture'}]}]}))
            state.write_text(json.dumps({'completed': ['first']}))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            page = Mock()
            page.evaluate.return_value = [{'name': name, 'href': f'/rank/{name}'} for name in ('first', 'second', 'third')]
            page.locator.return_value.click.side_effect = Exception('net::ERR_NETWORK_ACCESS_DENIED')
            with patch.object(fanqie, 'OUTPUT_DIR', str(root)), patch.object(fanqie, '_is_blocked_page', return_value=False), \
                 patch.object(fanqie, '_warn_char_sequence_gaps'), patch.object(fanqie.time, 'sleep') as sleep:
                with self.assertRaises(qidian.NetworkUnavailable):
                    fanqie.scrape_rank_type(page, rank)
            page.locator.return_value.click.assert_called_once()
            sleep.assert_not_called()
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)


class BuildQualityTests(unittest.TestCase):
    def test_metric_parser_excludes_nonfinite_negative_and_partial_values(self):
        for value in ('NaN', 'Infinity', '1e309', '1e308万', '-2万', '12万unknown', True, [12]):
            with self.subTest(value=value):
                self.assertEqual(parse_metric(value), 0)
        for value, expected in [('10.5万字', 105000), (' 1.2 G ', 1200000000), ('1,234', 1234), ('2e3', 2000)]:
            with self.subTest(value=value):
                self.assertEqual(parse_metric(value), expected)

    def test_metric_median_averages_middle_pair_and_preserves_units(self):
        from scripts import build_latest as build
        for metric_type, values, expected in [('views', ['10万', '20万'], '15.0万'),
                                               ('word_count', ['10万字', '20万字'], '15.0万字'),
                                               ('score', ['1G', '3G'], '2.0G')]:
            data = {'categories': [{'name': 'X', 'books': [
                {'title': str(i), 'reads': value, 'metric_type': metric_type} for i, value in enumerate(values)
            ]}]}
            distribution = build.build_competitive_analysis(data)['categories']['X']['reads_distribution']
            with self.subTest(metric_type=metric_type):
                self.assertEqual(distribution['median'], expected)
                self.assertEqual(distribution['top10_avg'], expected)

    def test_nonfinite_metrics_do_not_enter_competitive_statistics(self):
        from scripts import build_latest as build
        data = {'categories': [{'name': 'X', 'books': [
            {'title': 'bad', 'reads': 'Infinity', 'metric_type': 'word_count'},
            {'title': 'good', 'reads': '10万字', 'metric_type': 'word_count'},
        ]}]}
        distribution = build.build_competitive_analysis(data)['categories']['X']['reads_distribution']
        self.assertEqual(distribution['sample_size'], 1)
        self.assertEqual(distribution['top10_avg'], '10.0万字')

    def test_api_cleanup_preserves_rank_with_overlapping_slug(self):
        from scripts import build_latest as build
        with tempfile.TemporaryDirectory() as directory:
            output = {"date": "2026-09-08", "categories": [{"name": "保留", "books": []}]}
            build.build_latest_api(output, directory, "fixture_rank_new")
            api = Path(directory) / "api/latest"
            before = {p.name: p.read_bytes() for p in api.glob("fixture_rank_new_*.json")}
            build.build_latest_api(output, directory, "fixture_rank")
            after = {p.name: p.read_bytes() for p in api.glob("fixture_rank_new_*.json")}
            self.assertEqual(after, before)

    def test_competitive_analysis_records_zero_valid_metric_samples(self):
        from scripts import build_latest as build
        data = {"platform": "qidian", "categories": [{"name": "X", "books": [
            {"title": "fixture", "reads": "", "metric_type": "word_count"},
        ]}]}
        result = build.build_competitive_analysis(data)["categories"]["X"]
        self.assertEqual(result["reads_distribution"]["sample_size"], 0)
        self.assertEqual(result["reads_distribution"]["ranked_books"], 1)
        self.assertNotIn("top10_avg", result["reads_distribution"])
        self.assertEqual(result["top10_books"][0]["metric_type"], "word_count")

    def test_invalid_snapshot_shapes_and_explicit_partial_rank_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture_ranks_20260908.json"
            for payload in ([], None, {"categories": [None]}, {"categories": [{"name": "X", "books": None}]},
                            {"categories": [{"name": "X", "books": [{}]}], "quality": {"rank_status": "partial"}}):
                path.write_text(json.dumps(payload), encoding="utf-8")
                self.assertFalse(snapshot_complete(path))

    def test_missing_metrics_are_reported_without_fabricating_observation_dates(self):
        quality = snapshot_quality({"platform": "qidian", "categories": [{"name": "X", "books": [
            {"reads": "10万字", "author": "A", "intro": "I"},
            {"reads": "", "author": "A", "intro": "I"},
        ]}]})
        self.assertEqual(quality["metrics_missing"], 1)
        self.assertEqual(quality["metric_coverage"], 0.5)
        self.assertEqual(quality["metrics_observed_unknown"], 1)

    def test_quality_survives_api_build_and_contended_index_is_not_overwritten(self):
        from scripts import build_latest as build
        with tempfile.TemporaryDirectory() as directory:
            output = {"date": "2026-09-08", "platform": "qidian", "rank_type": "fixture",
                      "quality": {"rank_status": "complete"}, "categories": [{"name": "X", "books": [
                          {"title": "fixture", "reads": "", "author": "A", "intro": "I"}
                      ]}]}
            build.build_latest_api(output, directory, "qidian_fixture")
            api = Path(directory) / "api"
            result = json.loads((api / "latest/qidian_fixture_all.json").read_text(encoding="utf-8"))
            self.assertEqual(result["quality"]["metrics_missing"], 1)
            before = (api / "latest.json").read_bytes()
            with patch.object(build, "exclusive_lock", side_effect=LockUnavailable("busy")):
                with self.assertRaises(LockUnavailable):
                    build._update_main_index(str(api), {"prefix": "another"})
            self.assertEqual((api / "latest.json").read_bytes(), before)

    def test_rebuild_removes_retired_category_api_only_after_success(self):
        from scripts import build_latest as build
        with tempfile.TemporaryDirectory() as directory:
            output = {"date": "2026-09-08", "platform": "fixture", "categories": [
                {"name": "first", "books": []}, {"name": "retired", "books": []},
            ]}
            build.build_latest_api(output, directory, "fixture_rank")
            api = Path(directory) / "api/latest"
            self.assertTrue((api / "fixture_rank_retired.json").exists())
            other = api / "other_rank_all.json"
            other.write_text("{}")
            output["categories"].pop()
            build.build_latest_api(output, directory, "fixture_rank")
            self.assertFalse((api / "fixture_rank_retired.json").exists())
            self.assertTrue((api / "fixture_rank_first.json").exists())
            self.assertTrue(other.exists())

    def test_offline_build_produces_consistent_quality_and_valid_metric_sample_count(self):
        from scripts import build_latest as build
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            trends = data / "trends"
            trends.mkdir(parents=True)
            snapshot = {"date": "2026-09-08", "platform": "fixture", "categories": [{"name": "X", "books": [
                {"title": "one", "url": "https://example.invalid/1", "reads": "10万字", "metric_type": "word_count", "author": "A", "intro": "I"},
                {"title": "two", "url": "https://example.invalid/2", "reads": "", "metric_type": "word_count", "author": "A", "intro": "I"},
            ]}]}
            (data / "fixture_rank_ranks_20260908.json").write_text(json.dumps(snapshot), encoding="utf-8")
            config = {"fixture_rank": {"prefix": "fixture_rank", "name": "fixture", "platform": "fixture"}}
            ok = build.process_one_rank_type("fixture_rank", config, SimpleNamespace(date="", force=False),
                                             str(root), str(data), str(trends), shared_ai_client=None)
            self.assertTrue(ok)
            latest = json.loads((data / "latest_fixture_rank_ranks.json").read_text(encoding="utf-8"))
            api = json.loads((root / "api/latest/fixture_rank_all.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["quality"], api["quality"])
            self.assertEqual(latest["quality"]["metrics_missing"], 1)
            competitive = json.loads((data / "author/competitive_analysis_fixture_rank.json").read_text(encoding="utf-8"))
            distribution = competitive["categories"]["X"]["reads_distribution"]
            self.assertEqual(distribution["sample_size"], 1)
            self.assertEqual(distribution["ranked_books"], 2)


if __name__ == "__main__":
    unittest.main()
