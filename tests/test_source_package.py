import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

from scripts.build_source_release import build_source_release, release_path


class SourcePackageTests(unittest.TestCase):
    def test_package_is_self_contained_and_excludes_history_and_private_data(self):
        with tempfile.TemporaryDirectory() as temp:
            release = build_source_release(temp)
            package = release / 'NovelIndex'
            manifest = json.loads((package / 'source-manifest.json').read_text(encoding='utf-8'))
            self.assertFalse(manifest['git_history_included'])
            self.assertFalse(manifest['real_data_included'])
            for name, digest in manifest['files'].items():
                self.assertEqual(hashlib.sha256((package / name).read_bytes()).hexdigest(), digest)
                self.assertNotIn('.git', Path(name).parts)
                self.assertNotIn(Path(name).parts[0], ('data', 'api', 'logs', 'probe_out', '晋江爬虫', 'Scrapling-main'))
            self.assertTrue((package / 'scrapers/qidian_html.py').is_file())
            self.assertTrue((package / 'config/source_release.json').is_file())
            for document in package.rglob('*.md'):
                for link in re.findall(r'\[[^\]\n]*\]\(([^\s)]+)\)', document.read_text(encoding='utf-8')):
                    url = urlsplit(link)
                    if url.scheme or url.netloc or not url.path:
                        continue
                    self.assertTrue((document.parent / unquote(url.path)).is_file(),
                                    f'{document.relative_to(package)} -> {link}')
            repacked = build_source_release(Path(temp) / 'repacked', root=package) / 'NovelIndex'
            self.assertEqual((repacked / 'README.md').read_bytes(), (package / 'README.md').read_bytes())
            with ZipFile(release / 'NovelIndex-source.zip') as zipped:
                self.assertIsNone(zipped.testzip())
                self.assertEqual(len(zipped.namelist()), len(manifest['files']) + 1)

    def test_manifest_cannot_whitelist_credentials_history_or_path_escape(self):
        for name in ('.git/config', '.env', 'data/real.json', 'logs/run.log', '../secret.py', 'scripts/x.py:secret',
                     'Scrapling-main/LICENSE', '晋江爬虫/test.py'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                release_path(name)
