"""Local runtime paths and process locks released by the OS on process exit."""
import errno
import os
import time
from contextlib import contextmanager
from functools import partial, wraps
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parents[1] / ".runtime"


def publish_directory(source, target):
    """Rename a fresh sibling directory; tolerate brief Windows scanner locks."""
    source, target = Path(source).absolute(), Path(target).absolute()
    if source.parent.resolve() != target.parent.resolve() or source.resolve() != source or not source.is_dir():
        raise ValueError('Publication directories must be unlinked siblings')
    for attempt in range(5):
        if target.exists():
            raise FileExistsError('Publication target already exists')
        try:
            source.rename(target)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.5 * (attempt + 1))


class LockUnavailable(RuntimeError):
    pass


@contextmanager
def exclusive_lock(path, timeout=0):
    """Lock one persistent file; never delete it or steal a live process's lock."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    locked = False
    deadline = time.monotonic() + timeout
    try:
        if os.name == "nt":
            import msvcrt
            # Windows byte locks also work beyond EOF; no write before acquisition.
            acquire = partial(msvcrt.locking, handle.fileno(), msvcrt.LK_NBLCK, 1)
            release = partial(msvcrt.locking, handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = partial(fcntl.flock, handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = partial(fcntl.flock, handle.fileno(), fcntl.LOCK_UN)
        while True:
            handle.seek(0)
            try:
                acquire()
                locked = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise LockUnavailable(f"已有任务正在运行：{path.name}，请等待其完成") from exc
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        yield
    finally:
        try:
            if locked:
                handle.seek(0)
                release()
        finally:
            handle.close()


def single_run(name):
    """Protect a CLI entry point without holding the same lock in its children."""
    def decorate(fn):
        @wraps(fn)
        def run(*args, **kwargs):
            try:
                with exclusive_lock(RUNTIME_DIR / f"{name}.lock"):
                    return fn(*args, **kwargs)
            except LockUnavailable as exc:
                print(f"⚠️ {exc}")
                raise SystemExit(2) from exc
        return run
    return decorate
