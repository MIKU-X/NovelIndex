"""Build an isolated static release. No scraping, AI calls, deployment or source data writes.

--demo generates deterministic fictional snapshots and runs the real offline builder.
--source ROOT copies only public, index-referenced artifacts from an existing build.
Each successful build creates a new directory. current.json is replaced only after validation.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.loader import iter_ranks
from scripts.publication_safety import local_secrets, require_safe_files
from scripts.runtime_state import LockUnavailable, exclusive_lock, publish_directory

ASSETS = (
    'index.html', 'author.html', 'trend.html', 'css/style.css', 'css/author.css',
    'js/config.js', 'js/utils.js', 'js/app.js', 'js/trend.js', 'js/author.js',
    'js/export.js', 'js/nav.js',
)
ANALYSES = ('theme_trends', 'competitive_analysis', 'reader_profile', 'creation_suggestions')
DEMO_NOTICE = '合成演示数据，不代表真实榜单或市场表现。'


def read_json(path):
    def invalid(value):
        raise ValueError(f'Invalid JSON constant: {value}')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=invalid)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def safe_path(root, relative):
    """No absolute paths, traversal, Windows ADS, symlinks or junctions in inputs."""
    relative = str(relative)
    if '\\' in relative or ':' in relative or '\x00' in relative:
        raise ValueError(f'Unsafe release path: {relative!r}')
    parts = relative.split('/')
    if any(not p or p in ('.', '..') or p.endswith((' ', '.')) or p.startswith('.') for p in parts):
        raise ValueError(f'Unsafe release path: {relative!r}')
    candidate = root.joinpath(*parts)
    if candidate.resolve() != candidate or not candidate.is_file():
        raise ValueError(f'Missing or linked release file: {relative}')
    candidate.resolve().relative_to(root)
    return candidate


def copy_public(source, site, relative):
    path = safe_path(source, relative)
    target = site / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)


def api_path(url):
    parsed = urlsplit(url)
    path = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError(f'Non-local API URL: {url}')
    if not re.fullmatch(r'api/latest/[^/]+\.json', path):
        raise ValueError(f'Invalid API URL: {url}')
    return path


def public_data_files(source):
    """Follow the indexes used by the UI; never glob/copy the private data tree."""
    files = {'api/latest.json'}
    ranks = read_json(source / 'api/latest.json')['rank_types']
    expected = {r['slug'] for r in iter_ranks()}
    actual = [r.get('slug', r.get('prefix')) for r in ranks]
    if set(actual) != expected or len(actual) != len(expected):
        raise ValueError('API index must contain each configured rank exactly once.')
    for rank in ranks:
        slug = rank.get('slug', rank.get('prefix'))
        latest_path = f'data/latest_{slug}_ranks.json'
        latest = read_json(safe_path(source, latest_path))
        if not latest.get('categories') or latest.get('date') != rank.get('date'):
            raise ValueError(f'Latest/API date mismatch or empty rank: {slug}')
        # This checks release consistency, not historical scraper completeness.
        date_index = f'data/dates_{slug}.json'
        dates = read_json(safe_path(source, date_index))['dates']
        if latest['date'] not in dates or len(set(dates)) != len(dates):
            raise ValueError(f'Invalid date index: {slug}')
        files.update((latest_path, date_index, f'api/latest/{slug}_index.json',
                      f'api/latest/{slug}_all.json', f'data/market_summary_{slug}.json'))
        per_rank = read_json(safe_path(source, f'api/latest/{slug}_index.json'))
        if per_rank.get('date') != latest['date'] or per_rank.get('types') != rank.get('types'):
            raise ValueError(f'API indexes disagree: {slug}')
        for entry in rank['types']:
            rel = api_path(entry['url'])
            payload = read_json(safe_path(source, rel))
            if payload.get('date') != latest['date'] or not payload.get('categories'):
                raise ValueError(f'Invalid API payload: {rel}')
            files.add(rel)
        for day in dates:
            if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                raise ValueError(f'Invalid date for {slug}')
            date.fromisoformat(day)
            rel = f'data/{slug}_ranks_{day.replace("-", "")}.json'
            if read_json(safe_path(source, rel)).get('date') != day:
                raise ValueError(f'Snapshot date mismatch: {rel}')
            files.add(rel)
            trend = f'data/trends/{slug}_{day}.json'
            if (source / trend).exists():
                files.add(trend)
        for kind in ANALYSES:
            rel = f'data/author/{kind}_{slug}.json'
            if kind != 'creation_suggestions' or (source / rel).exists():
                files.add(rel)
    for relative in files:
        read_json(safe_path(source, relative))
    return sorted(files)


def generate_demo(site):
    # The optional AI client is always None. No need to install scraping libraries.
    from scripts import build_latest as build

    seed = read_json(ROOT / 'examples/demo_seed.json')
    ranks = list(iter_ranks())
    rank_types = {r['slug']: {'prefix': r['slug'], 'name': r['name'], 'platform': r['platform'],
                             'platform_name': r['platform_name']} for r in ranks}
    first = date.fromisoformat(seed['start_date'])
    for offset in range(seed['days']):
        day = (first + timedelta(days=offset)).isoformat()
        for rank in ranks:
            slug, platform = rank['slug'], rank['platform']
            # Meet the normal snapshot gates without weakening production checks.
            cats, count = {'fanqie': (18, 30), 'qidian': (14, 30), 'jjwxc': (24, 20)}[platform]
            if slug == 'qidian_sanjiang':
                cats, count = 1, 15
            categories = []
            for cat in range(cats):
                books = []
                for place in range(count):
                    number = (place + offset) % count
                    key = f'{slug}-{cat}-{number}'
                    metric = 100000 + (count - place) * 12500 + offset * 5000
                    book = {'title': f'演示·{seed["title_roots"][number % 6]} {cat+1}-{number+1}',
                            'author': seed['authors'][number % 3], 'rank': place + 1,
                            'intro': f'这是合成的{seed["themes"][number % 6]}题材简介，仅用于展示功能。',
                            'cover': '', 'url': f'https://example.invalid/demo/{key}',
                            'tags': [seed['themes'][number % 6]], 'demo': True,
                            'metric_type': {'fanqie': 'views', 'qidian': 'word_count', 'jjwxc': 'score'}[platform],
                            'reads': str(metric), 'status': '连载中'}
                    if platform == 'qidian':
                        book.update(reads=f'{metric / 10000:.2f}万字', word_count=metric,
                                    word_count_observed_at=day + 'T08:00:00+08:00', word_count_source='synthetic')
                    elif platform == 'jjwxc':
                        book['score'] = metric
                    books.append(book)
                categories.append({'name': seed['categories'][cat], 'books': books})
            payload = {'demo': True, 'data_notice': DEMO_NOTICE, 'date': day, 'platform': platform,
                       'platform_name': rank['platform_name'], 'rank_type': rank['name'],
                       'collected_at': day + 'T08:00:00+08:00', 'quality': {'rank_status': 'complete'},
                       'categories': categories}
            write_json(site / f'data/{slug}_ranks_{day.replace("-", "")}.json', payload)
            ok = build.process_one_rank_type(slug, rank_types, SimpleNamespace(date=day, force=False),
                                             str(site), str(site / 'data'), str(site / 'data/trends'),
                                             shared_ai_client=None)
            if not ok:
                raise ValueError(f'Demo build failed: {slug} {day}')
    for directory in ('data', 'api'):
        for path in (site / directory).rglob('*.json'):
            payload = read_json(path)
            payload.update(demo=True, data_notice=DEMO_NOTICE)
            write_json(path, payload)


def build_release(output, demo=False, source=None):
    output = Path(output).resolve()
    source = Path(source or ROOT).resolve()
    output.mkdir(parents=True, exist_ok=True)
    releases = output / 'releases'
    releases.mkdir(exist_ok=True)
    if releases.resolve() != releases:
        raise ValueError('Release directory must not be a link.')
    with tempfile.TemporaryDirectory(prefix='.build-', dir=output) as temporary:
        staging = Path(temporary).resolve()
        site = staging / 'site'
        site.mkdir()
        for relative in ASSETS:
            copy_public(ROOT, site, relative)
        shutil.copyfile(ROOT / 'LICENSE', site / 'LICENSE.txt')
        shutil.copyfile(ROOT / 'THIRD_PARTY_NOTICES.md', site / 'NOTICE.txt')
        if demo:
            demo_work = staging / 'demo-work'
            demo_work.mkdir()
            with (staging / 'build.log').open('w', encoding='utf-8') as log, contextlib.redirect_stdout(log):
                generate_demo(demo_work)
            # The real builder also creates locks/state. Copy only public outputs,
            # exactly as for a real-data source; never publish its working root.
            for relative in public_data_files(demo_work):
                copy_public(demo_work, site, relative)
        else:
            # Workspaces can be rebuilt or cleaned concurrently; immutable site
            # releases have no writers and need no runtime files created in them.
            guard = (contextlib.nullcontext() if (source / 'site-info.json').is_file()
                     else exclusive_lock(source / '.runtime/build.lock'))
            with guard:
                for relative in public_data_files(source):
                    copy_public(source, site, relative)
            # Do not let a demo source be repackaged as real data.
            if read_json(site / 'api/latest.json').get('demo'):
                demo = True
        public_data_files(site)
        if demo:
            for name in ('index.html', 'author.html', 'trend.html'):
                path = site / name
                html = path.read_text(encoding='utf-8')
                html, replaced = re.subn(
                    r'<body\b([^>]*)>',
                    lambda match: '<body' + match[1] + ' data-demo="true">\n'
                    '<aside class="demo-notice" role="note">演示模式 · ' + DEMO_NOTICE + '</aside>',
                    html, count=1, flags=re.IGNORECASE,
                )
                if replaced != 1:
                    raise ValueError(f'Missing body element in {name}')
                path.write_text(html, encoding='utf-8')
        write_json(site / 'site-info.json', {'demo': demo, 'data_notice': DEMO_NOTICE if demo else '',
                                           'schema_version': 1})
        require_safe_files([(p.relative_to(site).as_posix(), p) for p in site.rglob('*') if p.is_file()],
                           local_secrets(ROOT, source), source=False)
        files = {p.relative_to(site).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(site.rglob('*')) if p.is_file()}
        manifest = {'schema_version': 1, 'demo': demo, 'files': files}
        write_json(site / 'release-manifest.json', manifest)
        release_id = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
        target = releases / release_id
        # Never replace a directory in use. A failed build leaves current.json intact.
        # Copy into a fresh directory so it inherits the release parent's ACL.
        # Windows tempfile directories have a private DACL; renaming one directly
        # makes the supposedly public release unreadable by the web-server user.
        pending = releases / f'.pending-{release_id}'
        shutil.copytree(site, pending)
        publish_directory(pending, target)
        pointer = output / f'.current-{release_id}.tmp'
        try:
            write_json(pointer, {'path': f'releases/{release_id}', 'demo': demo})
            os.replace(pointer, output / 'current.json')
        finally:
            pointer.unlink(missing_ok=True)
        return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--demo', action='store_true')
    mode.add_argument('--source', type=Path, help='existing built project root')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    try:
        target = build_release(args.output, args.demo, args.source)
    except (OSError, ValueError, KeyError, TypeError, LockUnavailable) as exc:
        parser.exit(1, f'Release failed: {exc}\n')
    print(f'Release ready: {target}')
    print(f'Preview: python scripts/serve_preview.py 8000 --root "{target}"')


if __name__ == '__main__':
    main()
