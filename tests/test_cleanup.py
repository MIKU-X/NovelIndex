import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from scripts import cleanup_task_states as cleanup
from scripts.runtime_state import LockUnavailable, exclusive_lock


class Today(date):
    @classmethod
    def today(cls):
        return cls(2026, 1, 3)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.data = self.root / 'data'
        self.data.mkdir()
        self.slug = 'fixture_rank'
        self.days = ['2026-01-01', '2026-01-02', '2026-01-03']
        for day in self.days:
            self.write(self.raw(day), {'date': day})
        self.index = self.data / f'dates_{self.slug}.json'
        self.latest = self.data / f'latest_{self.slug}_ranks.json'
        self.write(self.index, {'dates': self.days, 'demo': True})
        self.write(self.latest, {'date': self.days[-1]})
        self.addCleanup(patch.stopall)
        patch.object(cleanup, 'date', Today).start()

    def raw(self, day):
        return self.data / f'{self.slug}_ranks_{day.replace("-", "")}.json'

    @staticmethod
    def write(path, payload):
        path.write_text(json.dumps(payload), encoding='utf-8')

    def contents(self):
        return {p.name: p.read_bytes() for p in self.data.iterdir()}

    def run_cleanup(self, keep=1, dry_run=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return cleanup.cleanup_by_keep_days(self.data, keep, dry_run)

    def test_retention_prunes_dates_and_repairs_preexisting_missing_history(self):
        self.write(self.index, {'dates': ['2000-01-01', *self.days], 'demo': True})
        deleted, _ = self.run_cleanup()
        self.assertEqual(deleted, 1)
        self.assertFalse(self.raw(self.days[0]).exists())
        self.assertEqual(json.loads(self.index.read_text()), {'dates': self.days[1:], 'demo': True})

    def test_latest_backing_snapshot_is_kept_even_when_older_than_retention(self):
        self.write(self.latest, {'date': self.days[0]})
        self.run_cleanup()
        self.assertTrue(self.raw(self.days[0]).exists())
        self.assertIn(self.days[0], json.loads(self.index.read_text())['dates'])

    def test_without_latest_or_index_old_unpublished_snapshots_can_be_removed(self):
        self.latest.unlink()
        self.index.unlink()
        self.run_cleanup()
        self.assertFalse(self.raw(self.days[0]).exists())
        self.assertTrue(self.raw(self.days[1]).exists())

    def test_dry_run_preserves_all_data_bytes(self):
        before = self.contents()
        deleted, _ = self.run_cleanup(dry_run=True)
        self.assertEqual(deleted, 0)
        self.assertEqual(self.contents(), before)

    def test_invalid_index_or_missing_latest_backing_file_aborts_before_changes(self):
        for damage in ('bad-index', 'missing-latest'):
            with self.subTest(damage=damage):
                self.write(self.index, {'dates': 'broken' if damage == 'bad-index' else self.days})
                if damage == 'missing-latest':
                    self.raw(self.days[-1]).unlink()
                before = self.contents()
                with self.assertRaises(ValueError):
                    self.run_cleanup()
                self.assertEqual(self.contents(), before)

    def test_index_write_failure_prevents_snapshot_deletion(self):
        before = self.contents()
        with patch.object(cleanup, 'write_json', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.run_cleanup()
        self.assertEqual(self.contents(), before)

    def test_failed_delete_leaves_safe_index_and_can_be_retried(self):
        with patch.object(cleanup.os, 'remove', side_effect=PermissionError('locked')), self.assertRaises(OSError):
            self.run_cleanup()
        self.assertTrue(self.raw(self.days[0]).exists())
        self.assertEqual(json.loads(self.index.read_text())['dates'], self.days[1:])
        self.run_cleanup()
        self.assertFalse(self.raw(self.days[0]).exists())

    def test_running_build_or_scraper_blocks_retention_without_changes(self):
        before = self.contents()
        for name in ('build', 'scrape-qidian'):
            with self.subTest(name=name), exclusive_lock(self.root / f'.runtime/{name}.lock'):
                with self.assertRaises(LockUnavailable):
                    self.run_cleanup()
            self.assertEqual(self.contents(), before)

    def test_invalid_retention_cannot_delete_files(self):
        before = self.contents()
        with self.assertRaises(ValueError):
            self.run_cleanup(keep=0)
        self.assertEqual(self.contents(), before)
