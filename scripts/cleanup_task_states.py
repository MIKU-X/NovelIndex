"""清理孤儿 task_state 文件 / 过期快照。

task_state_{slug}_{YYYYMMDD}.json 是爬虫断点续传的中间状态，只在当天续传时有意义。
若对应的 {slug}_ranks_{YYYYMMDD}.json 快照已不存在（抓取未完成/快照已删），该
task_state 即孤儿文件，可安全删除，避免长期累积（此前 data/ 下累积了 200+ 个历史
task_state，见审查报告 N7）。

用法：
    python scripts/cleanup_task_states.py             # 清理孤儿（默认，最安全）
    python scripts/cleanup_task_states.py --dry-run   # 只列出不删除
    python scripts/cleanup_task_states.py --all       # 删除所有非当日 task_state（更激进）
    python scripts/cleanup_task_states.py --keep-days N
        # N77: 删除 N 天前的每日快照（*_ranks_YYYYMMDD.json，全平台）与对应日期的
        # task_state；替代 setup.bat 内嵌的 python -c 一行式（无错误处理、含死代码）。
        # --dry-run 可配合使用。
"""
import argparse
import glob
import json
import os
import re
import sys
from contextlib import ExitStack, contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.loader import iter_ranks
from scrapers.base import write_json
from scripts.runtime_state import LockUnavailable, exclusive_lock

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")


def collect_task_states():
    """返回 [(task_state_path, slug, date_str)]，按文件名排序。"""
    items = []
    for p in sorted(glob.glob(os.path.join(DATA, "task_state_*.json"))):
        name = os.path.basename(p)
        m = re.match(r"task_state_([a-z0-9_]+)_(\d{8})\.json$", name)
        if m:
            items.append((p, m.group(1), m.group(2)))
    return items


