import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build_release as release
from scripts import cleanup_task_states as cleanup
from scripts.runtime_state import LockUnavailable, exclusive_lock
from scripts.scheduled_run import run


class DistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name).resolve()
        cls.site = release.build_release(cls.root / 'demo', demo=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_demo_is_labelled_and_index_complete(self):
        self.assertFalse((self.site / '.runtime').exists())
        self.assertFalse(any(p.suffix == '.lock' for p in self.site.rglob('*')))
        index = release.read_json(self.site / 'api/latest.json')
        self.assertEqual(len(index['rank_types']), 9)
        for relative in release.public_data_files(self.site):
            data = release.read_json(self.site / relative)
            self.assertTrue(data['demo'], relative)
            categories = data.get('categories', [])
            for cat in categories if isinstance(categories, list) else []:
                if not isinstance(cat, dict) or 'books' not in cat:
                    continue
                for book in cat['books']:
                    self.assertTrue(book['title'].startswith('演示·'))
                    self.assertTrue(book['url'].startswith('https://example.invalid/demo/'))
                    self.assertEqual(book['cover'], '')
        for page in ('index.html', 'trend.html', 'author.html'):
            self.assertIn('data-demo="true"', (self.site / page).read_text(encoding='utf-8'))
        manifest = release.read_json(self.site / 'release-manifest.json')
        for relative, digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((self.site / relative).read_bytes()).hexdigest(), digest)

    def test_cleanup_keeps_site_publishable_and_respects_workspace_build_lock(self):
        source = self.root / 'cleanup-source'
        shutil.copytree(self.site, source)
        (source / 'site-info.json').unlink()  # Exercise the mutable workspace path.
        cleanup.cleanup_by_keep_days(source / 'data', 1)
        for rank in release.read_json(source / 'api/latest.json')['rank_types']:
            slug = rank['slug']
            self.assertEqual(release.read_json(source / f'data/dates_{slug}.json')['dates'], [rank['date']])
        release.public_data_files(source)
        output = self.root / 'after-cleanup'
        with exclusive_lock(source / '.runtime/build.lock'), self.assertRaises(LockUnavailable):
            release.build_release(output, source=source)
        self.assertFalse((output / 'current.json').exists())
        published = release.build_release(output, source=source)
        release.public_data_files(published)
        self.assertFalse((published / '.runtime').exists())

    def test_private_files_not_copied_and_demo_not_disguised(self):
        for relative in ('.env', 'data/task_state_test.json', 'data/private.json', 'logs/private.json'):
            path = self.site / relative
            path.parent.mkdir(exist_ok=True)
            path.write_text('DO NOT PUBLISH', encoding='utf-8')
        copied = release.build_release(self.root / 'copied', source=self.site)
        self.assertTrue(release.read_json(copied / 'site-info.json')['demo'])
        for path in copied.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'DO NOT PUBLISH', path.read_bytes())

    def test_failure_keeps_previous_release_and_pointer(self):
        output = self.root / 'failure'
        previous = release.build_release(output, source=self.site)
        pointer = (output / 'current.json').read_bytes()
        broken = self.root / 'broken'
        release.write_json(broken / 'api/latest.json', {'rank_types': []})
        with self.assertRaises(ValueError):
            release.build_release(output, source=broken)
        self.assertEqual(pointer, (output / 'current.json').read_bytes())
        self.assertTrue((previous / 'index.html').is_file())
        self.assertEqual(list(output.glob('.build-*')), [])

    def test_untrusted_paths_rejected(self):
        for path in ('../.env', '/etc/passwd', 'data/x.json::$DATA', 'data/../index.html',
                     'data\\private.json', 'data/.env', 'index.html '):
            with self.subTest(path=path), self.assertRaises(ValueError):
                release.safe_path(self.site, path)
        for url in ('https://example.com/x.json', 'api/latest/../../.env', 'api/latest/x.json?token=x'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                relative = release.api_path(url)
                release.safe_path(self.site, relative)

    def test_secret_in_public_json_blocks_release_and_preserves_current(self):
        token = 'custom_' + 'synthetic9' * 4
        output = self.root / 'credential-gate'
        release.build_release(output, source=self.site)
        before = (output / 'current.json').read_bytes()
        path = self.site / 'api/latest.json'
        original = path.read_bytes()
        try:
            data = release.read_json(path)
            data['accidental_debug_value'] = token
            release.write_json(path, data)
            with patch.dict(os.environ, {'API_KEY': token}), self.assertRaises(ValueError) as failure:
                release.build_release(output, source=self.site)
            self.assertNotIn(token, str(failure.exception))
            self.assertEqual(before, (output / 'current.json').read_bytes())
            self.assertEqual(len(list((output / 'releases').iterdir())), 1)
        finally:
            path.write_bytes(original)

    def test_symlink_cannot_be_distributed(self):
        path = self.root / 'linked.json'
        try:
            path.symlink_to(self.site / 'api/latest.json')
        except OSError:
            self.skipTest('This Windows account cannot create symlinks.')
        with self.assertRaises(ValueError):
            release.safe_path(self.root, 'linked.json')


class UnattendedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='NovelIndex 中文 & ! ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'run_all.py').write_text(
            "import pathlib,sys\nassert pathlib.Path.cwd()==pathlib.Path(__file__).resolve().parent\n"
            "assert sys.stdin.read()==''\nprint('中文 stdout');print('中文 stderr',file=sys.stderr)\n"
            "raise SystemExit(7)\n", encoding='utf-8')

    def test_nonzero_pipeline_result_and_both_streams_reach_log(self):
        self.assertEqual(run(self.root), 7)
        logs = list((self.root / 'logs/scheduled').glob('*.log'))
        self.assertEqual(len(logs), 1)
        content = logs[0].read_text(encoding='utf-8')
        for text in ('中文 stdout', '中文 stderr', 'exit_code=7'):
            self.assertIn(text, content)
        self.assertEqual(json.loads(logs[0].with_suffix('.json').read_text())['exit_code'], 7)

    @unittest.skipUnless(os.name == 'nt', 'Windows batch entry')
    def test_setup_auto_from_another_directory(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for name in ('scheduled_run.py', 'run_scheduled.ps1'):
            shutil.copyfile(release.ROOT / 'scripts' / name, scripts / name)
        shutil.copyfile(release.ROOT / 'setup.bat', self.root / 'setup.bat')
        env = os.environ.copy()
        env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env['PATH']
        command = f'"{os.environ["COMSPEC"]}" /d /s /c ""{self.root / "setup.bat"}" /auto"'
        completed = subprocess.run(command, cwd=release.ROOT, env=env, stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 7, completed.stdout + completed.stderr)
        self.assertTrue(list((self.root / 'logs/scheduled').glob('*.log')))


if __name__ == '__main__':
    unittest.main()
