"""Optional .env loading, preserving explicit process environment variables."""
from pathlib import Path


def load_environment():
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    path = Path(__file__).resolve().parents[1] / ".env"
    for encoding in ("utf-8-sig", "gbk"):
        try:
            load_dotenv(path, encoding=encoding, override=False)
            return
        except (UnicodeDecodeError, ValueError):
            continue
    print("[WARN] .env 编码无法识别，使用进程环境变量")
