"""晋江文学城积分月榜爬虫（App API 路线）。

按平台响应字段提取月榜；历史参考与本次替代记录见 docs/来源核对.md。
- 本项目 JsonHttpClient：最小请求头、响应大小上限、HTTP 冷却与有限重试
- getFullPageV1?channel={channel} 取各频道"六排行榜"下的月榜 list（首屏 ~20 本/分类）
- 按完整分类树遍历（6 频道 + 22 子分类，配置在 platforms.json 的 channels/subcategories）
- novelbasicinfo 详情接口补积分/字数/收藏等
- reads 填积分（format_score K/M/G），metric_type="score"，score 存原始数值

输出：data/jjwxc_month_ranks_YYYYMMDD.json
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.base import BaseScraper, write_json
from scrapers.json_http import JsonHttpClient
from scripts.runtime_state import single_run
from scripts.snapshot_utils import parse_metric

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REQUEST_DELAY = 1.0          # 请求间限流（晋江 App API 高频会 503）
DETAIL_DELAY = 0.8           # 详情页间限流
_HTTP = JsonHttpClient()

API_BASE = "https://app-cdn.jjwxc.com"
URL_FULLPAGE = API_BASE + "/bookstore/getFullPageV1?channel={channel}&version=20&use_cdn=1"
URL_DETAIL = API_BASE + "/androidapi/novelbasicinfo?novelId={novel_id}"


def api_get(url, timeout=30):
    """Return JSON/error without retrying a site block for each category."""
    return _HTTP.get(url, timeout)


def extract_monthly_rank(payload):
    """Read the documented-by-response monthly section; malformed sections are skipped."""
    groups = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(groups, list):
        return []
    for group in groups:
        if not isinstance(group, dict) or group.get("rank_group_type") != "rankings":
            continue
        sections = group.get("data")
        if not isinstance(sections, list):
            continue
        for section in sections:
            if not isinstance(section, dict) or str(section.get("channelName") or "").strip() != "月榜":
                continue
            rows = section.get("list") or section.get("data")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict) and str(row.get("novelId", "")).isdigit()]
    return []


def parse_score(value):
    return round(parse_metric(value))


def format_score(n):
    """积分数值格式化为 K/M/G 字符串（reads 字段用）。"""
    if not n:
        return "0"
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}G"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1000:
        return f"{n/1000:.1f}K"
    return str(n)


# C3: novelId → detail 运行期缓存。同一 novelId 跨分类重复出现率实测约 35%，
# 命中直接复用，避免对同一本书重复请求详情接口。
# P3-14(0902): 加容量上限——模块级 dict 随进程生命周期累积，单次运行 ~460 本
# 远低于上限（实际影响极低），上限防未来多榜/长驻复用场景无界增长。
_DETAIL_CACHE = {}
_DETAIL_CACHE_MAX = 2000


def _detail_cache_put(key: str, value):
    """写入缓存；超上限按插入序淘汰最旧条目（dict 3.7+ 保插入序，FIFO 近似 LRU）。"""
    if len(_DETAIL_CACHE) >= _DETAIL_CACHE_MAX:
        for _k in list(_DETAIL_CACHE)[: len(_DETAIL_CACHE) - _DETAIL_CACHE_MAX + 1]:
            del _DETAIL_CACHE[_k]
    _DETAIL_CACHE[key] = value


def fetch_novel_detail(novel_id):
    """从 novelbasicinfo 详情接口补积分/字数/收藏/简介等。返回 dict 或 None。

    C3: 命中缓存直接复用。N24: 缓存策略按失败类型区分——
    - 确定性失败（HTTP 404/400/410 = 书已下架/不存在）缓存 None，避免跨分类对
      同一 novelId 反复请求；
    - 瞬时失败（503/5xx/超时/网络错误/JSON 解析失败）**不缓存**，同书后续重试
      还能补回积分，避免整轮运行积分大面积记"未知"。
    - N4 原逻辑保留：503 熔断期内失败同样不缓存。
    """
    key = str(novel_id)
    if key in _DETAIL_CACHE:
        return _DETAIL_CACHE[key]
    url = URL_DETAIL.format(novel_id=key)
    d, err_code = api_get(url, timeout=20)
    if not isinstance(d, dict):
        d = None
    # 确定性失败（书不存在/已下架）：缓存 None
    definitive = isinstance(err_code, int) and err_code in (400, 404, 410)
    if d is None and not definitive:
        # 瞬时失败（含 503 熔断期）不缓存，留待后续重试
        return None
    _detail_cache_put(key, d)
    return d


class JjwxcScraper(BaseScraper):
    platform = "jjwxc"
    platform_name = "晋江文学城"
    # 晋江是 HTTP 爬虫，不用浏览器，headless 不影响

    def scrape_categories(self, page, rank, completed=None, all_categories=None,
                          output_file=None, state_file=None, **kwargs):
        """App API 路线：按完整分类树遍历各频道的月榜。

        分类树来自 platforms.json 的 channels（6 频道）+ subcategories（22 子分类）。
        每个频道/子分类请求 getFullPageV1，取月榜 list（~20 本），再补详情页积分/字数。
        断点续传：completed 用 'channel_yq'/'sub_gywx' 标记已抓分类。
        """
        from config.loader import load_config
        cfg = load_config()["platforms"].get("jjwxc", {})
        channels = cfg.get("channels", {})            # {channel_code: 频道名}
        subcategories = cfg.get("subcategories", {})  # {parent_code: [{channel,name}]}

        completed = completed or []
        all_categories = all_categories or []
        # all_categories 槽位存已抓的分组结构 [{name, books}]，断点续传还原
        existing_names = {c["name"] for c in all_categories}

        # 构建任务清单：(分类名, channel_code, 标记key)
        # C2: 不同父分类下可能出现同一 channel（ysnocp/trdm/trys/trmd 同时挂在
        # nocp_plus/noyq/yq 与 ys 下），getFullPageV1 按 channel 取数，同一 channel
        # 内容完全相同，重复抓纯属浪费。按 channel 去重：保留先出现的（yq/noyq/
        # nocp_plus），跳过后出现的（ys）重复 channel；ys 频道自身仍作为独立频道任务
        # 保留（channel_ys），其下非重复子分类也照常保留。
        tasks = []
        seen_channels = set()
        skipped_dups = []
        for code, name in channels.items():
            tasks.append((name, code, f"channel_{code}"))
        for parent, subs in subcategories.items():
            for sub in subs:
                ch = sub["channel"]
                if ch in seen_channels:
                    skipped_dups.append((sub["name"], ch, parent))
                    continue
                seen_channels.add(ch)
                tasks.append((sub["name"], ch, f"sub_{ch}_{parent}"))

        if skipped_dups:
            print(f"  ⏭️ 跳过 {len(skipped_dups)} 个重复 channel："
                  f"{', '.join(f'{n}({ch}@{p})' for n, ch, p in skipped_dups)}")
        print(f"  📚 晋江分类树：{len(channels)} 频道 + {len(seen_channels)} 子分类（按 channel 去重） = {len(tasks)} 个")

        def _upsert_category(name, books_list):
            """N42: 空占位可被覆盖；同名槽位 in-place 更新。"""
            for i, c in enumerate(all_categories):
                if c.get("name") == name:
                    all_categories[i] = {"name": name, "books": books_list}
                    return
            all_categories.append({"name": name, "books": books_list})
            existing_names.add(name)

        for cat_name, channel_code, mark in tasks:
            if mark in completed:
                print(f"  ⏭️ 跳过已抓 {cat_name}")
                continue
            # N42: 仅跳过「已有非空 books」；空占位必须允许重试
            if cat_name in existing_names:
                prev = next(
                    (c for c in all_categories if c.get("name") == cat_name),
                    None,
                )
                if prev and (prev.get("books") or []):
                    print(f"  ⏭️ 跳过已存在非空分类 {cat_name}")
                    continue
                print(f"  🔄 [{cat_name}] 空占位，重新抓取")

            print(f"\n  🔍 [{cat_name}] channel={channel_code}")
            books = self._fetch_category_books(channel_code)
            if not books:
                # N16: 写空分类占位，避免次日被趋势判「全掉榜」；不 mark completed 以便重试
                print(f"  ⚠️ [{cat_name}] 月榜为空（可能反爬/无数据），写空占位")
                _upsert_category(cat_name, [])
                if output_file and state_file:
                    snapshot = self.build_snapshot(rank, all_categories)
                    write_json(output_file, snapshot)
                    write_json(state_file, {"completed": completed})
                time.sleep(REQUEST_DELAY)
                continue

            # 补详情页积分/字数
            print(f"  📝 补 {len(books)} 本详情...")
            cat_books = []
            for i, b in enumerate(books):
                novel_id = str(b.get("novelId", ""))
                if not novel_id:
                    continue
                detail = fetch_novel_detail(novel_id)
                word_count = ""
                intro = b.get("novelIntroShort") or "暂无简介"
                # N20: 详情失败与真零分区分——失败用 null + 可读提示，不静默写 0
                if detail:
                    score = parse_score(detail.get("novelScore", 0))
                    reads_str = format_score(score)
                    wc_raw = detail.get("novelSize", "")
                    if wc_raw:
                        word_count = str(wc_raw).replace(",", "") + "字"
                    if detail.get("novelIntro"):
                        intro = detail["novelIntro"][:300]
                else:
                    score = None
                    reads_str = "未知"
                    print(f"    ⚠️ 详情失败 novelId={novel_id}《{b.get('novelName', '')}》，积分记未知")

                cat_books.append({
                    "title": b.get("novelName", ""),
                    "author": b.get("authorName", ""),
                    "reads": reads_str,
                    "metric_type": "score",
                    "score": score,
                    "intro": intro,
                    "cover": b.get("cover", "") or b.get("ebookurl", ""),
                    "url": f"https://www.jjwxc.net/onebook.php?novelid={novel_id}",
                    "_tags": [cat_name],
                    "_channel": cat_name,
                    "_word_count": word_count,
                    "_rank": i + 1,
                    "_detail_ok": detail is not None,
                })
                if i < len(books) - 1:
                    time.sleep(DETAIL_DELAY)
                if (i + 1) % 10 == 0 and i < len(books) - 1:
                    print(f"    ...已完成 {i+1}/{len(books)}")
                    time.sleep(2.0)

            # N63: books 非空但全部无 novelId → cat_books 空，与 list 空同路径，不 mark
            if not cat_books:
                print(f"  ⚠️ [{cat_name}] 无有效 novelId，写空占位（不 mark completed）")
                _upsert_category(cat_name, [])
                if output_file and state_file:
                    snapshot = self.build_snapshot(rank, all_categories)
                    write_json(output_file, snapshot)
                    write_json(state_file, {"completed": completed})
                time.sleep(REQUEST_DELAY)
                continue

            _upsert_category(cat_name, cat_books)
            completed.append(mark)
            print(f"  ✅ [{cat_name}] {len(cat_books)} 本，累计 {sum(len(c['books']) for c in all_categories)} 本")

            # 逐分类增量保存（崩溃安全）
            if output_file and state_file:
                snapshot = self.build_snapshot(rank, all_categories)
                write_json(output_file, snapshot)
                write_json(state_file, {"completed": completed})

            time.sleep(REQUEST_DELAY)

        return all_categories

    def _fetch_category_books(self, channel_code):
        """请求 getFullPageV1，提取月榜 list 书列表。"""
        url = URL_FULLPAGE.format(channel=channel_code)
        d, _err = api_get(url, timeout=30)
        if not d:
            return []
        books = extract_monthly_rank(d)
        print(f"    月榜 list: {len(books)} 本")
        return books

    def _load_state(self, output_file, state_file):
        """重写：晋江断点续传从 state_file 读 completed，all_categories 从 output_file 读分组结构。
        N64: 与 base 同构——output 缺失/读失败/空 categories → 清空 completed。
        N75: 交叉验证——剔除 state 标记已完成但 output 实际无对应分类的幽灵 mark，
        避免该分类被永久跳过（仿 scrape_fanqie_ranks.py:136-141）。"""
        completed = []
        all_categories = []
        if os.path.exists(state_file):
            try:
                with open(state_file, encoding="utf-8") as f:
                    completed = json.load(f).get("completed", [])
            except (OSError, json.JSONDecodeError):
                pass
        if completed:
            if not os.path.exists(output_file):
                print(f"⚠️ output 不存在但 state 有 completed，清空 completed 避免永久跳过: {output_file}")
                completed = []
            else:
                try:
                    with open(output_file, encoding="utf-8") as f:
                        all_categories = json.load(f).get("categories", [])
                except (OSError, json.JSONDecodeError) as e:
                    print(f"⚠️ output 读取失败，清空 completed: {output_file} ({e})")
                    completed = []
                    all_categories = []
                if completed and not all_categories:
                    print(f"⚠️ output 无 categories 但 state 有 completed，清空 completed: {output_file}")
                    completed = []
                elif completed and all_categories:
                    # N75: 晋江 completed 存 channel_{code}/sub_{channel}_{parent} mark
                    # （非分类名），需先从 platforms.json 构建 mark→name 映射，再与
                    # all_categories 的 name 比对，剔除「state 标记已完成但 output 无对应
                    # 分类」的幽灵 mark，避免该分类永久跳过。
                    try:
                        from config.loader import load_config
                        cfg = (load_config().get("platforms", {})
                               .get("jjwxc", {}))
                        channels = cfg.get("channels", {})
                        subcategories = cfg.get("subcategories", {})
                        mark_to_name = {}
                        for code, name in channels.items():
                            mark_to_name[f"channel_{code}"] = name
                        for parent, subs in subcategories.items():
                            for sub in subs:
                                mark_to_name[f"sub_{sub['channel']}_{parent}"] = sub["name"]
                    except Exception as e:
                        print(f"⚠️ 加载 jjwxc 配置失败，跳过 completed 交叉验证: {e}")
                        mark_to_name = None

                    if mark_to_name is not None:
                        existing_names = {c.get("name") for c in all_categories}
                        filtered = []
                        for m in completed:
                            name = mark_to_name.get(m)
                            if name is None:
                                # mark 在当前配置中已不存在（配置漂移），
                                # 保留无副作用（task 不会再生成此 mark）
                                filtered.append(m)
                                continue
                            if name not in existing_names:
                                print(f"⚠️ state 标记 {m} 已完成但 output 无分类 [{name}]，"
                                      f"剔除避免永久跳过: {output_file}")
                                continue
                            filtered.append(m)
                        completed = filtered
        return completed, all_categories

    @single_run("scrape-jjwxc")
    def run(self):
        """重写：晋江无需 Playwright 浏览器，直接调 scrape_rank（page=None）。
        N27: 业务失败累计后 raise SystemExit(1)。"""
        from scrapers.base import iter_platform_ranks
        failed = []
        for rank in iter_platform_ranks(self.platform):
            try:
                self.scrape_rank(None, rank)
            except Exception as e:
                import traceback
                print(f"❌ 抓取 {rank['slug']} 失败：{e}")
                traceback.print_exc()
                failed.append(rank["slug"])
        if failed:
            print(f"\n❌ 晋江失败榜单：{', '.join(failed)}（共 {len(failed)}）")
            # INFO: 库函数内不用 sys.exit，改为 raise SystemExit 由 __main__/调用方统一处理
            raise SystemExit(1)


if __name__ == "__main__":
    print("开始抓取晋江文学城积分月榜（App API，完整分类树）...")
    JjwxcScraper(limit=100).run()
