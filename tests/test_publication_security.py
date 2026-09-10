"""Synthetic credentials only: release gates, index/history, hooks and error redaction."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ai_client import AIClient, Endpoint, NoAvailableEndpointError, valid_endpoint_url
from scripts.build_source_release import build_source_release
from scripts.check_publication import scan_git_blobs
from scripts.publication_safety import content_findings, local_secrets, private_path
from scripts.runtime_state import publish_directory
from scripts.secret_redaction import mask_secrets

ROOT = Path(__file__).resolve().parents[1]


class PublicationSecurityTests(unittest.TestCase):
    def test_publication_rename_retries_locks_without_overwriting_a_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            pending, target = root / '.pending-fixture', root / 'fixture'
            pending.mkdir()
            original = Path.rename
            attempts = []

            def rename(path, destination):
                attempts.append(destination)
                if len(attempts) == 1:
                    raise PermissionError('Transient fixture lock')
                return original(path, destination)

            with patch.object(Path, 'rename', rename), patch('scripts.runtime_state.time.sleep'):
                publish_directory(pending, target)
            self.assertTrue(target.is_dir())
            self.assertEqual(len(attempts), 2)
            pending.mkdir()
            with self.assertRaises(FileExistsError):
                publish_directory(pending, target)
            self.assertTrue(pending.is_dir())

    def test_configured_custom_keys_and_authentication_payloads_are_redacted(self):
        token = 'vendor_' + 'synthetic9' * 4
        secret = 'pass with/' + 'synthetic9' * 3
        header_value = 'abcdefg' + 'hijklmnop'
        json_value = 'a-different-' + 'synthetic-value'
        with patch.dict(os.environ, {'API_2_KEY': token}):
            from urllib.parse import quote
            text = f'{token} {quote(secret, safe="")} Authorization: Bearer {header_value}\n'
            text += f'https://user:pass@example.invalid/?Access_Token={header_value}\nCookie: session=private'
            text += '\n' + json.dumps({'api_key': json_value})
            redacted = mask_secrets(text, secrets=[secret])
            for value in (token, quote(secret, safe=''), 'abcdefghijklmnop', 'session=private',
                          'a-different-synthetic-value', 'user:pass'):
                self.assertNotIn(value, redacted)

    def test_endpoint_repr_and_transport_policy(self):
        ep = Endpoint('1', 'https://example.invalid', 'fixture-secret', 'fixture-model')
        self.assertNotIn(ep.api_key, repr(ep))
        self.assertNotIn(ep.base_url, repr(ep))
        for url in ('https://example.invalid/v1', 'http://localhost:1234/v1', 'http://127.0.0.1/v1', 'http://[::1]/v1'):
            self.assertTrue(valid_endpoint_url(url), url)
        for url in ('http://example.invalid', 'https://user:pass@example.invalid',
                    'https://example.invalid/?key=fixture', 'https://example.invalid/#fragment',
                    'http://127.0.0.1.example.invalid', 'file:///tmp/api'):
            self.assertFalse(valid_endpoint_url(url), url)

    def test_ai_failure_traceback_does_not_reveal_sdk_cause_or_custom_key(self):
        token = 'custom_' + 'synthetic9' * 4

        def fail(**kwargs):
            raise RuntimeError(f'401 rejected credential {token}')

        fake = types.ModuleType('openai')
        fake.OpenAI = lambda **kwargs: types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=fail)))
        stream = io.StringIO()
        with patch.dict(sys.modules, {'openai': fake}), contextlib.redirect_stdout(stream):
            client = AIClient([Endpoint('1', 'https://example.invalid', token, 'fixture')])
            try:
                client.chat_create([], max_tokens=5, temperature=0)
            except NoAvailableEndpointError:
                diagnostic = traceback.format_exc()
            else:
                self.fail('Expected failure')
        self.assertNotIn(token, stream.getvalue() + diagnostic)
        self.assertNotIn('RuntimeError: 401', diagnostic)
        self.assertIn('[REDACTED]', diagnostic)

    def test_ai_non_failover_error_is_also_sanitized(self):
        token = 'custom_' + 'synthetic9' * 4

        def fail(**kwargs):
            raise RuntimeError(f'provider failure {token}')

        fake = types.ModuleType('openai')
        fake.OpenAI = lambda **kwargs: types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=fail)))
        with patch.dict(sys.modules, {'openai': fake}):
            client = AIClient([Endpoint('1', 'https://example.invalid', token, 'fixture')])
            try:
                client.chat_create([], max_tokens=5, temperature=0)
            except RuntimeError:
                diagnostic = traceback.format_exc()
            else:
                self.fail('Expected failure')
        self.assertNotIn(token, diagnostic)

    def test_scanner_detects_custom_and_provider_keys_without_echo(self):
        token = 'custom_' + 'synthetic9' * 4
        provider = 'sk-' + 'synthetic9' * 4
        result = content_findings('README.md', f'example\n{token}\n{provider}'.encode(), [token])
        self.assertEqual({f['rule'] for f in result}, {'local-config-secret', 'provider-token'})
        self.assertNotIn(token, json.dumps(result))
        self.assertNotIn(provider, json.dumps(result))
        self.assertEqual(content_findings('.env.example', b'API_KEY=your-api-key-here'), [])

    def test_private_paths_include_nested_credentials_and_diagnostic_files(self):
        for name in ('config/credentials.json', 'fixtures/cookies.json', '.env.old', 'debug/capture.har',
                     'certs/client.pem', 'database.sqlite3', 'settings.json.bak', '.claude/settings.local.json'):
            self.assertTrue(private_path(name), name)
        self.assertFalse(private_path('.env.example'))
        self.assertFalse(private_path('api/latest.json', source=False))

    def test_source_build_rejects_secret_inside_allowed_readme_before_output(self):
        token = 'custom_' + 'synthetic9' * 4
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'config').mkdir()
            (root / 'config/source_release.json').write_text(json.dumps({'files': ['README.md']}))
            (root / '.env').write_text(f'API_KEY="{token}" # local only', encoding='utf-8')
            (root / 'README.md').write_text(f'Example accidentally includes {token}', encoding='utf-8')
            self.assertIn(token, local_secrets(root))
            with self.assertRaises(ValueError) as failure:
                build_source_release(root / 'dist', root)
            self.assertNotIn(token, str(failure.exception))
            self.assertFalse((root / 'dist').exists())

    @unittest.skipUnless(shutil.which('git'), 'Git not installed')
    def test_index_and_history_scan_detect_staged_and_deleted_secrets(self):
        token = 'sk-' + 'synthetic9' * 4
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()

            def git(*args):
                return subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c',
                                       'user.email=fixture@example.invalid', *args], capture_output=True, check=True)

            git('init', '--quiet')
            path = root / 'README.md'
            path.write_text(token)
            git('add', 'README.md')
            path.write_text('Working copy looks safe; index still holds the old value.')
            found, _ = scan_git_blobs(root, set())
            self.assertTrue(any(f['rule'] == 'provider-token' for f in found))
            git('commit', '--quiet', '-m', 'Synthetic scanner fixture')
            git('add', 'README.md')
            git('commit', '--quiet', '-m', 'Remove synthetic fixture')
            found, _ = scan_git_blobs(root, set(), history=True)
            self.assertTrue(any(f['rule'] == 'provider-token' for f in found))
            self.assertNotIn(token, json.dumps(found))
            # Exercise Git's actual pre-push boundary against a temporary LOCAL
            # bare repository; no network, account or existing repository used.
            for name in ('scripts/check_publication.py', 'scripts/publication_safety.py', '.githooks/pre-push'):
                dest = root / name
                dest.parent.mkdir(exist_ok=True)
                shutil.copyfile(ROOT / name, dest)
            (root / '.githooks/pre-push').chmod(0o755)
            git('config', 'core.hooksPath', '.githooks')
            remote = root / 'remote.git'
            git('init', '--bare', '--quiet', str(remote))
            result = subprocess.run(['git', '-C', str(root), 'push', str(remote), 'HEAD:refs/heads/main'],
                                    env={**os.environ, 'NOVELINDEX_PYTHON': sys.executable}, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'provider-token', result.stdout + result.stderr)
            self.assertEqual(git('--git-dir', str(remote), 'for-each-ref').stdout, b'')

    @unittest.skipUnless(shutil.which('git'), 'Git not installed')
    def test_real_precommit_hook_blocks_synthetic_secret(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for name in ('scripts/check_publication.py', 'scripts/publication_safety.py', '.githooks/pre-commit'):
                dest = root / name
                dest.parent.mkdir(exist_ok=True)
                shutil.copyfile(ROOT / name, dest)
            (root / '.githooks/pre-commit').chmod(0o755)
            subprocess.run(['git', 'init', '--quiet', str(root)], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(root), 'config', 'core.hooksPath', '.githooks'], check=True)
            (root / 'README.md').write_text('sk-' + 'synthetic9' * 4)
            subprocess.run(['git', '-C', str(root), 'add', 'README.md'], check=True, capture_output=True)
            result = subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c',
                                     'user.email=fixture@example.invalid', 'commit', '--quiet', '-m', 'Must be blocked'],
                                    env={**os.environ, 'NOVELINDEX_PYTHON': sys.executable}, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'provider-token', result.stdout + result.stderr)
