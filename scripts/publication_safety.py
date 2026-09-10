"""Offline publication checks. Findings contain locations/rules, never matched values.

Heuristics plus exact matching of local credentials are a release gate, not proof
that arbitrary secrets or personal information cannot exist in a repository.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote

SECRET_NAME = re.compile(r'(?:^|_)(?:KEY|TOKEN|SECRET|PASSWORD|COOKIE)$', re.I)
PLACEHOLDERS = {'your-api-key-here', 'your-api-key', 'your-token', 'changeme', 'placeholder'}
PATTERNS = {
    'provider-token': re.compile(rb'(?<![\w-])(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{35,}|xox[baprs]-[A-Za-z0-9-]{25,})(?![\w-])'),
    'aws-access-key': re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'private-key': re.compile(rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----'),
    'credential-assignment': re.compile(rb'''(?i)["']?\b(?:api[_-]?(?:\d+[_-]?)?key|access[_-]?token|client[_-]?secret|password)["']?\s*[:=]\s*["']([A-Za-z0-9_+/=-]{16,})["']'''),
    'url-credential': re.compile(rb'''(?i)[?&](?:api[_-]?key|token|access_token|secret|password)=([A-Za-z0-9_%+/-]{16,})'''),
    'env-credential': re.compile(rb'(?mi)^\s*(?:export\s+)?[A-Z][A-Z0-9_]*(?:_KEY|_TOKEN|_SECRET|_PASSWORD)\s*=\s*([A-Za-z0-9_+/=-]{16,})\s*(?:#.*)?$'),
    'authorization-header': re.compile(rb'(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9_+/=.%-]{20,}'),
    'url-userinfo': re.compile(rb'https?://[^\s/:@]+:[^\s/@]{8,}@'),
}
PRIVATE_DIRS = {'.git', '.runtime', '.venv', 'venv', '.codex', '.agents', '.claude', '.backup',
                'logs', 'probe_out', 'scrapling-main', '晋江爬虫'}
PRIVATE_NAMES = {'cookies.json', 'cookie.json', 'storage_state.json', 'auth.json', 'credentials.json',
                 '.netrc', '.npmrc', '.pypirc', 'id_rsa', 'id_ed25519'}
PRIVATE_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.db', '.sqlite', '.sqlite3', '.log', '.har', '.rar', '.7z'}


def local_secrets(*roots):
    """Read only values needed for local comparison; never load them into os.environ."""
    values = [v for k, v in os.environ.items() if SECRET_NAME.search(k)]
    for root in roots:
        for path in Path(root).glob('.env*'):
            if path.name == '.env.example' or not path.is_file() or path.is_symlink():
                continue
            raw = path.read_bytes()
            for encoding in ('utf-8-sig', 'gbk'):
                try:
                    content = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise ValueError('Cannot decode local environment file for publication check')
            for line in content.splitlines():
                match = re.match(r'\s*(?:export\s+)?([A-Za-z_]\w*)\s*=\s*(.*)', line)
                if match and SECRET_NAME.search(match[1]):
                    value = match[2].strip()
                    if value[:1] in ('"', "'"):
                        value = value[1:].split(value[0], 1)[0]
                    else:
                        value = re.split(r'\s+#', value, maxsplit=1)[0]
                    values.append(value)
    return {value for value in values if len(value) >= 8 and value.lower() not in PLACEHOLDERS}


def private_path(name, *, source=True):
    parts = Path(name.replace('\\', '/')).parts
    lower = [part.lower() for part in parts]
    if any(part in PRIVATE_DIRS for part in lower):
        return True
    if source and lower and lower[0] in {'data', 'api', 'dist'}:
        return True
    for part in lower:
        if part.startswith('.env') and part != '.env.example':
            return True
        if part in PRIVATE_NAMES or part.startswith('task_state_') or part.endswith(('.bak', '.backup')):
            return True
    return bool(parts and Path(parts[-1]).suffix.lower() in PRIVATE_SUFFIXES)


def content_findings(name, data, secrets=()):
    findings = set()
    for rule, pattern in PATTERNS.items():
        for match in pattern.finditer(data):
            value = (match[1] if match.lastindex else match[0]).decode('ascii', errors='ignore')
            if value.lower() in PLACEHOLDERS:
                continue
            findings.add((data.count(b'\n', 0, match.start()) + 1, rule))
    for secret in secrets:
        for value in {secret, quote(secret, safe='')}:
            offset = data.find(value.encode('utf-8'))
            if offset >= 0:
                findings.add((data.count(b'\n', 0, offset) + 1, 'local-config-secret'))
    return [{'path': name, 'line': line, 'rule': rule} for line, rule in sorted(findings)]


def scan_files(entries, secrets=(), *, source=True):
    findings = []
    for name, path in entries:
        path = Path(path)
        if private_path(name, source=source):
            findings.append({'path': name, 'line': 0, 'rule': 'private-path'})
        if path.is_symlink() or path.resolve() != path.absolute():
            findings.append({'path': name, 'line': 0, 'rule': 'linked-file'})
        elif not path.is_file():
            findings.append({'path': name, 'line': 0, 'rule': 'missing-file'})
        else:
            findings.extend(content_findings(name, path.read_bytes(), secrets))
    return findings


def require_safe_files(entries, secrets=(), *, source=True):
    findings = scan_files(entries, secrets, source=source)
    if findings:
        # No excerpts, exception payloads, key fragments or key hashes in diagnostics.
        locations = '; '.join(f"{f['path']}:{f['line']} [{f['rule']}]" for f in findings[:12])
        raise ValueError(f'Publication blocked: {len(findings)} finding(s): {locations}')
