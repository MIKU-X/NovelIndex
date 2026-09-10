import json
import os
import re
import sys
import time
from datetime import datetime

# Windows 控制台默认 GBK 编码，无法输出 emoji，强制使用 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 引入统一配置源与本项目浏览器会话/原子写
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.loader import get_platform
from scrapers.base import BaseScraper, write_json
from scrapers.network_errors import NetworkUnavailable, network_failure
from scripts.runtime_state import single_run

_FANQIE_PLATFORM = get_platform("fanqie")

START_CODE = 58344  # 0xE3E8（番茄自定义字体 Unicode 偏移；58344 == 0xE3E8）
CHAR_SEQUENCE = [
    "D", "在", "主", "特", "家", "军", "然", "表", "场", "4", "要", "只", "v", "和", "�", "6", "别", "还", "g", "现", "儿", "岁", "�", "�", "此", "象", "月", "3", "出", "战", "工", "相", "o", "男", "直", "失", "世", "F", "都", "平", "文", "什", "V", "O", "将", "真", "T", "那", "当", "�", "会", "立", "些", "u", "是", "十", "张", "学", "气", "大", "爱", "两", "命", "全", "后", "东", "性", "通", "被", "1", "它", "乐", "接", "而", "感", "车", "山", "公", "了", "常", "以", "何", "可", "话", "先", "p", "i", "叫", "轻", "M", "士", "w", "着", "变", "尔", "快", "l", "个", "说", "少", "色", "里", "安", "花", "远", "7", "难", "师", "放", "t", "报", "认", "面", "道", "S", "�", "克", "地", "度", "I", "好", "机", "U", "民", "写", "把", "万", "同", "水", "新", "没", "书", "电", "吃", "像", "斯", "5", "为", "y", "白", "几", "日", "教", "看", "但", "第", "加", "候", "作", "上", "拉", "住", "有", "法", "r", "事", "应", "位", "利", "你", "声", "身", "国", "问", "马", "女", "他", "Y", "比", "父", "x", "A", "H", "N", "s", "X", "边", "美", "对", "所", "金", "活", "回", "意", "到", "z", "从", "j", "知", "又", "内", "因", "点", "Q", "三", "定", "8", "R", "b", "正", "或", "夫", "向", "德", "听", "更", "�", "得", "告", "并", "本", "q", "过", "记", "L", "让", "打", "f", "人", "就", "者", "去", "原", "满", "体", "做", "经", "K", "走", "如", "孩", "c", "G", "给", "使", "物", "�", "最", "笑", "部", "�", "员", "等", "受", "k", "行", "一", "条", "果", "动", "光", "门", "头", "见", "往", "自", "解", "成", "处", "天", "能", "于", "名", "其", "发", "总", "母", "的", "死", "手", "入", "路", "进", "心", "来", "h", "时", "力", "多", "开", "已", "许", "d", "至", "由", "很", "界", "n", "小", "与", "Z", "想", "代", "么", "分", "生", "口", "再", "妈", "望", "次", "西", "风", "种", "带", "J", "�", "实", "情", "才", "这", "�", "E", "我", "神", "格", "长", "觉", "间", "年", "眼", "无", "不", "亲", "关", "结", "0", "友", "信", "下", "却", "重", "己", "老", "2", "音", "字", "m", "呢", "明", "之", "前", "高", "P", "B", "目", "太", "e", "9", "起", "稜", "她", "也", "W", "用", "方", "子", "英", "每", "理", "便", "四", "数", "期", "中", "C", "外", "样", "a", "海", "们", "任"
]

def decode_text(text: str) -> str:
    if not text:
        return ""
    result = []
    for char in text:
        code = ord(char)
        idx = code - START_CODE
        if 0 <= idx < len(CHAR_SEQUENCE):
            result.append(CHAR_SEQUENCE[idx])
        else:
            result.append(char)
    return "".join(result)


_CHAR_GAP_WARNED = False