def _parse_date_suffix(name, pattern):
    """从文件名提取 YYYYMMDD 并转 date；不匹配/非法返回 None（保守不删）。"""
    m = re.match(pattern, name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


@contextmanager
def cleanup_guard(data_dir):
    """Coordinate retention with the existing builder and scraper entry points."""
    root = Path(data_dir).absolute()
    if root.resolve() != root:
        raise ValueError('Data directory must not be a link')
    runtime = root.parent / '.runtime'
    platforms = {r['platform'] for r in iter_ranks()}
    with ExitStack() as locks:
        for name in ['build', *[f'scrape-{p}' for p in sorted(platforms)]]:
            locks.enter_context(exclusive_lock(runtime / f'{name}.lock'))
        yield


def cleanup_by_keep_days(data_dir, keep_days, dry_run=False):
    """删除旧快照，保留 latest 引用的快照；先更新索引再删除文件。

    - 快照：data/{slug}_ranks_YYYYMMDD.json；最新看板所需的快照不删除。
    - 日期索引：只保留仍存在且未计划删除的快照，保留其他元数据。
    - 状态：data/task_state_*.json
    返回 (删除文件数, 释放字节数)。
    """
    if keep_days < 1:
        raise ValueError('--keep-days 必须至少为 1')
    with cleanup_guard(data_dir):
        return _cleanup_by_keep_days(Path(data_dir), keep_days, dry_run)


def _cleanup_by_keep_days(data_dir, keep_days, dry_run):
    cutoff = date.today() - timedelta(days=keep_days)
    print(f"清理 {keep_days} 天前的快照/task_state（截止日期 < {cutoff}）"
          + ("（dry-run，不实际删除）" if dry_run else ""))

    def checked(path):
        path = Path(path).absolute()
        if path.resolve() != path or path.parent != data_dir.absolute():
            raise ValueError(f'拒绝清理链接或目录外文件：{path.name}')
        return path

    def read(path):
        payload = json.loads(checked(path).read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError(f'无效数据文件：{path.name}')
        return payload

    def snapshot(slug, day):
        if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise ValueError(f'无效快照日期：{slug}')
        date.fromisoformat(day)
        return checked(data_dir / f'{slug}_ranks_{day.replace("-", "")}.json')

    # Read every index before changing anything. A damaged latest/index must not
    # cause us to delete the only snapshot backing the currently published view.
    protected = set()
    latest_dates = {}
    for path in sorted(data_dir.glob('latest_*_ranks.json')):
        slug = path.name[len('latest_'):-len('_ranks.json')]
        day = read(path).get('date')
        raw = snapshot(slug, day)
        if not raw.is_file():
            raise ValueError(f'latest 引用的快照缺失，请先重新构建：{raw.name}')
        protected.add(raw)
        latest_dates[slug] = day

    targets = []
    for p in sorted(glob.glob(os.path.join(data_dir, "*_ranks_*.json"))):
        d = _parse_date_suffix(os.path.basename(p), r"^.+_ranks_(\d{8})\.json$")
        if d is not None and d < cutoff:
            path = checked(p)
            if path not in protected:
                targets.append(path)
    for p in sorted(glob.glob(os.path.join(data_dir, "task_state_*.json"))):
        d = _parse_date_suffix(os.path.basename(p), r"^task_state_.+_(\d{8})\.json$")
        if d is not None and d < cutoff:
            targets.append(checked(p))

    deleting = set(targets)
    indexes = {p.name[len('dates_'):-len('.json')]: p for p in data_dir.glob('dates_*.json')}
    for slug in latest_dates:
        indexes.setdefault(slug, data_dir / f'dates_{slug}.json')
    updates = []
    for slug, path in sorted(indexes.items()):
        payload = read(path) if path.exists() else {'dates': []}
        days = payload.get('dates')
        if not isinstance(days, list):
            raise ValueError(f'无效日期索引：{path.name}')
        remaining = {day for day in days if snapshot(slug, day).is_file() and snapshot(slug, day) not in deleting}
        if slug in latest_dates:
            remaining.add(latest_dates[slug])
        if sorted(remaining) != days:
            updates.append((checked(path), {**payload, 'dates': sorted(remaining)}))

    # If a write fails, no snapshots have been deleted. Earlier index updates
    # merely hide retained files. If a delete fails later, its unindexed file is
    # safe to retry; an index never points at a snapshot removed by this run.
    for path, payload in updates:
        print(f'  更新 {path.name}：保留 {len(payload["dates"])} 个日期')
        if not dry_run:
            write_json(str(path), payload)

    freed = 0
    deleted = 0
    errors = []
    for p in targets:
        try:
            size = os.path.getsize(p)
        except OSError:
            size = 0
        print(f"  🗑  {os.path.basename(p)} ({size} 字节)")
        if not dry_run:
            try:
                os.remove(p)
                deleted += 1
                freed += size
            except OSError as e:
                print(f"    删除失败: {e}")
                errors.append(p.name)
        else:
            freed += size
    if errors:
        raise OSError(f'清理未全部完成，日期索引已安全更新：{", ".join(errors)}')
    if dry_run:
        print(f"（dry-run，共 {len(targets)} 个文件、约 {freed/1024/1024:.1f} MB 未实际删除）")
    else:
        print(f"清理完成：删除 {deleted} 个文件，释放约 {freed/1024/1024:.1f} MB")
    return deleted, freed


def main():
    parser = argparse.ArgumentParser(description="清理孤儿 task_state 文件 / 过期快照")
    parser.add_argument("--dry-run", action="store_true", help="只列出不删除")
    parser.add_argument("--all", action="store_true",
                        help="删除所有非当日 task_state（默认仅删对应快照不存在的孤儿）")
    parser.add_argument("--keep-days", type=int, default=None,
                        help="删除 N 天前的每日快照与 task_state（setup.bat 清理菜单用）")
    args = parser.parse_args()

    if args.keep_days is not None:
        cleanup_by_keep_days(DATA, args.keep_days, dry_run=args.dry_run)
        return

    with cleanup_guard(DATA):
        cleanup_states(args.all, args.dry_run)


def cleanup_states(all_states=False, dry_run=False):
    today = date.today().strftime("%Y%m%d")
    items = collect_task_states()

    to_delete = []
    for p, slug, date_str in items:
        if all_states:
            if date_str != today:
                to_delete.append(p)
            continue
        # 默认模式：对应日期的快照文件不存在 → 孤儿，删
        snapshot = os.path.join(DATA, f"{slug}_ranks_{date_str}.json")
        if not os.path.exists(snapshot):
            to_delete.append(p)

    mode = "所有非当日" if all_states else "对应快照不存在的孤儿"
    print(f"扫描到 {len(items)} 个 task_state，将删除 {len(to_delete)} 个（{mode}）")
    errors = []
    for p in to_delete:
        print(f"  🗑  {os.path.basename(p)}")
        if not dry_run:
            try:
                os.remove(p)
            except OSError as e:
                print(f"    删除失败: {e}")
                errors.append(p)
    if errors:
        raise OSError(f'未能清理 {len(errors)} 个状态文件')
    if dry_run:
        print("（dry-run，未实际删除）")
    else:
        print("清理完成")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, LockUnavailable) as exc:
        print(f'清理未完成：{exc}')
        raise SystemExit(2 if isinstance(exc, LockUnavailable) else 1) from None
