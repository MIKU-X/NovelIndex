"""深度分析：跨平台关键词热度 + 各榜新进榜作品 + 头部作品跨平台分布。
"""
import json
import os
import sys
from collections import Counter, defaultdict

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
sys.path.insert(0, BASE)  # N54: 允许从项目根导入 config.loader


# P3-2/P2-3: 直接复用 snapshot_utils，删本地 _latest_dates 包装与 parse_num 重复实现
from snapshot_utils import latest_complete_dates, parse_metric  # noqa: E402

TODAY, YESTERDAY = latest_complete_dates(DATA)
print(f"[日期] 自动取最新快照：TODAY={TODAY} YESTERDAY={YESTERDAY}")


def parse_num(s):
    """'17万' / '606万字' / '8.2M' -> float。P2-3: 委托 snapshot_utils.parse_metric。"""
    return parse_metric(s)


def load(slug, date):
    p = os.path.join(DATA, f"{slug}_ranks_{date}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# N54: 榜单从 config/platforms.json 动态派生（原硬编码 7 榜，新增平台会静默漏掉）。
# 展示名保留历史短格式；未知 slug 回退「平台短名·榜单名」。
_SHORT_PLATFORM = {"fanqie": "番茄", "qidian": "起点", "jjwxc": "晋江"}
_SHORT_LABELS = {
    "fanqie_male_new": "番茄·男频新书",
    "fanqie_male_read": "番茄·男频阅读",
    "fanqie_female_new": "番茄·女频新书",
    "fanqie_female_read": "番茄·女频阅读",
    "qidian_hotsales": "起点·畅销",
    "qidian_signnewbook": "起点·新书",
    "jjwxc_month": "晋江·月榜",
}


def _label(r):
    if r["slug"] in _SHORT_LABELS:
        return _SHORT_LABELS[r["slug"]]
    short = _SHORT_PLATFORM.get(r["platform"], r["platform_name"])
    return f"{short}·{r['name']}"


from config.loader import iter_ranks  # noqa: E402

SLUGS = [(r["slug"], _label(r)) for r in iter_ranks()]

# 关键词族（题材/套路标签）
KW = {
    "重生/穿越": ["重生", "穿越", "穿书", "回到"],
    "系统/面板": ["系统", "面板", "签到", "加点", "金手指", "属性"],
    "玄幻/修仙": ["修仙", "玄幻", "仙侠", "巫师", "龙", "灵气", "修真", "丹"],
    "都市/豪门": ["豪门", "总裁", "恋综", "娱乐圈", "财阀", "影帝", "顶流"],
    "历史/年代": ["大明", "历史", "年代", "穿越六零", "知青", "民国", "抗战"],
    "悬疑/诡秘": ["悬疑", "诡秘", "灵异", "怪谈", "克苏鲁", "scp"],
    "游戏/诸天": ["游戏", "诸天", "副本", "无限流", "轮回", "玩家"],
    "甜宠/言情": ["甜宠", "病娇", "修罗场", "恋爱", "青梅", "霸总"],
}

print("=" * 72)
print("跨平台关键词热度（标题含题材标签的作品数，番茄按在读加权、起点/晋江按出现频次）")
print("=" * 72)

for slug, name in SLUGS:
    snap = load(slug, TODAY)
    if not snap:
        continue
    titles = []
    for c in snap.get("categories", []):
        for b in c.get("books", []):
            titles.append(b.get("title", ""))
    cnt = Counter()
    for kw, words in KW.items():
        n = sum(1 for t in titles if any(w in t for w in words))
        cnt[kw] = n
    total = max(len(titles), 1)
    top = cnt.most_common(5)
    print(f"\n【{name}】 共 {len(titles)} 本")
    for kw, n in top:
        if n == 0:
            break
        bar = "█" * int(n / total * 50)
        print(f"  {kw:<10} {n:>3}本 {n/total*100:4.0f}%  {bar}")

# 新进榜作品（今日有、昨日无）—— 各榜取头部新进
print("\n" + "=" * 72)
print("各榜「新进榜」作品 TOP（今日新进入榜单，按指标排序）")
print("=" * 72)

for slug, name in SLUGS:
    snap = load(slug, TODAY)
    yest = load(slug, YESTERDAY)
    if not snap:
        continue
    yest_titles = set()
    if yest:
        for c in yest.get("categories", []):
            for b in c.get("books", []):
                yest_titles.add(b.get("title", ""))
    new_books = []
    for c in snap.get("categories", []):
        for b in c.get("books", []):
            if b.get("title", "") not in yest_titles:
                val = parse_num(b.get("reads") or b.get("score") or b.get("word_count"))
                new_books.append((c["name"], b, val))
    new_books.sort(key=lambda x: x[2], reverse=True)
    print(f"\n【{name}】 新进 {len(new_books)} 本，头部:")
    for cat, b, val in new_books[:5]:
        unit = "在读"
        mt = b.get("metric_type")
        if mt == "word_count":
            unit = "字数"
        elif mt == "score":
            unit = "积分"
        vstr = f"{val/1e8:.2f}亿" if val >= 1e8 else (f"{val/1e4:.1f}万" if val >= 1e4 else str(int(val)))
        print(f"  [{cat}] {vstr:>8}{unit}  《{b['title'][:22]}》{b.get('author','')[:12]}")

# 晋江跨频道现象
print("\n" + "=" * 72)
print("晋江·跨频道现象（同一作品出现在多个分类）")
print("=" * 72)
jj = load("jjwxc_month", TODAY)
if jj:
    title_cats = defaultdict(list)
    for c in jj.get("categories", []):
        for b in c.get("books", []):
            title_cats[b.get("title", "")].append(c["name"])
    multi = [(t, cs) for t, cs in title_cats.items() if len(cs) >= 2]
    multi.sort(key=lambda x: len(x[1]), reverse=True)
    print(f"出现在 ≥2 个频道的作品：{len(multi)} 部\n")
    for t, cs in multi[:10]:
        print(f"  《{t[:24]}》 × {len(cs)} 频道: {', '.join(cs)}")
