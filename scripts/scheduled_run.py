"""Non-interactive entry point: fixed cwd, closed stdin, UTF-8 logs and real exit code."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path


def run(root: Path, check: bool = False) -> int:
    root = root.resolve()
    log_dir = root / "logs" / "scheduled"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = log_dir / f"{stamp}_{os.getpid()}.log"
    # pythonw.exe has no console handles. Children need the console interpreter,
    # but CREATE_NO_WINDOW keeps even an interactive-token task invisible.
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":
        python = python.with_name("python.exe")
    command = [str(python), "-X", "utf8", "-u", str(root / "run_all.py")]
    if check:
        command = [str(python), "-X", "utf8", "-u", "-c",
                   "import pathlib,sys; print('无人值守入口检查'); "
                   "assert pathlib.Path('run_all.py').is_file(); "
                   "assert sys.stdin.read() == ''; print('cwd/stdin/UTF-8: OK')"]
    environment = os.environ.copy()
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    result = 1
    with log_path.open("w", encoding="utf-8") as log:
        log.write(json.dumps({"started_at": datetime.now().astimezone().isoformat(),
                              "cwd": str(root), "command": command, "check": check},
                             ensure_ascii=False) + "\n")
        log.flush()
        try:
            result = subprocess.run(command, cwd=root, env=environment, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, check=False,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode
        except Exception:
            traceback.print_exc(file=log)
        log.write(f"\nexit_code={result}\n")
    # Per-run status avoids two tasks overwriting one another's diagnostics.
    log_path.with_suffix(".json").write_text(json.dumps({
        "finished_at": datetime.now().astimezone().isoformat(), "exit_code": result,
        "log": log_path.name, "check": check,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="offline entry check; no scraping/building")
    args = parser.parse_args()
    return run(Path(__file__).resolve().parents[1], check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
