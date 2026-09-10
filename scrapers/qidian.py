"""起点中文网榜单爬虫（每日畅销榜 + 签约新书榜 + 新人签约新书榜 + 三江榜）。

- patchright 引擎（playwright 反检测分支）+ 本机真实 Chrome + headless=True 无头反检测
  （本地历史测试曾成功获取正常页面，不保证后续页面可用；
  QD_HEADED=1 可临时切回有头排查）+ 资源拦截加速详情补全
- 主榜（hotsales/signnewbook/newsign）**按分类榜单 URL** 逐个抓取：
  遍历 config.platforms.json 的 chan_ids，每分类抓 {url}chn{chanId}/ 翻页到
  PER_CATE 本，抓取顺序即该分类榜单真实排名；分类名由 URL 决定，无需 primary_tag 推断。
  起点支持 `/rank/{hotsales|signnewbook|newsign}/chn{chanId}/` 分类筛选，
  冷门分类（体育/军事/现实等）也能抓满 30 本，且位次正确。
- 三江榜（sanjiang，配置 no_category=true 分流）独立抓取：页面结构为
  strongrec-wrap → li.strongrec-list（每期一个块，含 date-range）→ .book-list ul li，
  只取最新一期（约 30 本），每本书仅有书名/频道/推荐语。
- 四榜名单优先保存，再按书籍 ID 合并详情任务；缓存保留原采集时间
- 网络断开、HTTP 限流与连续验证码触发持久化冷却，重启也生效
- 逐分类增量保存 + 状态恢复（中断后重跑自动跳过已抓分类，标记 chan_{分类名}）

输出：data/qidian_{rank_id}_ranks_YYYYMMDD.json
"""
import json
import math
import os
import random
import re
import sys
import time
from datetime import datetime
from email.utils import parsedate_to_datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.base import BaseScraper, iter_platform_ranks, write_json
from scrapers.network_errors import NetworkUnavailable, network_failure
from scrapers.qidian_html import parse_detail, parse_rank, parse_sanjiang, to_book
from scrapers.qidian_state import CHINA_TZ, QidianState, observed_now, observed_timestamp
from scripts import runtime_state
from scripts.runtime_state import single_run
from scripts.snapshot_utils import snapshot_complete, snapshot_quality

PER_CATE = 30    # 每分类目标抓取本数（榜单每页 20 本，2 页凑 30+）
CATE_PAGES = 2   # 每分类抓取页数
# 详情补全节奏（20260908 快节奏实验失败，回退 09-07 已验证值）：
# 历史两次运行曾在约第 72 本详情时出现验证码，仅是当时观测，不能据此
# 推导网站固定请求阈值。保留较慢节奏，配合缓存、请求预算和持久化冷却。
DETAIL_GOTO_SLEEP = (1.8, 3.0)   # 详情页加载后等待（等 JS 渲染稳定）
DETAIL_BOOK_GAP = (4.0, 7.0)     # 书间间隔（IP 频率风控的主要缓冲）
DETAIL_BATCH_SIZE = 10           # 每批落盘 + 长休眠的本数
DETAIL_BATCH_PAUSE = (60, 120)   # 批间长休眠（小批量+长歇不易触发风控）


class IpBlockedError(RuntimeError):
    """A site block requires a persistent cooldown before another crawl."""


class DetailBudgetReached(RuntimeError):
    """The explicitly configured detail navigation budget has been consumed."""


def _rand_sleep(low: float, high: float):
    """随机区间休眠，模拟人类操作节奏，降低被风控概率。"""
    time.sleep(random.uniform(low, high))


