"""Small synchronous JSON transport with bounded responses and explicit retry policy.

Uses urllib, gzip/zlib and HTTP Retry-After semantics; no third-party crawler
headers, asynchronous client, circuit breaker implementation or configuration.
"""
from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


class JsonHttpClient:
    def __init__(self, *, attempts=3, max_bytes=8 * 1024 * 1024, opener=None, sleep=None, clock=None):
        if attempts < 1 or max_bytes < 1:
            raise ValueError('attempts and max_bytes must be positive')
        self.attempts = attempts
        self.max_bytes = max_bytes
        self.open = opener or urllib.request.urlopen
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self.cooldown_until = 0
        self.cooldown_code = None

    @staticmethod
    def retry_after(value):
        try:
            delay = int(value)
        except (ValueError, TypeError):
            try:
                stamp = parsedate_to_datetime(value)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                delay = (stamp - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return 60
        return max(60, delay)

    def decode(self, raw, encoding):
        encoding = encoding.lower().strip()
        if encoding == 'gzip' or raw[:2] == b'\x1f\x8b':
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif encoding == 'deflate':
            decompressor = zlib.decompressobj()
        elif encoding not in ('', 'identity'):
            raise ValueError('Unsupported content encoding')
        else:
            decompressor = None
        if decompressor:
            try:
                raw = decompressor.decompress(raw, self.max_bytes + 1)
            except zlib.error:
                if encoding != 'deflate':
                    raise
                decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
                raw = decompressor.decompress(raw, self.max_bytes + 1)
            if len(raw) > self.max_bytes or not decompressor.eof:
                raise ValueError('Truncated or oversized compressed response')
        def invalid_number(value):
            raise ValueError(f'Invalid JSON number: {value}')
        result = json.loads(raw.decode('utf-8-sig'), parse_constant=invalid_number)
        if not isinstance(result, (dict, list)):
            raise ValueError('Expected a JSON object or list')
        return result

    def get(self, url, timeout=30):
        if self.clock() < self.cooldown_until:
            return None, self.cooldown_code
        error = 'network'
        for attempt in range(self.attempts):
            try:
                request = urllib.request.Request(url, headers={
                    'User-Agent': 'NovelIndex/1.0', 'Accept': 'application/json',
                    'Accept-Encoding': 'gzip, deflate',
                })
                with self.open(request, timeout=timeout) as response:
                    raw = response.read(self.max_bytes + 1)
                    if len(raw) > self.max_bytes:
                        return None, 'size'
                    return self.decode(raw, response.headers.get('Content-Encoding', '')), None
            except urllib.error.HTTPError as exc:
                error = exc.code
                if error in (403, 429, 503):
                    self.cooldown_code = error
                    self.cooldown_until = self.clock() + self.retry_after((exc.headers or {}).get('Retry-After'))
                    return None, error
                if 400 <= error < 500:
                    return None, error
            except (ValueError, UnicodeError, zlib.error, gzip.BadGzipFile):
                error = 'parse'
            except (OSError, urllib.error.URLError):
                error = 'network'
            if attempt + 1 < self.attempts:
                self.sleep(2 ** (attempt + 1))
        return None, error
