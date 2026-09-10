"""分析最新快照日全部平台榜单数据，并对比上一快照日做趋势汇总。
日期自动取 data/ 最新两个快照（N1：原硬编码 20260806 隔几天不跑就跑出"看似有效实为旧数据"）。
用于在 build_latest 仍在跑 AI 分析时，先给出基于原始快照的硬数据分析。
输出到 stdout。
"""
import json
import os
import sys

# Windows 终端 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根目录
DATA = os.path.join(BASE, "data")
sys.path.insert(0, BASE)  # N54: 允许从项目根导入 config.loader


# P3-2/P2-3: 直接复用 snapshot_utils，删本地 _latest_dates 包装与 parse_num 重复实现
from snapshot_utils import latest_complete_dates, parse_metric  # noqa: E402

TODAY, YESTERDAY = latest_complete_dates(DATA)
print(f"[日期] 自动取最新快照：TODAY={TODAY} YESTERDAY={YESTERDAY}")


def parse_num(s):
    """'17万' / '606万字' / '8.2M' -> float。P2-3: 委托 snapshot_utils.parse_metric。"""
    return parse_metric(s)


def fmt(n):
    if n >= 1e8:
        return f"{n/1e8:.2f}亿"
    if n >= 1e4:
        return f"{n/1e4:.1f}万"
    return str(int(n))


def load_snapshot(slug, date):
    p = os.path.join(DATA, f"{slug}_ranks_{date}.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# N54: SLUGS 从 config/platforms.json 动态派生（原硬编码 7 榜，新增平台会静默漏掉）。
# 展示名保持历史短格式（番茄/起点/晋江），未知平台回退全名。
_SHORT_PLATFORM = {"fanqie": "番茄", "qidian": "起点", "jjwxc": "晋江"}


def _label(r):
    short = _SHORT_PLATFORM.get(r["platform"], r["platform_name"])
    return f"{short}·{r['name']}"


from config.loader import iter_ranks  # noqa: E402

SLUGS = [(r["slug"], _label(r)) for r in iter_ranks()]

print("=" * 70)
print(f"小说指数榜单分析  日期 {TODAY[:4]}-{TODAY[4:6]}-{TODAY[6:8]}  (对比 {YESTERDAY[:4]}-{YESTERDAY[4:6]}-{YESTERDAY[6:8]})")
print("=" * 70)

for slug, name in SLUGS:
    snap = load_snapshot(slug, TODAY)
    if not snap:
        print(f"\n【{name}】今日快照缺失")
        continue
    cats = snap.get("categories", [])
    total_books = sum(len(c.get("books", [])) for c in cats)
    platform = snap.get("platform_name", "")
    # 量纲
    metric = "views"
    if cats and cats[0].get("books"):
        metric = cats[0]["books"][0].get("metric_type", "views")
    unit = {"views": "在读", "word_count": "字数", "score": "积分"}.get(metric, "")

    print(f"\n{'─'*70}")
    print(f"【{name}】 {platform}  |  {len(cats)} 分类 / {total_books} 本  |  量纲: {unit}")

    # 每分类 top1 + 该分类最大值
    cat_rows = []
    for c in cats:
        books = c.get("books", [])
        if not books:
            cat_rows.append((c["name"], None, 0))
            continue
        # 取指标最大的（books 已按排名，但指标可能非单调，取指标 max 更稳）
        best = max(books, key=lambda b: parse_num(b.get("reads") or b.get("score") or b.get("word_count")))
        val = parse_num(best.get("reads") or best.get("score") or best.get("word_count"))
        cat_rows.append((c["name"], best, val))

    # 按指标值排序，展示分类热度
    cat_rows_sorted = sorted([r for r in cat_rows if r[1]], key=lambda x: x[2], reverse=True)
    print(f"\n  ▶ 分类热度 TOP（按头部作品{unit}排序，取每分类第1名）:")
    for cname, book, val in cat_rows_sorted[:8]:
        print(f"    {cname:<8} {fmt(val):>8}  《{book['title'][:20]}》 {book.get('author','')[:12]}")
    if len(cat_rows_sorted) > 8:
        print(f"    ... 另有 {len(cat_rows_sorted)-8} 个分类")

    # 与昨日对比：新书（今日有昨日无的 title）、掉榜
    yest = load_snapshot(slug, YESTERDAY)
    if yest:
        yest_titles = set()
        for c in yest.get("categories", []):
            for b in c.get("books", []):
                yest_titles.add(b.get("title", ""))
        today_titles = []
        for c in cats:
            for b in c.get("books", []):
                today_titles.append(b.get("title", ""))
        today_set = set(today_titles)
        new_in = today_set - yest_titles
        dropped = yest_titles - today_set
        overlap = today_set & yest_titles
        churn = len(new_in) / max(len(today_set), 1) * 100
        print(f"\n  ▶ 日环比（vs {YESTERDAY[4:6]}-{YESTERDAY[6:8]}）:")
        print(f"    今日上榜 {len(today_set)} 本，昨日 {len(yest_titles)} 本，重叠 {len(overlap)} 本")
        print(f"    新进榜 {len(new_in)} 本（{churn:.0f}% 换手率），掉出 {len(dropped)} 本")

print("\n" + "=" * 70)
print("分析完成")
