"""Check source, staged Git blobs, reachable history, or a release directory offline."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.publication_safety import content_findings, local_secrets, private_path, scan_files


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=False)
    if result.returncode:
        raise ValueError('Git check failed; run inside a repository with complete local history')
    return result.stdout


def scan_git_blobs(root, secrets, *, history=False):
    """Inspect object bytes, so staged content and deleted historical files are covered."""
    findings = []
    objects = {}
    if history:
        if git(root, 'rev-parse', '--is-shallow-repository').strip() == b'true':
            raise ValueError('History check requires a full clone (fetch-depth: 0)')
        for record in git(root, 'rev-list', '--objects', '--all').splitlines():
            oid, _, name = record.partition(b' ')
            objects[oid] = name.decode('utf-8', errors='replace')
        # rev-list emits each object only once; a private path could share a blob
        # with a harmless path. Audit all changed paths independently as well.
        paths = {p.decode('utf-8', errors='replace').strip('\n')
                 for p in git(root, 'log', '--all', '--format=', '--name-only', '-z').split(b'\0') if p}
        for name in sorted(paths):
            if private_path(name):
                findings.append({'path': f'history/{name}', 'line': 0, 'rule': 'private-path'})
    else:
        for record in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
            if not record:
                continue
            meta, name = record.split(b'\t', 1)
            mode, oid, stage = meta.split()
            name = name.decode('utf-8', errors='replace')
            if mode != b'100644' and mode != b'100755':
                findings.append({'path': name, 'line': 0, 'rule': 'git-link-or-conflict'})
            if stage != b'0':
                findings.append({'path': name, 'line': 0, 'rule': 'git-conflict'})
            # Multiple paths may share a blob; check every staged path separately.
            if private_path(name):
                findings.append({'path': name, 'line': 0, 'rule': 'private-path'})
            objects[oid] = name
    count = 0
    with subprocess.Popen(['git', '-C', str(root), 'cat-file', '--batch'], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            for oid, name in objects.items():
                process.stdin.write(oid + b'\n')
                process.stdin.flush()
                header = process.stdout.readline().split()
                if len(header) != 3:
                    raise ValueError('Cannot inspect Git object')
                size = int(header[2])
                data = process.stdout.read(size)
                if len(data) != size or process.stdout.read(1) != b'\n':
                    raise ValueError('Incomplete Git object')
                if history and header[1] == b'commit':
                    findings.extend(content_findings(f'history/{oid[:12].decode()}/commit-message', data, secrets))
                if header[1] != b'blob':
                    continue
                count += 1
                label = f"history/{oid[:12].decode()}/{name}" if history else f'index/{name}'
                findings.extend(content_findings(label, data, secrets))
        finally:
            process.stdin.close()
    return findings, count


def check(root, *, directory=None, staged=False, history=False):
    root = Path(root).resolve()
    secrets = local_secrets(root)
    findings, files, blobs = [], 0, 0
    if directory is not None:
        target = Path(directory).resolve()
        if not target.is_dir():
            raise ValueError('Release directory does not exist')
        entries = [(p.relative_to(target).as_posix(), p) for p in target.rglob('*') if p.is_file() or p.is_symlink()]
        # Static releases legitimately contain data/ and api/.
        findings.extend(scan_files(entries, secrets, source=not (target / 'site-info.json').is_file()))
        files += len(entries)
    elif not staged and not history:
        spec = json.loads((root / 'config/source_release.json').read_text(encoding='utf-8'))
        entries = [(e, root / e) if isinstance(e, str) else (e['path'], root / e['source']) for e in spec['files']]
        findings.extend(scan_files(entries, secrets))
        files += len(entries)
    if staged:
        found, count = scan_git_blobs(root, secrets)
        findings.extend(found)
        blobs += count
    if history:
        found, count = scan_git_blobs(root, secrets, history=True)
        findings.extend(found)
        blobs += count
    return {'files_checked': files, 'git_blobs_checked': blobs, 'findings': findings,
            'scope': 'heuristics and local credential matching; manual media/privacy review still required'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--staged', action='store_true', help='scan all index blobs, not working copies')
    parser.add_argument('--history', action='store_true', help='scan all locally reachable history; refuses shallow clones')
    args = parser.parse_args()
    try:
        report = check(args.root, directory=args.directory, staged=args.staged, history=args.history)
    except (OSError, ValueError, KeyError) as exc:
        # Do not echo arbitrary decoding errors, which can contain source bytes.
        parser.exit(2, f'Publication check could not complete ({type(exc).__name__}). Check paths, manifest and Git availability.\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report['findings'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