def _is_captcha_page(page, use_fallback: bool = True) -> bool:
    """检测是否被起点反爬拦截到腾讯验证码页（headless 或高频访问会触发）。

    主检测：HTML 含 captcha.qq.com / TencentCaptcha。
    辅助检测（use_fallback=True）：榜单 li 与详情页 .count em/.book-info em 均空
    且 html 较小 → 疑似验证码空壳页。

    use_fallback=False 用于榜单页/三江页等「空榜页正常」场景：冷门分类榜可能
    不足一页（如武侠签约新书榜仅 9 本），第 2 页是正常空榜页（HTML 完整含导航、
    约 34KB），辅助检测会将其误判为验证码并中断整榜。空榜页由调用方按「空页
    break」处理即可。详情页（无 em 属异常）保留辅助检测。
    """
    try:
        html = page.content()
        if "captcha.qq.com" in html or "TencentCaptcha" in html:
            return True
        if not use_fallback:
            return False
        # 辅助检测：榜单页 li 和详情页 .count em 均无，且 html 较小 → 验证码空壳页。
        # 保留 N76 核心修复（不再依赖 html<25000，>25KB 验证码页也能检出），
        # 同时加 60000 下限修复 P2：榜单页 :229 调用点 em 恒为 0（详情页专用选择器，
        # 见 _fetch_word_count），辅助检测退化为 li==0 判定；若慢渲染使 li 未挂载，
        # 正常大体积榜单页（含完整框架+脚本，html 常大于 60KB）会被误判验证码并 raise
        # 中止本榜抓取。加 html<60000 区分：验证码空壳页 html 通常 <60KB；慢渲染正常
        # 榜单页 html 通常 >60KB；正常榜单页 li>0 早返回 False，不进此分支。
        li_count = page.eval_on_selector_all('.book-img-text ul li', 'els => els.length')
        em_count = page.eval_on_selector_all('.count em, .book-info em', 'els => els.length')
        if li_count == 0 and em_count == 0 and len(html) < 60000:
            return True
    except Exception:
        pass
    return False


