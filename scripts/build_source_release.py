"""Package current source from an explicit allowlist; never include Git history or local data."""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.publication_safety import local_secrets, private_path, require_safe_files
from scripts.runtime_state import publish_directory

ALLOWED_DOTFILES = {'.env.example', '.gitignore', '.gitattributes', '.github/workflows/ci.yml', '.github/dependabot.yml',
                    '.githooks/pre-commit', '.githooks/pre-push'}
CODE_SUFFIXES = {'.py', '.ps1', '.bat', '.json', '.js', '.css', '.html', '.md', '.txt', '.toml', '.yml', '.cjs'}


def relocate_markdown(content, source, destination):
    """Rebase inline local links when release documentation changes directory."""
    def replace(match):
        url = urlsplit(match[2])
        if url.scheme or url.netloc or not url.path or url.path.startswith('/'):
            return match[0]
        target = posixpath.normpath(posixpath.join(str(PurePosixPath(source).parent), url.path))
        relative = posixpath.relpath(target, str(PurePosixPath(destination).parent))
        return match[1] + urlunsplit(('', '', relative, url.query, url.fragment)) + ')'
    return re.sub(r'(\[[^\]\n]*\]\()([^\s)]+)\)', replace, content)


def release_path(value):
    path = PurePosixPath(value)
    if private_path(value):
        raise ValueError(f'Private source release path: {value}')
    if '\\' in value or ':' in value or path.is_absolute() or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError(f'Unsafe source release path: {value}')
    if any(p.startswith('.') for p in path.parts) and value not in ALLOWED_DOTFILES:
        raise ValueError(f'Private path in source allowlist: {value}')
    if path.parts[0] in ('data', 'api', 'logs', 'dist', 'probe_out', 'Scrapling-main', '晋江爬虫'):
        raise ValueError(f'Local material in source allowlist: {value}')
    if path.name != 'LICENSE' and value not in ALLOWED_DOTFILES and path.suffix not in CODE_SUFFIXES:
        raise ValueError(f'Unexpected source file type: {value}')
    return path


def build_source_release(output, root=ROOT):
    root = Path(root).resolve()
    output = Path(output).resolve()
    spec = json.loads((root / 'config/source_release.json').read_text(encoding='utf-8'))
    entries = []
    names = set()
    for entry in spec['files']:
        name, source = (entry, entry) if isinstance(entry, str) else (entry['path'], entry['source'])
        release_path(name)
        release_path(source)
        if name.casefold() in names:
            raise ValueError(f'Duplicate output path: {name}')
        names.add(name.casefold())
        candidate = root / source
        if candidate.resolve() != candidate or not candidate.is_file():
            raise ValueError(f'Missing or linked source: {source}')
        entries.append((name, candidate))
    secrets = local_secrets(root)
    require_safe_files(entries, secrets)
    output.mkdir(parents=True, exist_ok=True)
    identifier = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    pending = output / f'.pending-{identifier}'
    package = pending / 'NovelIndex'
    package.mkdir(parents=True)
    hashes = {}
    for name, source in entries:
        destination = package / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if source.suffix == '.md' and source.relative_to(root).as_posix() != name:
            destination.write_text(relocate_markdown(source.read_text(encoding='utf-8'),
                                                     source.relative_to(root).as_posix(), name), encoding='utf-8')
        hashes[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    require_safe_files([(name, package / name) for name, _ in entries], secrets)
    manifest = {'schema_version': 1, 'git_history_included': False, 'real_data_included': False, 'files': hashes}
    (package / 'source-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = pending / 'NovelIndex-source.zip'
    with ZipFile(archive, 'x', compression=ZIP_DEFLATED) as zipped:
        for path in sorted(package.rglob('*')):
            if path.is_file():
                zipped.write(path, path.relative_to(pending).as_posix())
    with ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise ValueError('ZIP verification failed')
    (pending / 'SHA256SUMS.txt').write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  NovelIndex-source.zip\n', encoding='ascii')
    target = output / identifier
    publish_directory(pending, target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/source_releases')
    args = parser.parse_args()
    try:
        target = build_source_release(args.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f'Source release failed: {exc}\n')
    print(f'Source release ready: {target}')


if __name__ == '__main__':
    main()