def _warn_char_sequence_gaps():
    """N67: CHAR_SEQUENCE 含 � 时启动时说明一次，不猜汉字。

    N9(2026-08-11 实测): 当前 extract_js 用 innerText / img[alt] 提取，浏览器已
    渲染为真实中文，快照全字段无私有区字符（2220 本复核 0 命中缺口），decode_text
    对真实中文是空操作（codepoint < START_CODE → 原样保留），因此缺口索引在现
    路径下**不会**产出 �。缺口仅在未来改走 innerHTML/私有区文本时才需回填
    （运行 python verify_font_mapping.py）。保留为 INFO 提示，不作告警级噪音。
    """
    global _CHAR_GAP_WARNED
    if _CHAR_GAP_WARNED:
        return
    gaps = [i for i, ch in enumerate(CHAR_SEQUENCE) if ch == "�" or ch == "�"]
    if gaps:
        print(
            f"ℹ️ CHAR_SEQUENCE 存在 {len(gaps)} 处映射缺口（索引 {gaps}）。"
            f"当前提取走 innerText/alt（真实中文，2026-08-11 实测 0 命中缺口），"
            f"暂不影响数据；若未来改走私有区文本需运行 verify_font_mapping.py 回填。"
        )
    _CHAR_GAP_WARNED = True


# 排行榜配置：URL模式为 /rank/{gender}_{type}_{category_id}
# gender: 0=女频, 1=男频
# type: 1=新书榜, 2=阅读榜
# entry_cat: 该榜单的首个分类ID（女频用1139，男频用1141）
# 从 config/platforms.json 动态生成，slug = fanqie_{rank_id} 作为输出文件名前缀
RANK_CONFIGS = []
for _rank_id, _cfg in _FANQIE_PLATFORM["ranks"].items():
    RANK_CONFIGS.append({
        "gender": _cfg["gender_code"],
        "type": _cfg["type_code"],
        "name": _cfg["name"],
        "prefix": f"fanqie_{_rank_id}",   # slug，文件名前缀
        "rank_id": _rank_id,
        "entry_cat": _cfg.get("entry_cat", "1139"),
        "platform": "fanqie",
        "platform_name": _FANQIE_PLATFORM["name"],
    })

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

def _is_blocked_page(page) -> bool:
    """检测番茄是否返回验证码/IP 封禁等拦截空壳页（N74）。

    page.goto 即使被风控拦截也通常成功不抛异常，此时会写出 0 书的脏快照。
    番茄正常排行页含大量 a[href^="/page/"] 书籍链接且 HTML 较长；拦截空壳页
    该选择器为 0 且 HTML 显著过短。保守起见仅用结构特征判定，不依赖未知的
    风控关键词，避免误杀正常空分类。参考 qidian.py:_is_captcha_page 同构设计。
    """
    try:
        link_count = page.eval_on_selector_all(
            'a[href^="/page/"]', 'els => els.length'
        )
        if link_count > 0:
            return False  # 有正常榜单链接，不是拦截页
        # 无榜单链接 + HTML 过短，疑似拦截空壳页
        # 阈值 15000：正常排行页含 30 本卡片+字体+反混淆脚本远大于此；
        # 起点同类判定用 25000（含腾讯 captcha HTML），番茄保守取下限。
        if len(page.content()) < 15000:
            return True
    except Exception:
        pass
    return False


