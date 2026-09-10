#!/usr/bin/env python3
"""本地预览静态站：默认仅绑定 127.0.0.1，并拒绝敏感路径（N24）。

用法:
  python scripts/serve_preview.py [port] [--lan]
默认端口 8000，工作目录为项目根（本文件的上一级）。
--lan 改绑 0.0.0.0 允许局域网访问（仅限可信网络，敏感路径仍被拒绝）。
"""
from __future__ import annotations

import argparse
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

# 只公开看板需要的资源。新增参考项目、实验目录默认不可通过 HTTP 读取。
_PUBLIC_PAGES = {"index.html", "trend.html", "author.html", "site-info.json", "release-manifest.json",
                 "license.txt", "notice.txt"}
_PUBLIC_SUFFIXES = {
    "css": {".css"},
    "js": {".js"},
    "data": {".json"},
    "api": {".json"},
}

# 禁止通过 HTTP 提供的路径片段 / 文件名
_BLOCKED_NAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".git",
    ".gitignore",
    "requirements.txt",
    "ruff.toml",
    "claude.md",
    "setup.bat",
}
_BLOCKED_PREFIXES = (".env.",)
_BLOCKED_DIR_PREFIXES = (".git/", ".env")
# N59: 源码/工具目录直链不可读（静态看板只需 html/css/js/data/api）
# N10: 加 logs/——运行日志含完整爬取/构建会话详情与 AI 响应片段，不应经预览服务暴露
# P1-1(0826起点专项): 加 晋江爬虫/——第三方整包含真实域名 nginx 配置/SQLite 库/.rar，--lan 下泄露他人隐私
_BLOCKED_PATH_PREFIXES = (
    "scrapers/",
    "scripts/",
    "config/",
    "logs/",
    ".claude/",
    ".backup/",
    ".ruff_cache/",
    "docs/",
    "晋江爬虫/",
)
# P1-1(0826起点专项): 扩充敏感后缀——覆盖第三方整包内的 .rar/.db/.yml/.conf 等
_BLOCKED_SUFFIXES = (
    ".py", ".pyc", ".pyo", ".bat", ".ps1", ".toml", ".md", ".txt",
    ".rar", ".zip", ".7z", ".db", ".sqlite", ".sqlite3",
    ".yml", ".yaml", ".conf", ".ini", ".cfg",
    ".pem", ".key", ".p12", ".pfx", ".log",
)

# P2(0902): Windows 8.3 短文件名（GITIGN~1=.gitignore、RUFF~1.TOM=ruff.toml）可绕过
# 后缀黑名单（短名后缀被截到 3 字符）。短名特征：stem（去扩展名）= 前缀≤6字符 + ~ + 数字。
# 正常前端资源（html/css/js/json）不会命中此模式，宁可误杀 404 不放行。
_8DOT3_RE = re.compile(r"^[^./~]{1,6}~\d+$", re.IGNORECASE)


def _is_8dot3_name(name_lower: str) -> bool:
    """检测 8.3 短文件名（输入须已 lower）。"""
    stem = name_lower.rsplit(".", 1)[0] if "." in name_lower else name_lower
    return bool(_8DOT3_RE.match(stem))


