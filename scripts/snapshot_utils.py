"""N18: 快照完整性校验（供 analyze_today.py / analyze_deep.py 复用）。

爬虫被验证码/反爬中断时会留下「文件存在但分类/本数远低于正常」的部分快照，
只读分析脚本若直接使用，会把残缺日当完整日产出误导性结论。build_latest.py 的
load_snapshot 已有 B9 完整性检测，本模块提供同口径阈值给分析脚本复用。

用法：
    from snapshot_utils import snapshot_complete, latest_complete_dates
"""
import json
import math
import os
import re

# B9 完整性下限（与 build_latest.load_snapshot 同口径，约正常值的 85%~88%）
# slug 级优先（按文件名前缀），platform 级兜底。
# 起点三江榜不分类、仅最新一期 ~30 本，须单独阈值（platform 级 14 分类阈值会误判残缺）。
_PLATFORM_MIN = {
    "fanqie": (16, 450),   # 正常约 18 分类 / 540 本
    "qidian": (12, 160),   # 旧格式兜底（历史快照）
    "jjwxc": (22, 410),    # 去重后正常 24 分类 / 460 本（~10% 容差）
}
_RANK_MIN = {
    "qidian_hotsales": (12, 320),     # 正常 14 分类 / ~420 本（按分类榜单抓取）
    "qidian_signnewbook": (12, 260),     # 正常 14 分类 / ~300-310 本（冷门分类不足 30）
    "qidian_newsign": (12, 260),
    "qidian_sanjiang": (1, 10),       # 不分类总榜，最新一期 ~17 本（每期数量不定）
}

_METRIC_RE = re.compile(r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:e[+-]?[0-9]+)?)\s*([万亿KMG]?)", re.IGNORECASE)


def parse_metric(value) -> float:
    """统一解析指标字符串为数值（P2-3）。

    兼容万/亿中文量词、K/M/G 单位（晋江积分）、"字"后缀（起点字数）、千分位逗号。
    失败返回 0。供 build_latest.parse_reads / analyze_today.parse_num /
    analyze_deep.parse_num 复用，避免多处复制解析逻辑（单位格式变更只需改此处）。
    """
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return 0
    s = str(value).strip().replace(",", "").removesuffix("字").strip()
    match = _METRIC_RE.fullmatch(s)
    if not match:
        return 0
    try:
        multiplier = {"万": 10000, "亿": 100_000_000, "K": 1000, "M": 1_000_000, "G": 1_000_000_000}
        result = float(match[1]) * multiplier.get(match[2].upper(), 1)
        return result if math.isfinite(result) and result > 0 else 0
    except (ValueError, OverflowError):
        return 0


def snapshot_complete(path, lenient=False):
    """判断单个快照文件是否完整：结构合法（categories 为非空数组）且达到
    平台分类/本数下限。未知平台不设下限（视为完整，交给调用方判断）。

    lenient=True：宽松模式（--date 历史重建用），阈值取 slug 级与 platform 级的
    较小者。供 build_latest --date 历史重建使用——旧格式快照（如起点 8/24 及更早
    hotsales 仅 190-220 本）低于新 slug 级阈值 (12,320)，但结构合法、数据可用，
    历史重建不应判残缺（P1-3）。
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    cats = data.get("categories")
    if not isinstance(cats, list) or not cats or any(
        not isinstance(c, dict) or not isinstance(c.get("name"), str)
        or not isinstance(c.get("books"), list)
        or any(not isinstance(b, dict) for b in c["books"])
        for c in cats
    ):
        return False
    if len({c["name"] for c in cats}) != len(cats):
        return False
    # 新快照明确记录尚未完成的分类时，不用条目数阈值把它误判为成功。
    quality = data.get("quality")
    if isinstance(quality, dict) and quality.get("rank_status") == "partial":
        return False
    # slug 前缀优先（按文件名，如 qidian_sanjiang），platform 级兜底
    base = os.path.basename(path)
    slug = base.split("_ranks_")[0] if "_ranks_" in base else ""
    p_cat, p_book = _PLATFORM_MIN.get(data.get("platform", ""), (0, 0))
    if not lenient:
        min_cat, min_book = _RANK_MIN.get(slug, (p_cat, p_book))
    else:
        # lenient：取 slug 严格阈值与 platform 兜底的较小者。P3-7(0902) 修复：
        # 原实现直接套 platform 级，导致三江榜 strict (1,10) 可过、lenient 却被
        # qidian (12,160) 误判残缺——宽松模式反而比严格模式更严
        r_cat, r_book = _RANK_MIN.get(slug, (p_cat, p_book))
        min_cat, min_book = min(p_cat, r_cat), min(p_book, r_book)
    if not min_cat:
        return True
    cat_count = len(cats)
    book_count = sum(len(c.get("books", [])) for c in cats if isinstance(c, dict))
    return cat_count >= min_cat and book_count >= min_book


def snapshot_quality(data):
    """Summarize missing details without requiring them to publish valid rankings.

    Old snapshots retain unknown observation times; never fabricate today's timestamp.
    """
    previous = data.get("quality")
    quality = dict(previous) if isinstance(previous, dict) else {}
    books = [b for c in data.get("categories", []) if isinstance(c, dict)
             for b in c.get("books", []) if isinstance(b, dict)]
    valid = [b for b in books if math.isfinite(parse_metric(b.get("reads")))
             and parse_metric(b.get("reads")) > 0]
    missing_authors = sum(not b.get("author") for b in books)
    missing_intros = sum(not b.get("intro") or b.get("intro") == "暂无简介" for b in books)
    quality.update({
        "rank_status": quality.get("rank_status", "unknown"),
        "detail_status": "complete" if books and len(valid) == len(books) and not missing_authors and not missing_intros else "partial",
        "book_count": len(books),
        "metrics_available": len(valid),
        "metrics_missing": len(books) - len(valid),
        "metric_coverage": round(len(valid) / len(books), 4) if books else 0,
        "metrics_cached": sum(b.get("word_count_source") == "cache" for b in valid),
        "metrics_observed_unknown": sum(not b.get("word_count_observed_at") for b in valid) if data.get("platform") == "qidian" else 0,
        "authors_missing": missing_authors,
        "intros_missing": missing_intros,
    })
    return quality


def latest_complete_dates(data_dir):
    """扫描 data/ 下 `*_ranks_YYYYMMDD.json`，返回「当日现有快照全部完整」的
    最新两个日期 (today, yesterday)；无完整日返回 ("", "")。

    说明：跨午夜/中断场景下某日可能只有部分快照（爬虫仍在跑或被打断），
    只要当日存在任一残缺快照，该日整体视为残缺日跳过并自动回退到上一个
    完整日——避免分析出「看似有效实为残缺」的结论。
    """
    by_date = {}
    for f in os.listdir(data_dir):
        m = re.search(r"_ranks_(\d{8})\.json$", f)
        if m:
            by_date.setdefault(m.group(1), []).append(os.path.join(data_dir, f))
    complete = sorted(
        d for d, paths in by_date.items()
        if paths and all(snapshot_complete(p) for p in paths)
    )
    if not complete:
        return "", ""
    if len(complete) == 1:
        return complete[0], complete[0]
    return complete[-1], complete[-2]