class QidianScraper(BaseScraper):
    platform = "qidian"
    platform_name = "起点中文网"
    # 沿用历史测试采用的 patchright + Chrome；QD_HEADED=1 可切回有头排查。
    engine = "patchright"
    headless = True
    # 拦截图片/字体/样式表等非数据资源：详情补全每日 ~1000 本，单页加载 6-10s → ~3s
    block_resources = True
    # IP 级风控为致命错误：命中即中止整个平台（base.run 钩子），勿继续下一榜空转
    fatal_error_types = (IpBlockedError,)

    def __init__(self, limit=30, sleep_sec=5.0):
        super().__init__(limit, sleep_sec)
        self._state = None
        self._lists_only = False
        self._detail_checkpoint = None
        self._detail_stop_reason = ""
        self._cache_hit_keys = set()
        self._stats = {"rank_requests": 0, "detail_requests": 0, "cache_hits": 0, "detail_failures": 0}
        self.detail_ttl_hours = self._number_option("QD_DETAIL_TTL_HOURS", 24)
        self.max_detail_requests = int(self._number_option("QD_DETAIL_MAX_REQUESTS", 0))
        self.cooldown_seconds = max(60, self._number_option("QD_COOLDOWN_SECONDS", 3600))

    @staticmethod
    def _number_option(name, default):
        try:
            value = float(os.environ.get(name, default))
            if not math.isfinite(value) or value < 0:
                raise ValueError
            return value
        except (TypeError, ValueError):
            print(f"[WARN] {name} 应为非负数，使用默认值 {default}")
            return default

    @staticmethod
    def _book_key(raw):
        bid = str(raw.get("bookId") or "")
        if bid.isdigit():
            return bid
        match = re.search(r"/book/(\d+)", raw.get("bookUrl") or "")
        return match[1] if match else ""

    def _block_site(self, reason, seconds=None):
        if self._state is not None:
            self._state.defer(seconds if seconds is not None else self.cooldown_seconds, reason)
        raise IpBlockedError(reason)

    def _navigate(self, page, url, *, detail=False, timeout=60000):
        if detail and self.max_detail_requests and self._stats["detail_requests"] >= self.max_detail_requests:
            raise DetailBudgetReached("详情请求预算已用完，剩余详情可在下次运行补全")
        self._stats["detail_requests" if detail else "rank_requests"] += 1
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        except Exception as exc:
            if failure := network_failure(exc):
                if self._state is not None:
                    self._state.defer(300, str(failure))
                raise failure from exc
            raise
        if response is not None and response.status in (403, 429, 503):
            delay = self.cooldown_seconds
            retry_after = response.headers.get("retry-after", "")
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                try:
                    delay = max(delay, parsedate_to_datetime(retry_after).timestamp() - time.time())
                except (TypeError, ValueError, OverflowError):
                    pass
            self._block_site(f"起点返回 HTTP {response.status}，已进入冷却", delay)
        if response is not None and response.status >= 400:
            raise OSError(f"HTTP {response.status}")
        return response

    @single_run("scrape-qidian")
    def run(self):
        """Collect every rank first, then refresh a shared queue of book details."""
        self._stats = {"rank_requests": 0, "detail_requests": 0, "cache_hits": 0, "detail_failures": 0}
        self._cache_hit_keys.clear()
        self._detail_stop_reason = ""
        self._run_date_str = datetime.now(CHINA_TZ).strftime("%Y%m%d")
        self._state = QidianState(runtime_state.RUNTIME_DIR / "qidian.sqlite3")
        jobs = []
        failed = []
        stop_reason = ""
        exit_code = 0
        try:
            remaining, reason = self._state.remaining_cooldown()
            if remaining:
                print(f"⏸️ 起点冷却中，剩余 {remaining} 秒（{reason}）；本次不启动浏览器")
                raise SystemExit(75)
            self._state.prune()
            headed = os.environ.get("QD_HEADED", "").strip().lower() in ("1", "true", "yes", "on")
            with self._sync_playwright()() as driver:
                browser, context, page = self.launch_browser(driver, headless=self.headless and not headed)
                try:
                    self._lists_only = True
                    for rank in iter_platform_ranks(self.platform):
                        output, state = self._paths(rank)
                        completed, raw = self._load_state(output, state)
                        try:
                            self.scrape_categories(page, rank, completed=completed, all_categories=raw,
                                                   output_file=output, state_file=state)
                        except (IpBlockedError, NetworkUnavailable):
                            raise
                        except Exception as exc:
                            print(f"⚠️ {rank['slug']} 名单抓取失败：{exc}")
                            failed.append(rank["slug"])
                        completed, raw = self._load_state(output, state)
                        if not snapshot_complete(output):
                            failed.append(rank["slug"])
                        jobs.append((rank, output, state, set(completed), raw))
                    self._lists_only = False

                    def checkpoint():
                        for rank, output, state, marks, raw in jobs:
                            self._save_qidian_progress(output, state, rank, raw, marks)

                    self._detail_checkpoint = checkpoint
                    self._fetch_details(page, [row for job in jobs for row in job[4]], None, None, {}, set())
                finally:
                    # Keep successful enrichment even when a later request is blocked.
                    try:
                        if self._detail_checkpoint:
                            self._detail_checkpoint()
                    finally:
                        browser.close()
        except (IpBlockedError, NetworkUnavailable) as exc:
            stop_reason = str(exc)
            exit_code = 75
            print(f"⏸️ {exc}；榜单名单与已完成详情已保留")
        finally:
            self._lists_only = False
            self._detail_checkpoint = None
            remaining, reason = self._state.remaining_cooldown()
            try:
                write_json(str(runtime_state.RUNTIME_DIR / "qidian_status.json"), {
                    "finished_at": observed_now(), "date": self._run_date_str,
                    "stats": self._stats, "failed_ranks": sorted(set(failed)),
                    "stop_reason": stop_reason or self._detail_stop_reason,
                    "cooldown_seconds_remaining": remaining, "cooldown_reason": reason,
                })
            finally:
                self._state.close()
                self._state = None
        print(f"起点请求统计：{self._stats}")
        if exit_code or failed:
            raise SystemExit(exit_code or 1)

    def _chan_ids(self):
        """14 个主分类的 chanId 映射（顺序=分类展示顺序）。"""
        from config.loader import load_config
        return load_config()["platforms"].get("qidian", {}).get("chan_ids", {})

    def _load_state(self, output_file, state_file):
        """重写：起点断点续传从 state_file 的 raw 字段还原原始记录列表（all_categories 槽位）。
        base._load_state 读 categories 字段，但起点 latest 的 categories 是分组结构，
        续传需还原扁平 raw 列表，故从 state 的 raw 字段读。
        N64: completed 非空但 raw 空 → 清空 completed，避免永久 skip 页。
        """
        completed = []
        all_raw = []
        if os.path.exists(state_file):
            try:
                with open(state_file, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    completed = data.get("completed", [])
                    all_raw = data.get("raw", []) or []
            except (OSError, json.JSONDecodeError):
                pass
        completed = [c for c in completed if isinstance(c, str)] if isinstance(completed, list) else []
        all_raw = [r for r in all_raw if isinstance(r, dict)] if isinstance(all_raw, list) else []
        if completed and not all_raw:
            print(f"⚠️ state 有 completed 但 raw 为空，清空 completed 避免永久跳过: {state_file}")
            completed = []
        # Old state files record the snapshot day, not an exact observation time.
        match = re.search(r"_(\d{4})(\d{2})(\d{2})\.json$", str(state_file))
        for raw in all_raw:
            if raw.get("wordCount") and not raw.get("_word_count_observed_at") and match:
                raw["_word_count_observed_at"] = "-".join(match.groups())
                raw["_word_count_source"] = "legacy"
        return completed, all_raw

    def _apply_cached_details(self, all_raw):
        if self._state is None:
            return
        for raw in all_raw:
            key = self._book_key(raw)
            if not key:
                continue
            cached = self._state.get(key)
            if cached:
                if not raw.get("wordCount") or observed_timestamp(cached.get("_word_count_observed_at")) > observed_timestamp(raw.get("_word_count_observed_at")):
                    for field in ("wordCount", "_word_count_observed_at"):
                        raw[field] = cached[field]
                    raw["_word_count_source"] = "cache"
                    self._cache_hit_keys.add(key)
                for field in ("author", "intro"):
                    if not raw.get(field) and cached.get(field):
                        raw[field] = cached[field]
            self._state.put(key, raw)
        self._stats["cache_hits"] = len(self._cache_hit_keys)

    def _save_qidian_progress(self, output_file, state_file, rank, all_raw, done_marks,
                              empty_categories=None):
        """起点专用增量保存：latest 写分组后的标准 schema（{name,books}），
        state 额外存 raw 原始记录列表（每本带 _cate），供断点续传还原。

        主榜按 chan_ids 顺序分组；三江榜（no_category）按 _cate 出现顺序分组。
        empty_categories：抓取失败的空分类占位名列表（N22），追加进 categories，
        避免空分类在最终快照中缺失、下游趋势判「未收录」而非「当日无数据」。
        """
        self._apply_cached_details(all_raw)
        expected = ["三江推荐"] if rank.get("no_category") else list(self._chan_ids())
        missing = [name for name in expected if f"chan_{name}" not in done_marks]
        empty_categories = sorted(set(empty_categories or []) | set(missing))
        if rank.get("no_category"):
            categories = self._group_by_cate(all_raw, None)
        else:
            categories = self._group_by_cate(all_raw, self._chan_ids())
        if empty_categories:
            have = {c["name"] for c in categories}
            for name in empty_categories:
                if name not in have:
                    categories.append({"name": name, "books": []})
        snapshot = self.build_snapshot(rank, categories)
        snapshot["schema_version"] = 2
        snapshot["collected_at"] = observed_now()
        snapshot["quality"] = {
            "rank_status": "partial" if missing else "complete",
            "expected_categories": len(expected),
            "completed_categories": len(expected) - len(missing),
            "missing_categories": missing,
        }
        snapshot["quality"] = snapshot_quality(snapshot)
        write_json(output_file, snapshot)
        write_json(state_file, {"completed": sorted(done_marks), "raw": all_raw})

    def _fetch_detail(self, page, book_url, book_id):
        """从详情页抓总字数（起点榜单页无阅读量/月票明文，详情页 .count em 有字数）
        及作者/简介（三江榜页面无此二字段，从详情页补全）。
        返回 (word_count, author, intro, is_captcha) 四元组：
          - word_count: 带"万字"后缀的字符串或空串（空串=普通失败，可重试/留空）
          - author/intro: 详情页作者/简介（可能为空）
          - is_captcha: True 表示详情页被验证码拦截（由调用方累计计数，连续多本
            才中止整榜，避免单本误判/间歇风控拖垮整榜）
        含 2 次重试 + 指数退避。
        """
        url = book_url or f"https://www.qidian.com/book/{book_id}/"
        for attempt in range(3):
            try:
                self._navigate(page, url, detail=True, timeout=30000)
                _rand_sleep(*DETAIL_GOTO_SLEEP)
                # C13: 先等字数元素挂载；正常详情页等待后即有内容
                try:
                    page.wait_for_selector('.count em, .book-info em', timeout=5000)
                except Exception:
                    # 元素未出现：区分「验证码拦截」与「普通失败」
                    if _is_captcha_page(page):
                        return "", "", "", True
                    if attempt < 2:
                        wait = 3 * (attempt + 1)
                        print(f"    [WARN] 详情页字数元素超时 {book_id}，{wait}s 后重试 {attempt+1}/2")
                        time.sleep(wait)
                        continue
                    print(f"    [WARN] 详情页字数元素最终超时 {book_id}")
                    return "", "", "", False
                val = parse_detail(page.content())
                wc = val.get("wc", "") if isinstance(val, dict) else ""
                author = val.get("author", "") if isinstance(val, dict) else ""
                intro = val.get("intro", "") if isinstance(val, dict) else ""
                # N25: 元素已挂载但取空时重试，降低字数静默缺失率
                if wc:
                    return wc, author, intro, False
                if attempt < 2:
                    wait = 3 * (attempt + 1)
                    print(f"    [WARN] 详情页字数取空 {book_id}，{wait}s 后重试 {attempt+1}/2")
                    time.sleep(wait)
                    continue
                print(f"    [WARN] 详情页字数最终取空 {book_id}")
                return wc, author, intro, False
            except (IpBlockedError, NetworkUnavailable, DetailBudgetReached):
                raise
            except Exception as e:
                if attempt < 2:
                    wait = 3 * (attempt + 1)
                    print(f"    [WARN] 详情页失败 {book_id}，{wait}s 后重试 {attempt+1}/2: {e}")
                    time.sleep(wait)
                else:
                    print(f"    [WARN] 详情页最终失败 {book_id}: {e}")
                    return "", "", "", False
        return "", "", "", False

    def scrape_categories(self, page, rank, completed=None, all_categories=None,
                          output_file=None, state_file=None, **kwargs):
        """主榜按分类榜单 URL 逐个抓取；三江榜（no_category）走 _scrape_sanjiang。

        断点续传：completed 存已抓的分类标记（chan_{分类名}），重跑跳过已抓分类；
        每分类抓完增量保存（崩溃安全），半截分类的 raw 丢弃重抓（粒度=分类级）。
        """
        completed = completed or []
        all_raw = list(all_categories) if all_categories else []
        done_marks = {
            c for c in completed
            if isinstance(c, str) and (c.startswith("page") or c.startswith("chan_"))
        }

        # 三江榜分流（不分类总榜）
        if rank.get("no_category"):
            return self._scrape_sanjiang(page, rank, all_raw, done_marks,
                                         output_file, state_file)

        chan_ids = self._chan_ids()
        if not chan_ids:
            print("  ⚠️ 未配置 chan_ids，无法按分类抓取起点榜单")
            return []
        done_chan = {c[len("chan_"):] for c in done_marks if c.startswith("chan_")}
        # P3-7: 只保留已完成分类的 raw；无 _cate（旧格式残留）或未完成分类一律丢弃重抓，
        # 避免旧格式 state 残留混入 _group_by_cate 的"未分类"
        all_raw = [r for r in all_raw if r.get("_cate") in done_chan]

        base = (rank.get("url") or "https://www.qidian.com/rank/hotsales/").rstrip("/") + "/"
        rank_name = rank["name"]
        print(f"\n🚀 开始抓取{self.platform_name}·{rank_name}（按 {len(chan_ids)} 个分类榜单，每分类目标 {PER_CATE} 本）")

        empty_placeholders = []
        chan_names = list(chan_ids)
        # 连续分类验证码触发持久化冷却，重启进程也不立即重新访问。
        consecutive_captcha = 0
        for idx, (name, chan_id) in enumerate(chan_ids.items()):
            mark = f"chan_{name}"
            if mark in done_marks:
                print(f"  ⏭️ 跳过已抓分类 [{name}]")
                continue
            try:
                cat_raw = self._scrape_rank_category(page, base, chan_id, name)
            except RuntimeError as e:
                # 分类页验证码截断：写空占位但不 mark（下次可续传重试），不中止整榜；
                # 连续 3 个分类验证码时保存进度，持久化冷却并退出。
                if "验证码" not in str(e):
                    raise
                consecutive_captcha += 1
                print(f"  ⛔ [{name}] 分类页验证码截断（连续 {consecutive_captcha}/3），写空占位跳过")
                if consecutive_captcha >= 3:
                    empty_placeholders.append(name)
                    if output_file and state_file:
                        self._save_qidian_progress(output_file, state_file, rank, all_raw, done_marks,
                                                   empty_categories=empty_placeholders)
                    self._block_site("连续 3 个分类页出现验证码，暂停起点请求")
                empty_placeholders.append(name)
                if output_file and state_file:
                    self._save_qidian_progress(
                        output_file, state_file, rank, all_raw, done_marks,
                        empty_categories=empty_placeholders)
                _rand_sleep(8, 15)
                continue
            if not cat_raw:
                # 抓取失败/空榜：写空占位但不 mark 完成（下次可重试），避免丢失该分类
                print(f"  ⚠️ [{name}] 抓取失败/空，写空占位")
                empty_placeholders.append(name)
                _rand_sleep(8, 15)
            else:
                for r in cat_raw:
                    r["_cate"] = name
                all_raw.extend(cat_raw)
                done_marks.add(mark)
                consecutive_captcha = 0   # 单个分类成功 → 连续验证码计数清零
            if output_file and state_file:
                self._save_qidian_progress(output_file, state_file, rank, all_raw, done_marks,
                                           empty_categories=empty_placeholders)
            print(f"  ✅ [{name}] 抓 {len(cat_raw)} 本，累计 {len(all_raw)} 本")
            if idx < len(chan_names) - 1:
                _rand_sleep(5, 10)   # 分类间长 sleep

        print(f"\n📖 总计: {len(all_raw)} 本 / {len(chan_ids)} 分类")
        if not all_raw:
            print("⚠️ 未抓到任何数据（可能被反爬）")
            return []

        # 补充详情页字数（榜单页无字数明文，详情页有；三江榜另补作者/简介）
        # P2-0902#3: 透传 empty_placeholders，详情阶段的中间保存不冲掉分类阶段的空占位
        self._fetch_details(page, all_raw, output_file, state_file, rank, done_marks,
                            empty_placeholders=empty_placeholders)

        if output_file and state_file:
            self._save_qidian_progress(output_file, state_file, rank, all_raw, done_marks,
                                       empty_categories=empty_placeholders)
        return self._group_by_cate(all_raw, chan_ids)

    def _scrape_rank_category(self, page, base, chan_id, name):
        """抓单个分类榜单页：{base}chn{chanId}/ 翻页至 PER_CATE 本（抓取顺序=榜单排名）。

        空页即停（冷门分类榜可能不足 PER_CATE 本，属正常）。返回该分类的 raw 列表。
        """
        url = f"{base}chn{chan_id}/"
        cat_raw = []
        for page_num in range(1, CATE_PAGES + 1):
            if len(cat_raw) >= PER_CATE:
                break
            page_url = url if page_num == 1 else f"{url}page{page_num}/"
            print(f"    📄 [{name}] 第{page_num}页: {page_url}")

            got_page = False
            for attempt in range(3):
                try:
                    self._navigate(page, page_url)
                    _rand_sleep(2.5, 4.0)
                    # 榜单页空榜页正常（冷门分类书少），仅主检测，避免把空榜页误判验证码
                    if _is_captcha_page(page, use_fallback=False):
                        # N57: 验证码截断视为业务失败（已抓部分由外层增量保存）
                        raise RuntimeError(
                            f"{name} 分类榜单第{page_num}页验证码拦截（本分类已抓 {len(cat_raw)} 本）"
                        )
                    got_page = True
                    break
                except RuntimeError:
                    raise
                except Exception as e:
                    if attempt < 2:
                        wait = 5 * (attempt + 1)
                        print(f"    ⚠️ 访问失败重试 {attempt+1}/3，{wait}s 后: {e}")
                        time.sleep(wait)
                    else:
                        print(f"    ❌ 第{page_num}页访问失败，跳过该页")
            if not got_page:
                # 完成标记以分类为单位；任一页失败都不能把截断分类 mark done。
                # 下次从第一页重抓，避免尾页缺失永久固化，或位次从第二页重新编号。
                print(f"    ❌ [{name}] 第 {page_num} 页访问失败，放弃该分类（空占位，可续传）")
                return []

            try:
                page.wait_for_selector('.book-img-text li, .rank-body li, .book-list li', timeout=10000)
            except Exception:
                print("    ⚠️ 等待选择器超时，延时后继续")
            _rand_sleep(1.5, 2.5)

            try:
                books = parse_rank(page.content())
            except Exception as e:
                print(f"    ⚠️ 提取失败: {e}")
                return []  # 提取异常与正常空榜不同，保留分类重试机会
            print(f"    抓到: {len(books)} 本")
            if not books:
                print(f"    ⚠️ 空页，结束该分类（已抓 {len(cat_raw)} 本）")
                break
            for book in books:
                if book.get("wordCount"):
                    book["_word_count_observed_at"] = observed_now()
                    book["_word_count_source"] = "rank"
            cat_raw.extend(books)
            if page_num < CATE_PAGES and len(cat_raw) < PER_CATE:
                _rand_sleep(3.0, 6.0)   # 翻页间长 sleep
        return cat_raw[:PER_CATE]

    def _scrape_sanjiang(self, page, rank, all_raw, done_marks, output_file, state_file):
        """三江榜：不分类总榜，取最新一期 strongrec-list 块（约 30 本）。

        页面每本书仅有书名/频道/推荐语，作者/简介/字数从详情页补全（_fetch_details）。
        categories 单组 [{name: "三江推荐", books}]。断点续传标记 chan_三江推荐。
        """
        mark = "chan_三江推荐"
        if mark in done_marks:
            print("  ⏭️ 三江榜列表已抓，跳过列表抓取")
            # P2-9(0826): mark 仅代表列表阶段完成——上次运行若在详情补全中途被中断，
            # raw 里的书缺 wordCount/作者/简介（三江页面无此三字段，全靠详情页补），
            # 直接 return 会导致重跑永不补详情。续传时对缺字数的书补跑详情补全。
            self._fetch_details(page, all_raw, output_file, state_file, rank, done_marks)
            if output_file and state_file:
                self._save_qidian_progress(output_file, state_file, rank, all_raw, done_marks)
            return self._group_by_cate(all_raw, None)
        url = rank.get("url", "https://www.qidian.com/sanjiang/")
        print(f"\n🚀 开始抓取{self.platform_name}·{rank['name']}（最新一期）: {url}")

        # 单页总榜出现验证码，保留占位并进入持久化冷却。
        got_page = False
        attempt = 0
        while not got_page:
            try:
                self._navigate(page, url)
                _rand_sleep(3.0, 5.0)
                # 三江榜是总榜页，空列表页也正常，仅主检测
                if _is_captcha_page(page, use_fallback=False):
                    raise RuntimeError("qidian 三江榜验证码拦截")
                got_page = True
            except (IpBlockedError, NetworkUnavailable):
                raise
            except RuntimeError:
                if output_file and state_file:
                    self._save_qidian_progress(output_file, state_file, rank, all_raw, done_marks,
                                               empty_categories=["三江推荐"])
                self._block_site("三江榜出现验证码，暂停起点请求")
            except Exception as e:
                if attempt < 2:
                    wait = 5 * (attempt + 1)
                    attempt += 1
                    print(f"  ⚠️ 访问失败重试 {attempt}/3，{wait}s 后: {e}")
                    time.sleep(wait)
                else:
                    print("  ❌ 三江榜访问失败")
                    return []

        try:
            page.wait_for_selector('.strongrec-wrap .book-list li', timeout=10000)
        except Exception:
            print("  ⚠️ 三江榜选择器超时，延时后继续")
        _rand_sleep(1.0, 2.0)
        try:
            books = parse_sanjiang(page.content())
        except Exception as e:
            print(f"  ⚠️ 三江榜提取失败: {e}")
            books = []
        print(f"  三江榜抓到: {len(books)} 本（最新一期）")
        if not books:
            print("  ⚠️ 三江榜未抓到书（可能反爬/页面结构变化）")
            return []
        for r in books:
            r["_cate"] = "三江推荐"
        all_raw.extend(books)
        done_marks.add(mark)
        if output_file and state_file:
            self._save_qidian_progress(output_file, state_file, rank, all_raw, done_marks)

        # 详情补全（字数 + 作者 + 简介，三江页面本身无此三字段）
        self._fetch_details(page, all_raw, output_file, state_file, rank, done_marks)

        if output_file and state_file:
            self._save_qidian_progress(output_file, state_file, rank, all_raw, done_marks)
        return self._group_by_cate(all_raw, None)

    def _needs_details(self, raw):
        if not raw.get("wordCount") or not raw.get("author") or not raw.get("intro"):
            return True
        observed = observed_timestamp(raw.get("_word_count_observed_at"))
        age = time.time() - observed
        return not observed or age < 0 or age >= self.detail_ttl_hours * 3600

    def _fetch_details(self, page, all_raw, output_file, state_file, rank, done_marks,
                       empty_placeholders=None):
        """Refresh missing/expired details; one queue and budget across every rank."""
        if self._lists_only:
            return
        self._apply_cached_details(all_raw)
        grouped = {}
        for raw in all_raw:
            key = self._book_key(raw) or raw.get("bookUrl")
            if key:
                grouped.setdefault(key, []).append(raw)
        queue = [rows for rows in grouped.values() if any(self._needs_details(r) for r in rows)]
        # Missing values first; refresh the oldest known values next.
        queue.sort(key=lambda rows: (all(r.get("wordCount") for r in rows),
                                     min(observed_timestamp(r.get("_word_count_observed_at")) for r in rows)))
        print(f"  📝 待补详情 {len(queue)} 本（跨榜合并，同一书籍只请求一次）")

        def checkpoint():
            if self._detail_checkpoint:
                self._detail_checkpoint()
            elif output_file and state_file:
                self._save_qidian_progress(output_file, state_file, rank, all_raw, done_marks,
                                           empty_categories=empty_placeholders)

        consecutive_captcha = 0
        try:
            for i, rows in enumerate(queue):
                raw = rows[0]
                try:
                    wc, author, intro, captcha = self._fetch_detail(page, raw.get("bookUrl", ""), raw.get("bookId", ""))
                except DetailBudgetReached as exc:
                    self._detail_stop_reason = str(exc)
                    print(f"  ⏸️ {exc}")
                    break
                if captcha:
                    consecutive_captcha += 1
                    self._stats["detail_failures"] += 1
                    if consecutive_captcha >= 3:
                        self._block_site("连续 3 本详情页出现验证码，暂停起点请求")
                else:
                    consecutive_captcha = 0
                    if not wc:
                        self._stats["detail_failures"] += 1
                    for target in rows:
                        if wc:
                            target["wordCount"] = wc
                            target["_word_count_observed_at"] = observed_now()
                            target["_word_count_source"] = "detail"
                        if author and not target.get("author"):
                            target["author"] = author
                        if intro and not target.get("intro"):
                            target["intro"] = intro
                        if self._state is not None:
                            self._state.put(self._book_key(target), target)
                print(f"    [{i+1}/{len(queue)}] {raw.get('bookName', '')}: {'验证码' if captcha else (wc or '详情待补')}")
                can_continue = not self.max_detail_requests or self._stats["detail_requests"] < self.max_detail_requests
                if i < len(queue) - 1 and can_continue:
                    _rand_sleep(*DETAIL_BOOK_GAP)
                if (i + 1) % DETAIL_BATCH_SIZE == 0:
                    checkpoint()
                    if i < len(queue) - 1 and can_continue:
                        _rand_sleep(*DETAIL_BATCH_PAUSE)
        finally:
            checkpoint()

    def _build_book(self, raw, rank_idx, cate_name):
        return to_book(raw, rank_idx)

    def _group_by_cate(self, all_raw, chan_ids):
        """按分类（raw._cate 字段，由抓取 URL 决定）分组到 categories。

        chan_ids 为 None（三江榜）时按 _cate 出现顺序；否则按 chan_ids 顺序。
        """
        order = list(chan_ids.keys()) if chan_ids else []
        categories_map = {}
        for raw in all_raw:
            cate = raw.get("_cate") or "未分类"
            if cate not in categories_map:
                categories_map[cate] = []
                if cate not in order:
                    order.append(cate)
            rank_idx = len(categories_map[cate]) + 1
            categories_map[cate].append(self._build_book(raw, rank_idx, cate))
        return [{"name": name, "books": categories_map[name]} for name in order if name in categories_map]


if __name__ == "__main__":
    from config.environment import load_environment
    load_environment()
    print("开始抓取起点中文网榜单（每日畅销榜 + 签约新书榜 + 新人签约新书榜 + 三江榜）...")
    QidianScraper(limit=100, sleep_sec=4).run()