class SafeHandler(SimpleHTTPRequestHandler):
    def _is_blocked(self, path: str) -> bool:
        """拒绝敏感路径。N48：不用 urlparse(path).path（//.env 会进 netloc）。"""
        # 只取 path 段前的请求目标，去掉 query/fragment
        raw = path or "/"
        if "?" in raw:
            raw = raw.split("?", 1)[0]
        if "#" in raw:
            raw = raw.split("#", 1)[0]
        raw = unquote(raw)
        # 协议相对 //host/x：整段按路径片段扫描（含 netloc 位）
        norm = raw.replace("\\", "/").lstrip("/")
        parts = [p for p in norm.split("/") if p and p != "."]
        # 必须在 rstrip(" .") 前检查：否则 ".." 会被剥成空串，随后文件服务
        # 再做 normpath 时就能绕过目录黑名单。冒号还可能引用 Windows ADS。
        if any(p.rstrip(" ") == ".." or ":" in p or "\x00" in p for p in parts):
            return True
        # P2(0902): Win32 打开文件时剥离最终组件尾部的空格/点——URL 尾加 %20/%2e 可让
        # `requirements.txt ` 不命中黑名单/后缀而文件照常读出。统一按剥离后的组件匹配
        # （剥离只去尾部空格/点，黑名单命中只增不减）；`..%20` 剥离后也须命中 .. 检查。
        parts = [p.rstrip(" .") for p in parts]
        if ".." in parts:
            return True
        for part in parts:
            low = part.lower()
            if low in _BLOCKED_NAMES:
                return True
            if any(low.startswith(p) for p in _BLOCKED_PREFIXES):
                return True
            if low.startswith(".env"):
                return True
            # P2(0902): 8.3 短名（GITIGN~1/RUFF~1.TOM）绕过后缀黑名单
            if _is_8dot3_name(low):
                return True
        joined = "/".join(parts).lower()
        if joined.startswith(_BLOCKED_DIR_PREFIXES):
            return True
        # P3-15(0826起点专项): 前缀尾斜杠匹配——`/scripts`(无尾斜杠) 也须命中 `scripts/`，防黑名单被精确路径绕过
        if any(joined == p.rstrip("/") or joined.startswith(p) for p in _BLOCKED_PATH_PREFIXES):
            return True
        if joined not in _PUBLIC_PAGES and any(joined.endswith(s) for s in _BLOCKED_SUFFIXES):
            return True
        if any(p.startswith(".") for p in parts):
            return True
        if any(p.lower().startswith("task_state_") for p in parts):
            return True
        if parts:
            public_page = len(parts) == 1 and joined in _PUBLIC_PAGES
            public_asset = (
                len(parts) >= 2
                and Path(joined).suffix in _PUBLIC_SUFFIXES.get(parts[0].lower(), set())
            )
            if not (public_page or public_asset):
                return True
        # SimpleHTTPRequestHandler 会跟随符号链接；同时验证实际文件仍在公开区域。
        root = Path(self.directory).resolve()
        try:
            translated = Path(self.translate_path(path))
            resolved = translated.resolve()
            relative = resolved.relative_to(root)
            # 根目录本身可能来自 Windows TEMP 的 8.3 路径，先规范化根目录，
            # 再判断公开目录内是否发生链接跳转，避免误拦所有正常资源。
            expected = root / translated.relative_to(Path(self.directory))
            if resolved != expected:
                return True
            if relative.parts and relative.parts[0].lower() not in _PUBLIC_SUFFIXES and relative.as_posix().lower() not in _PUBLIC_PAGES:
                return True
        except (OSError, RuntimeError, ValueError):
            return True
        return False

    def do_GET(self):  # noqa: N802
        if self._is_blocked(self.path):
            self.send_error(404, "Not Found")
            return
        return super().do_GET()

    def do_HEAD(self):  # noqa: N802
        if self._is_blocked(self.path):
            self.send_error(404, "Not Found")
            return
        return super().do_HEAD()

    def list_directory(self, path):  # noqa: N802
        # N59: 关闭目录列表，避免暴露 .env 等文件名与源码树
        self.send_error(404, "Not Found")
        return None

    def end_headers(self):
        # P3-8: 静态资源禁用浏览器缓存——SimpleHTTPRequestHandler 默认按 Last-Modified
        # 启发式缓存，改 js/config.js 后浏览器沿用旧 JS、新增榜单看不到（需硬刷新）。
        # no-cache 每次重新校验，改动即时生效。
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # Never log query values or attacker-controlled terminal escape sequences.
        message = re.sub(r'\?[^\s]*', '?[REDACTED]', fmt % args)
        message = ''.join(c if c.isprintable() else '?' for c in message)
        sys.stderr.write(f"{self.address_string()} - {message}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('port', nargs='?', type=int, default=8000)
    parser.add_argument('--lan', action='store_true')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    port, lan = args.port, args.lan
    root = args.root.resolve()
    # dist/current.json points at an immutable release; select it once per server.
    if (root / 'current.json').is_file():
        import json
        try:
            target = (root / json.loads((root / 'current.json').read_text(encoding='utf-8'))['path']).resolve()
            target.relative_to(root / 'releases')
            root = target
        except (OSError, ValueError, KeyError, TypeError) as exc:
            parser.error(f'Invalid release pointer: {exc}')
    if not 1 <= port <= 65535 or not (root / 'index.html').is_file():
        parser.error('Port must be 1-65535 and root must contain index.html.')

    host = "0.0.0.0" if lan else "127.0.0.1"
    server = ThreadingHTTPServer((host, port), partial(SafeHandler, directory=str(root)))
    if lan:
        print(f"小说指数预览: http://0.0.0.0:{port}/  (局域网可访问，已屏蔽 .env 等敏感文件)")
        print(f"局域网设备请用本机局域网 IP 访问，如 http://<本机IP>:{port}/")
    else:
        print(f"小说指数预览: http://127.0.0.1:{port}/  (仅本机，已屏蔽 .env 等敏感文件)")
    print(f"根目录: {root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
