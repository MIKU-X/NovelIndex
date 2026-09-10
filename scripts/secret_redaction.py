"""Redact configured credentials and common authentication payloads from errors."""
from __future__ import annotations

import os
import re
from urllib.parse import quote


def mask_secrets(value, *, secrets=()):
    text = str(value)
    configured = set(secrets)
    configured.update(v for k, v in os.environ.items()
                      if re.search(r'(?:^|_)(?:KEY|TOKEN|SECRET|PASSWORD|COOKIE)$', k, re.I))
    for secret in sorted((s for s in configured if isinstance(s, str) and s), key=len, reverse=True):
        for variant in {secret, quote(secret, safe='')}:
            text = text.replace(variant, '[REDACTED]')
    text = re.sub(r'sk-[A-Za-z0-9_-]{8,}', 'sk-***', text)
    text = re.sub(r'(?i)(https?://)[^/\s@]+@', r'\1[REDACTED]@', text)
    text = re.sub(r'''(?i)([?&](?:key|api_key|token|apikey|access_token|secret|password)=)[^&\s'"<>]+''', r'\1[REDACTED]', text)
    text = re.sub(r'''(?i)(\b(?:Bearer|Basic)\s+)[A-Za-z0-9_+/=.%-]+''', r'\1[REDACTED]', text)
    text = re.sub(r'''(?i)(["']?\b(?:api_key|apiKey|access_token|token|password|secret)["']?\s*[:=]\s*)(["'])(.*?)\2''',
                  lambda m: m[1] + m[2] + '[REDACTED]' + m[2], text)
    text = re.sub(r'(?im)(\b(?:set-cookie|cookie)\s*:\s*)[^\r\n]+', r'\1[REDACTED]', text)
    return text
