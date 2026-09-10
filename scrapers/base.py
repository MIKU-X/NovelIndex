"""小说平台爬虫通用基类。

本项目的浏览器会话、存储和快照通用能力（历史来源与替代记录见 docs/来源核对.md）：
- atomic_write_json / write_json：带重试的原子写入（应对同步盘文件锁）
- TaskStateMixin：中断恢复（先写数据再写 state 的两步法，崩溃安全）
- launch_browser：双引擎反检测——playwright（浏览器默认配置与中文 locale）
  或 patchright（playwright 反检测分支，真实 Chrome 一致性指纹，headless 可过起点反爬）；
  可选 block_resources 资源拦截（图片/字体/样式表，批量详情页提速）
- parse_reads："万/亿" 阅读量解析
- 输出统一 schema：{date, platform, platform_name, rank_type, categories:[{name, books:[{title,author,reads,intro,cover,url,metric_type,...}]}]}

book.reads 语义因平台而异，用 metric_type 标注量纲，下游按类型分别处理不混比：
  - fanqie:  metric_type="views"      reads=真实阅读量（如"15.2万"）
  - qidian:  metric_type="word_count" reads=总字数（如"594.74万字"），另存 word_count 字段
  - jjwxc:   metric_type="score"      reads=积分（如"8.2M"），另存 score 字段（原始 int）
reads_growth 仅对 metric_type=="views" 计算阅读量增量；其他类型不与阅读量混比。

各平台爬虫继承 BaseScraper，实现 scrape_categories(page, rank) 即可。
"""
import json
import os
import sys
import tempfile
import time
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

OUTPUT_DIR = os.path.join(BASE_DIR, "data")

# 资源拦截清单（与 Scrapling 的 disable_resources 同款）：拦截非数据资源可显著
# 加速页面加载（起点详情页实测 6-10s → ~3s），且不影响 DOM 提取——封面 URL 取自
# img 属性而非图片请求本身。只按资源类型拦截、不拦域名：验证码域保持可达，
# _is_captcha_page 的页面标记检测不受影响。
BLOCKED_RESOURCE_TYPES = frozenset({
    "font", "image", "media", "beacon", "object", "imageset",
    "texttrack", "websocket", "csp_report", "stylesheet",
})


def _route_block_resources(route):
    """context.route 处理器：拦截 BLOCKED_RESOURCE_TYPES 内的资源类型，其余放行。"""
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        route.abort()
    else:
        route.continue_()


def _atomic_replace(src: str, dst: str, retries: int = 5, delay: float = 1.0) -> None:
    """带重试的原子替换，应对 BaiduSyncdisk 等同步工具的文件锁定。

    重试耗尽时保留旧文件并抛出异常，禁止退回直写而损坏已有快照。
    """
    if retries < 1:
        raise ValueError("retries must be at least 1")
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(delay * (attempt + 1))