def scrape_rank_type(page, rank_config, limit=30, sleep_sec=5):
    """抓取指定类型的排行榜（女频新书/阅读、男频新书/阅读）"""
    _warn_char_sequence_gaps()  # N67: decode 使用前提示字体映射缺口
    gender = rank_config["gender"]
    rank_type = rank_config["type"]
    rank_name = rank_config["name"]
    prefix = rank_config["prefix"]
    entry_cat = rank_config.get("entry_cat", "1139")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    output_file = os.path.join(OUTPUT_DIR, f"{prefix}_ranks_{date_str}.json")
    state_file = os.path.join(OUTPUT_DIR, f"task_state_{prefix}_{date_str}.json")

    # ------------- 状态恢复逻辑（N64: completed 与 output 一致）-------------
    completed_cats = []
    all_categories = []
    if os.path.exists(state_file):
        # C8: open 移入 try——同步盘锁时 open 也会抛 PermissionError，原先在 try 外
        # 会未捕获直接炸掉整个番茄抓取；现与 json.load 同路径降级为「重新开始」。
        try:
            with open(state_file, encoding="utf-8") as f:
                state = json.load(f)
            completed_cats = state.get("completed", [])
        except (OSError, json.JSONDecodeError) as e:
            print(f"⚠️ 状态文件读取失败，将重新开始: {state_file} ({e})")
    if completed_cats:
        if not os.path.exists(output_file):
            print(f"⚠️ output 不存在但 state 有 completed，清空 completed: {output_file}")
            completed_cats = []
        else:
            try:
                with open(output_file, encoding="utf-8") as f:
                    existing = json.load(f)
                all_categories = existing.get("categories", [])
            except (OSError, json.JSONDecodeError) as e:
                print(f"⚠️ 数据文件读取失败，清空 completed: {output_file} ({e})")
                completed_cats = []
                all_categories = []
            if completed_cats and not all_categories:
                print(f"⚠️ output 无 categories 但 state 有 completed，清空 completed: {output_file}")
                completed_cats = []
            elif completed_cats and all_categories:
                # 仅保留 output 中确有同名分类的 mark，避免幽灵 skip
                completed_cats = [
                    c for c in completed_cats
                    if any(x.get("name") == c for x in all_categories)
                ]
    # ----------------------------------------

    # 访问该类型排行榜的入口页面
    init_url = f"https://fanqienovel.com/rank/{gender}_{rank_type}_{entry_cat}"
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🚀 开始抓取{rank_name}，入口：{init_url}")

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            page.goto(init_url, wait_until="load", timeout=15000)
            # N74: goto 成功不抛异常不代表页面正常，番茄可能返回验证码/封禁空壳页。
            # 检测到拦截则直接 fail-fast 进入重试，而非静默写 0 书脏快照。
            if _is_blocked_page(page):
                raise RuntimeError("疑似被番茄拦截（验证码/封禁空壳页）")
            page.wait_for_selector('a[href^="/page/"]', timeout=5000)
            break
        except Exception as e:
            if failure := network_failure(e):
                raise failure from e
            if attempt < max_retries:
                print(f"⚠️ 访问{rank_name}入口页面失败（第{attempt}次），重试中... ({e})")
                time.sleep(3)
            else:
                print(f"❌ 访问{rank_name}入口页面失败（已重试{max_retries}次）: {e}")
                return False

    # 动态解析页面左侧拥有的所有类别目录
    categories_js = f"""
    () => {{
        return Array.from(document.querySelectorAll('a'))
            .filter(a => a.href.includes('/rank/{gender}_{rank_type}_'))
            .map(a => ({{
                name: a.innerText.trim(),
                href: a.getAttribute('href')
            }}));
    }}
    """
    categories = page.evaluate(categories_js)
    print(f"✅ 成功提取到 {len(categories)} 个{rank_name}分类标签")
    # N27: 入口可达但 0 分类视为失败（反爬/页面结构变化）
    if not categories:
        print(f"❌ {rank_name} 未解析到任何分类，标记失败")
        return False

    for cat in categories:
        cat_name = cat["name"]
        cat_href = cat["href"]

        if cat_name in completed_cats:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⏭️ 跳过已完成：{cat_name}")
            continue

        print(f"[{datetime.now().strftime('%H:%M:%S')}] 📖 抓取{rank_name} - {cat_name}")

        # 分类切换重试机制（最多3次）
        max_cat_retries = 3
        cat_switch_success = False
        # N51: href 来自页面抓取，拼入 CSS 属性选择器前转义引号/反斜杠，
        # 防止含特殊字符的 href 破坏选择器语法（当前 href 为数字路径风险极低，防御性修复）
        _cat_href_css = cat_href.replace("\\", "\\\\").replace('"', '\\"')
        for cat_attempt in range(max_cat_retries):
            try:
                page.locator(f'a[href="{_cat_href_css}"]').click()
                time.sleep(2)
                page.wait_for_selector('a[href^="/page/"]', timeout=5000)
                # N74: 切换后页面可能被拦截（验证码/封禁空壳页），wait_for_selector
                # 通过不代表内容正常。检测到拦截则按失败处理（不置 True），进入下一轮
                # 重试；最终失败走 N70 空占位路径，由 N71 兜底，不写脏成功。
                if _is_blocked_page(page):
                    print(f"⚠️ {cat_name}: 切换后疑似被拦截（验证码/封禁空壳页），重试 {cat_attempt + 1}/{max_cat_retries}")
                    if cat_attempt < max_cat_retries - 1:
                        time.sleep(2)
                        continue
                    break
                cat_switch_success = True
                break
            except Exception as e:
                if failure := network_failure(e):
                    raise failure from e
                if cat_attempt < max_cat_retries - 1:
                    print(f"⚠️ 切换分类出错，重试 {cat_attempt + 1}/{max_cat_retries}: {e}")
                    time.sleep(2)
                else:
                    print(f"⚠️ 切换分类出错，跳过 {cat_name}: {e}")

        if not cat_switch_success:
            # N70: 切换失败写空占位（与 0 书路径一致），不 mark completed，便于重试与 N71 脏成功门禁
            existing_idx = next(
                (i for i, c in enumerate(all_categories) if c.get("name") == cat_name),
                None,
            )
            if existing_idx is not None:
                prev_books = all_categories[existing_idx].get("books") or []
                if prev_books:
                    print(f"  ⏭️ {cat_name}: 切换失败，已有 {len(prev_books)} 本，保留原数据")
                else:
                    all_categories[existing_idx] = {"name": cat_name, "books": []}
            else:
                all_categories.append({"name": cat_name, "books": []})
            snapshot = {
                "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}",
                "platform": rank_config.get("platform", "fanqie"),
                "platform_name": rank_config.get("platform_name", "番茄小说"),
                "rank_type": rank_name,
                "categories": all_categories,
            }
            write_json(output_file, snapshot)
            print(f"  🔄 {cat_name}: 切换失败，写入空占位，不 mark completed")
            continue

        # 动态滚动：检测是否已滚动到底部或达到目标数量
        max_scrolls = 25  # 最大滚动次数上限
        target_count = limit + 10  # 多加载一些以确保有足够的书
        scroll_count = 0
        no_new_count = 0  # 连续无新书加载的次数

        while scroll_count < max_scrolls:
            try:
                # 获取当前加载的书籍数量
                current_count = page.evaluate("""
                    () => document.querySelectorAll('a[href^="/page/"]').length
                """)

                # 滚动一屏
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                time.sleep(2)
                scroll_count += 1

                # 检查新加载的书籍数量
                new_count = page.evaluate("""
                    () => document.querySelectorAll('a[href^="/page/"]').length
                """)

                if new_count > current_count:
                    no_new_count = 0  # 有新书加载，重置计数器
                else:
                    no_new_count += 1

                # 如果已加载足够数量或连续5次无新书加载，停止滚动
                if new_count >= target_count or no_new_count >= 5:
                    break
            except Exception as e:
                print(f"  ⚠️ 滚动异常，停止加载: {e}")
                break

        # 提取书籍数据
        extract_js = """
        () => {
            const bookMap = new Map();
            const links = document.querySelectorAll('a[href^="/page/"]');
            links.forEach(link => {
                let container = link.parentElement;
                let depth = 0;
                while (container && depth < 6) {
                    if (container.querySelector('img') && container.innerText.includes('在读')) {
                        const href = link.getAttribute('href');
                        if (!bookMap.has(href)) {
                            bookMap.set(href, container);
                        }
                        break;
                    }
                    container = container.parentElement;
                    depth++;
                }
            });

            const cards = Array.from(bookMap.values());
            const results = [];
            for (const item of cards) {
                let imgNode = item.querySelector('img');
                let cover = imgNode ? imgNode.getAttribute('src') : "";

                let title = "";
                if (imgNode && imgNode.getAttribute('alt')) {
                    title = imgNode.getAttribute('alt').trim();
                }
                if (!title) {
                    let textTitleNode = item.querySelector('h4, .title, h1') || item.querySelector('a[href^="/page/"]');
                    if (textTitleNode) {
                        let text = textTitleNode.innerText.trim();
                        if (text && !/^\\d+$/.test(text)) {
                            title = text;
                        }
                    }
                }
                if (!title) title = "未知";
                if (title.includes("榜单说明")) continue;

                let authorNode = item.querySelector('.author, .author-name') || item.querySelector('a[href^="/author-page/"]');
                let author = authorNode ? authorNode.innerText.trim() : "未知";

                let reads = "未知";
                const lines = item.innerText.split('\\n');
                for (let line of lines) {
                    if (line.includes('在读')) {
                        reads = line;
                        break;
                    }
                }

                let introNode = item.querySelector('.intro, .abstract, .desc');
                let intro = introNode ? introNode.innerText.trim() : "暂无简介";

                results.push({
                    title: title,
                    author: author,
                    reads: reads,
                    intro: intro,
                    cover: cover,
                    url: item.querySelector('a[href^="/page/"]').getAttribute('href')
                });
            }
            return results;
        }
        """

        try:
            books_data = page.evaluate(extract_js)
        except Exception as e:
            if failure := network_failure(e):
                raise failure from e
            print(f"JS抽取失败 {cat_name}: {e}")
            books_data = []

        category_books = []
        for b in books_data[:limit]:
            t = decode_text(b.get("title", ""))
            a = decode_text(b.get("author", ""))
            r_raw = decode_text(b.get("reads", ""))
            # N52: 原 `replace("\\n", " ")` 只替换字面反斜杠-n、对真实换行无效；
            # 改为替换真实换行符（json.dump 会自行转义，此处仅为可读性清洗）
            i = decode_text(b.get("intro", "")).replace("\n", " ")
            c = b.get("cover", "")

            # C12: 用正则从行中提取阅读量数字（如 "15.2万人在读" / "在读 15.2万" → "15.2万"），
            # 不再依赖 "在读" 标记词的位置，DOM 微调也不至于静默取空
            # N8: 在读量<1万时页面格式为"在读：8623"（无"万/亿"单位）。旧逻辑 `m else r_raw`
            # 会把整行前缀保留进 reads → build_latest.parse_reads("在读：8623") 解析为 0
            # （阅读量趋势丢失）+ 前端显示"在读：在读：8623"双前缀。无单位时改提取纯数字
            # （"8623"），parse_reads 可解析为 8623、前端单前缀。
            m = re.search(r'([\d.]+)\s*[万亿]', r_raw)
            if m:
                cleaned_r = m.group(0)
            else:
                m2 = re.search(r'\d+(?:\.\d+)?', r_raw)
                cleaned_r = m2.group(0) if m2 else r_raw

            # 验证URL：处理None、空字符串、完整URL、相对路径等情况
            raw_url = b.get("url", "")
            if not raw_url:
                url = ""
            elif raw_url.startswith("http://") or raw_url.startswith("https://"):
                # 已经是完整URL，直接使用
                url = raw_url
            else:
                # 相对路径，拼接域名
                url = "https://fanqienovel.com" + raw_url

            category_books.append({
                "title": t,
                "author": a,
                "reads": cleaned_r,
                "metric_type": "views",   # D4治本：番茄 reads 是真实阅读量，标注量纲供下游按类型处理
                "intro": i,
                "cover": c,
                "url": url
            })

        # N41: 空占位可被重试覆盖；仅「已有非空且本次仍 0 书」时保留原数据
        existing_idx = next(
            (i for i, c in enumerate(all_categories) if c.get("name") == cat_name),
            None,
        )
        if existing_idx is not None:
            prev_books = all_categories[existing_idx].get("books") or []
            if prev_books and not category_books:
                print(f"  ⏭️ {cat_name}: 已有 {len(prev_books)} 本且本次 0 书，保留原数据")
            else:
                all_categories[existing_idx] = {
                    "name": cat_name,
                    "books": category_books,
                }
                if prev_books and category_books:
                    print(f"  🔄 {cat_name}: 覆盖更新 {len(prev_books)} → {len(category_books)} 本")
                elif not prev_books and category_books:
                    print(f"  🔄 {cat_name}: 空占位补全为 {len(category_books)} 本")
        else:
            all_categories.append({
                "name": cat_name,
                "books": category_books
            })

        # 数据质量校验
        if len(category_books) == 0:
            print(f"  ⚠️ {cat_name}: 未提取到任何书籍，可能页面加载异常")
        elif len(category_books) < 10:
            print(f"  ⚠️ {cat_name}: 仅提取到 {len(category_books)} 本书，数量偏少")

        # 检查是否有"未知"标题
        unknown_count = sum(1 for b in category_books if b["title"] == "未知")
        if unknown_count > 0:
            print(f"  ⚠️ {cat_name}: {unknown_count} 本书标题为'未知'，可能提取异常")

        # 原子写入：先写数据文件，再写状态文件
        # 顺序设计：如果在两步之间崩溃，状态文件不会标记该分类完成，
        # 下次运行会重新抓取（安全重复），而不会丢失数据

        # 先写数据文件（原子写入）——空分类也写入占位，便于趋势 no_data
        snapshot = {
            "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}",
            "platform": rank_config.get("platform", "fanqie"),
            "platform_name": rank_config.get("platform_name", "番茄小说"),
            "rank_type": rank_name,
            "categories": all_categories
        }
        write_json(output_file, snapshot)

        # N34: 0 书不标记 completed，下次可重试该分类
        if len(category_books) == 0:
            print(f"  🔄 {cat_name}: 0 书，不写入 completed，下次重试")
            continue

        # 再更新状态文件（原子写入）
        completed_cats.append(cat_name)
        write_json(state_file, {"completed": completed_cats})

        print(f"  ✅ {cat_name}: {len(category_books)} 本书")
        time.sleep(sleep_sec)

    # 最终数据质量校验
    cat_names = [c["name"] for c in all_categories]
    duplicates = [name for name in cat_names if cat_names.count(name) > 1]
    if duplicates:
        print(f"  ⚠️ 发现重复分类：{set(duplicates)}，可能存在数据问题")

    total_books = sum(len(c["books"]) for c in all_categories)
    # N43: 有分类但全书 0 本视为业务失败（避免 N27 只拦 0 分类）
    if total_books == 0:
        print(f"❌ {rank_name}抓取失败：{len(all_categories)} 个分类但 0 本书。数据：{output_file}")
        return False
    # N71: 分类数>=5 且有效分类过少（<20%）视为脏成功
    nonempty = sum(1 for c in all_categories if c.get("books"))
    ncat = len(all_categories)
    if ncat >= 5 and nonempty < max(1, (ncat + 4) // 5):
        print(f"❌ {rank_name}抓取失败：有效分类过少 {nonempty}/{ncat}。数据：{output_file}")
        return False
    print(f"✅ {rank_name}抓取完成！共 {len(all_categories)} 个分类，{total_books} 本书。数据：{output_file}")
    return True


@single_run("scrape-fanqie")
def run_scraper(limit=30, sleep_sec=5):
    """抓取所有排行榜类型。N27: 任一本榜失败则返回 False（入口 exit 1）。"""
    failed = []
    scraper = BaseScraper()
    with scraper._sync_playwright()() as p:
        browser, context, page = scraper.launch_browser(p, headless=True)
        try:
            # 依次抓取每种排行榜类型
            # C9: 单榜异常隔离——scrape_rank_type 业务失败返回 False，但未捕获的
            # 异常会拖垮其余三榜（对比 scrapers/base.py 的 run() 每个 rank 有隔离）。
            # 循环内 try/except，异常记入 failed 列表继续下一榜。
            for rank_config in RANK_CONFIGS:
                try:
                    ok = scrape_rank_type(page, rank_config, limit, sleep_sec)
                    if ok is False:
                        failed.append(rank_config.get("name") or rank_config.get("rank_id", "?"))
                except NetworkUnavailable as e:
                    print(f"⏸️ {e}；已完成分类的数据与断点已保留")
                    return False
                except Exception as e:
                    import traceback
                    print(f"❌ 抓取 {rank_config.get('name', '?')} 失败：{e}")
                    traceback.print_exc()
                    failed.append(rank_config.get("name") or rank_config.get("rank_id", "?"))
        finally:
            browser.close()

    if failed:
        print(f"\n❌ 番茄失败榜单：{', '.join(failed)}（共 {len(failed)}）")
        return False
    print(f"\n🎉 所有排行榜抓取完成！数据目录：{OUTPUT_DIR}")
    return True

if __name__ == "__main__":
    print("开始执行番茄小说排行榜全量抓取（女频新书/阅读、男频新书/阅读）...")
    import sys
    ok = run_scraper(limit=30, sleep_sec=5)
    if not ok:
        sys.exit(1)