def write_json(path: str, data, indent: int = 2) -> None:
    """原子写入 JSON：先写临时文件再替换，保证中途崩溃不损坏目标文件。

    序列化、磁盘写入或替换失败时向调用方报错，目标文件保留原内容。
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=parent,
        suffix=".tmp", delete=False
    )
    try:
        json.dump(data, tmp, ensure_ascii=False, indent=indent)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        _atomic_replace(tmp.name, path)
    finally:
        # Windows 必须先关闭临时文件才能删除。替换成功时临时路径已不存在。
        try:
            tmp.close()
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


def parse_reads(text: str) -> str:
    """规范化阅读量文本。番茄/起点/晋江格式不一，这里只做轻量清洗保留原文本。
    如 "15.4万" 保留为 "15.4万"，由 build_latest 的 parse_reads 进一步解析。
    """
    if not text:
        return ""
    return str(text).strip()


class BaseScraper:
    """小说平台榜单爬虫基类。

    子类需实现 scrape_categories(page, rank) -> list[{name, books}]
    返回的分类列表每项 {name, books:[{title,author,reads,intro,cover,url}]}。
    基类负责：浏览器启动、中断恢复、原子写入、统一 schema 组装。
    """

    platform: str = ""       # 子类覆盖，如 "qidian"
    platform_name: str = ""  # 子类覆盖，如 "起点中文网"
    headless: bool = True    # 子类可覆盖；起点在 patchright 引擎下 True 即可（见 engine）
    # 浏览器引擎："playwright"（默认，UA 池 + init 脚本反检测，番茄等够用）或
    # "patchright"（playwright 反检测分支：真实 Chrome 一致性指纹，20260908 实测
    # headless=True 可过起点腾讯验证码；未安装 patchright 时自动回退 playwright 并提示）
    engine: str = "playwright"
    # 拦截图片/字体/样式表等非数据资源（批量详情页补全场景提速明显，见 BLOCKED_RESOURCE_TYPES）
    block_resources: bool = False
    # 致命错误类型（子类注入，如起点 IpBlockedError）：run() 中命中即中止整个平台
    fatal_error_types: tuple[type[Exception], ...] = ()

    def __init__(self, limit: int = 30, sleep_sec: float = 5.0):
        self.limit = limit
        self.sleep_sec = sleep_sec
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        if self.platform:
            try:
                self._plat_cfg = get_platform_cfg(self.platform)
                self.platform_name = self._plat_cfg["name"]
            except Exception as e:
                # 保留降级（_plat_cfg=None，platform_name 用子类兜底），但打印 warning 避免配置错误被静默掩盖
                print(f"[WARN] 加载平台配置失败 {self.platform}: {e}（platform_name 将使用子类默认值）")
                self._plat_cfg = None

    # ---------- 浏览器 ----------

    @staticmethod
    def _patchright_factory():
        """返回 patchright 的 sync_playwright 工厂；未安装返回 None。"""
        try:
            from patchright.sync_api import sync_playwright
        except ImportError:
            return None
        return sync_playwright

    def _sync_playwright(self):
        """按 engine 返回浏览器驱动工厂：patchright 优先，未安装回退 playwright。"""
        if self.engine == "patchright":
            factory = self._patchright_factory()
            if factory is not None:
                return factory
            print("[WARN] patchright 未安装（pip install patchright），回退 playwright 引擎"
                  "（起点 headless=True 可能触发腾讯验证码）")
        from playwright.sync_api import sync_playwright
        return sync_playwright

    def launch_browser(self, playwright, headless: bool = True):
        """Return a browser session using the installed browser's native defaults.

        Chrome is preferred; both engines can fall back to their bundled Chromium.
        Both use zh-CN locale. No copied fingerprint injection or UA rotation.
        """
        browser = None
        try:
            engine_pkg = type(playwright).__module__.split(".")[0]
            try:
                browser = playwright.chromium.launch(headless=headless, channel="chrome")
                print(f"[INFO] {engine_pkg} + Chrome 已启动（headless={headless}）")
            except Exception as e:
                print(f"[WARN] Chrome 不可用（{e}），回退内置 Chromium")
                browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(locale="zh-CN")
            if self.block_resources:
                # 挂路由在 new_page 之前：确保首个页面首波请求（文档+首批子资源）即被拦截
                context.route("**/*", _route_block_resources)
            page = context.new_page()
            return browser, context, page
        except Exception:
            # 泄漏前兜底关闭：避免 Chromium 进程残留
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            raise

    # ---------- 中断恢复 ----------

    def _paths(self, rank):
        """返回 (output_file, state_file)。文件名 {slug}_ranks_{YYYYMMDD}.json。

        N77: date_str 优先用 self._run_date_str（由 scrape_rank 入口一次性固定），
        避免跨午夜跑时 _load_state 与后续 _paths 返回不同日期，导致旧 state_file
        找不到、前一日已抓成孤儿。兜底用 now() 防其他入口未设。
        """
        date_str = getattr(self, "_run_date_str", None) or datetime.now().strftime("%Y%m%d")
        slug = rank["slug"]
        output_file = os.path.join(OUTPUT_DIR, f"{slug}_ranks_{date_str}.json")
        state_file = os.path.join(OUTPUT_DIR, f"task_state_{slug}_{date_str}.json")
        return output_file, state_file

    def _load_state(self, output_file, state_file):
        """加载已有数据与已完成分类列表（中断恢复）。
        N64: state 有 completed 但 output 缺失/读失败/空 categories → 清空 completed，避免永久跳过。"""
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
        return completed, all_categories

    def _save_progress(self, output_file, state_file, snapshot, completed):
        """先写数据文件，再写状态文件（两步法，崩溃安全）。"""
        write_json(output_file, snapshot)
        write_json(state_file, {"completed": completed})

    # ---------- 统一 schema 组装 ----------

    def build_snapshot(self, rank, categories):
        """组装统一 schema 的快照对象。

        N77: date 字段优先用 self._run_date_str 转 ISO，保证快照 date 日与
        文件名日一致（防止跨午夜跑时 date 字段跳到次日而文件名仍是前日）。
        """
        date_iso = (
            f"{self._run_date_str[:4]}-{self._run_date_str[4:6]}-{self._run_date_str[6:8]}"
            if getattr(self, "_run_date_str", None) else datetime.now().strftime("%Y-%m-%d")
        )
        return {
            "date": date_iso,
            "platform": self.platform,
            "platform_name": self.platform_name,
            "rank_type": rank["name"],
            "categories": categories,
        }

    # ---------- 子类实现 ----------

    def scrape_categories(self, page, rank) -> list:
        """子类实现：抓取单榜单的所有分类，返回 [{name, books}]。"""
        raise NotImplementedError

    # ---------- 主流程 ----------

    def scrape_rank(self, page, rank):
        """抓取单榜单（含中断恢复、逐分类保存）。

        N77: 入口固定 _run_date_str，本 rank 整个处理周期内 _paths/build_snapshot
        都用它，防止跨午夜跑时日期漂移（旧 state_file 找不到 + date 字段跳日）。
        多 rank 遍历时每个 rank 入口重新固定（仍按当下日期命名，符合直觉）。
        """
        self._run_date_str = datetime.now().strftime("%Y%m%d")
        output_file, state_file = self._paths(rank)
        completed, all_categories = self._load_state(output_file, state_file)
        rank_name = rank["name"]
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🚀 开始抓取{self.platform_name}·{rank_name}")

        categories = self.scrape_categories(page, rank, completed=completed,
                                            all_categories=all_categories,
                                            output_file=output_file,
                                            state_file=state_file)
        # 若子类自行增量保存（推荐，崩溃安全），categories 即最终列表
        categories = categories or []
        total_books = sum(len(c.get("books", [])) for c in categories)
        # N27: 0 分类视为业务失败（反爬/入口失败），供 run() 非零退出
        if len(categories) == 0:
            print(f"❌ {rank_name}抓取失败：0 个分类。数据：{output_file}")
            raise RuntimeError(f"{rank['slug']} 产出 0 个分类")
        # N43: 有分类但全书 0 本同样失败（空占位不等于抓取成功）
        if total_books == 0:
            print(f"❌ {rank_name}抓取失败：{len(categories)} 个分类但 0 本书。数据：{output_file}")
            raise RuntimeError(f"{rank['slug']} 产出 0 本书")
        # N71: 分类数>=5 且有效分类过少（<20%）视为脏成功
        nonempty = sum(1 for c in categories if c.get("books"))
        ncat = len(categories)
        if ncat >= 5 and nonempty < max(1, (ncat + 4) // 5):
            print(f"❌ {rank_name}抓取失败：有效分类过少 {nonempty}/{ncat}。数据：{output_file}")
            raise RuntimeError(f"{rank['slug']} 有效分类过少 {nonempty}/{ncat}")
        print(f"✅ {rank_name}抓取完成！共 {len(categories)} 个分类，{total_books} 本书。数据：{output_file}")
        return output_file

    def run(self):
        """抓取该平台所有已配置榜单。N27: 任一本榜业务失败则最终 raise SystemExit(1)。

        引擎由 _sync_playwright 按 engine 类属性选择；QD_HEADED=1 强制有头（排查用）。
        """
        sync_playwright = self._sync_playwright()
        # QD_HEADED 显式解析："0"/空 = 无头（"0" 是非空字符串，直接 not get() 会把
        # QD_HEADED=0 误判为强制有头）
        _headed = os.environ.get("QD_HEADED", "").strip().lower() in ("1", "true", "yes", "on")
        headless = self.headless and not _headed
        failed = []
        with sync_playwright() as p:
            browser, context, page = self.launch_browser(p, headless=headless)
            try:
                for rank in iter_platform_ranks(self.platform):
                    try:
                        self.scrape_rank(page, rank)
                    except Exception as e:
                        import traceback
                        # 致命错误（IP 级风控等）：继续下一榜只会空转并加深风控，中止整个平台
                        if isinstance(e, self.fatal_error_types):
                            print(f"⛔ 抓取 {rank['slug']} 致命失败，中止本平台剩余榜单：{e}")
                            traceback.print_exc()
                            failed.append(rank["slug"])
                            break
                        print(f"❌ 抓取 {rank['slug']} 失败，跳过：{e}")
                        traceback.print_exc()
                        failed.append(rank["slug"])
            finally:
                browser.close()
        if failed:
            print(f"\n❌ 本平台失败榜单：{', '.join(failed)}（共 {len(failed)}）")
            # INFO: 库函数内不用 sys.exit，改为 raise SystemExit 由 __main__/调用方统一处理
            raise SystemExit(1)


# ---------- 配置辅助 ----------

def get_platform_cfg(platform: str) -> dict:
    from config.loader import get_platform
    return get_platform(platform)


def iter_platform_ranks(platform: str):
    """遍历某平台所有榜单，每项是 get_rank 返回的 dict。"""
    from config.loader import get_rank, load_config
    plat = load_config()["platforms"].get(platform)
    if not plat:
        return
    for rank_id in plat.get("ranks", {}):
        yield get_rank(platform, rank_id)
