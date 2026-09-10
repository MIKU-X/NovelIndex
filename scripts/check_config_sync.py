"""配置同步校验：核对 js/config.js 的 PLATFORMS 与 config/platforms.json 是否一致。

A1: 前端 config.js 是 platforms.json 的硬编码副本，新增平台需双写，易漂移。
本脚本校验两边的平台列表、name、color、ranks(rank_id 列表 + name + gender) 是否一致，
并额外校验 config.js 每平台 icon 非空（icon 是前端专属，platforms.json 无），
不一致时打印差异并返回非零退出码，便于纳入 CI 或 run_all 前置检查。

用法：
    python scripts/check_config_sync.py
"""
import json
import os
import re
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JSON_PATH = os.path.join(BASE_DIR, "config", "platforms.json")
JS_PATH = os.path.join(BASE_DIR, "js", "config.js")
BUILD_LATEST_PATH = os.path.join(BASE_DIR, "scripts", "build_latest.py")
TREND_JS_PATH = os.path.join(BASE_DIR, "js", "trend.js")


def load_json_config() -> dict:
    """从 platforms.json 读取 {platform: {name, color, ranks: {rank_id: {name, gender}}}}。"""
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    platforms = {}
    for pid, plat in data.get("platforms", {}).items():
        ranks = {}
        for rid, r in plat.get("ranks", {}).items():
            ranks[rid] = {"name": r.get("name", ""), "gender": r.get("gender", "")}
        platforms[pid] = {"name": plat.get("name", ""), "color": plat.get("color", ""), "ranks": ranks}
    return platforms


def load_js_config() -> dict:
    """从 js/config.js 的 PLATFORMS 对象解析出同构结构。

    纯静态前端不能 fetch platforms.json（file:// 受 CORS 限制），故 config.js 是硬编码副本，
    本函数用正则解析该副本，避免引入 Node 依赖。
    """
    with open(JS_PATH, encoding="utf-8") as f:
        src = f.read()
    platforms = {}
    # 平台块：形如  fanqie: { name: "番茄小说", color: "#ff6b35", icon: "番", ranks: { ... } }
    # ranks 内嵌大括号，故逐平台提取 name/color，再单独提取其 ranks 块
    plat_pattern = re.compile(
        r"([a-z_]+):\s*\{\s*name:\s*\"([^\"]*)\",\s*color:\s*\"([^\"]*)\""
        r"(?:,\s*icon:\s*\"([^\"]*)\")?"
    )
    for m in plat_pattern.finditer(src):
        pid, pname, pcolor, picon = m.group(1), m.group(2), m.group(3), m.group(4)
        # 从该平台 name 位置向后找最近的 ranks: { ... }，匹配到同层级闭合 }
        ranks_start = src.find("ranks:", m.end())
        if ranks_start == -1:
            platforms[pid] = {"name": pname, "color": pcolor, "icon": picon or "", "ranks": {}}
            continue
        brace_start = src.find("{", ranks_start)
        depth = 0
        ranks_end = None
        for i in range(brace_start, len(src)):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    ranks_end = i + 1
                    break
        ranks_block = src[brace_start + 1:ranks_end - 1] if ranks_end else ""
        ranks = {}
        # 每个 rank: rank_id: { name: "...", gender: "...", ... }
        # rank_id 可能带引号也可能不带，此处 config.js 用裸 key
        # N105: 放宽正则，允许 name/gender 之间或之后有额外字段（如 url/icon/color），
        # 避免新增字段时该 rank 静默漏检导致双副本漂移。[^}]* 不跨闭合大括号，安全。
        rank_pattern = re.compile(
            r"([a-z_]+):\s*\{[^}]*name:\s*\"([^\"]*)\"[^}]*gender:\s*\"([^\"]*)\"[^}]*\}"
        )
        for rm in rank_pattern.finditer(ranks_block):
            ranks[rm.group(1)] = {"name": rm.group(2), "gender": rm.group(3)}
        platforms[pid] = {"name": pname, "color": pcolor, "icon": picon or "", "ranks": ranks}
    return platforms


def _extract_keywords(src: str, start_marker: str) -> list:
    """从源码中提取关键词列表。

    支持 Python 列表（单引号或双引号字符串，逗号分隔，可能跨多行带注释）和
    JS 数组（单引号字符串，逗号分隔）。统一返回去除引号后的字符串列表。
    start_marker 为列表起始锚点（如 "MARKET_KEYWORDS = [" 或 "const keywords = ["）。
    """
    idx = src.find(start_marker)
    if idx == -1:
        return []
    # 从 marker 后找第一个 [ 到匹配的 ]
    bracket_start = src.find("[", idx)
    if bracket_start == -1:
        return []
    depth = 0
    bracket_end = None
    for i in range(bracket_start, len(src)):
        if src[i] == "[":
            depth += 1
        elif src[i] == "]":
            depth -= 1
            if depth == 0:
                bracket_end = i
                break
    if bracket_end is None:
        return []
    block = src[bracket_start + 1:bracket_end]
    # 提取所有引号字符串（单引号或双引号），容错 Python/JS 注释行
    kw_pattern = re.compile(r"""['"]([^'"]+)['"]""")
    return kw_pattern.findall(block)


def check_market_keywords() -> list:
    """N8: 校验 build_latest.py 的 MARKET_KEYWORDS 与 trend.js 的关键词数组是否一致。

    两处为硬编码双副本（前端不能 fetch 后端常量），本函数把"双副本漂移"变为可检测：
    解析两边的字符串列表，对比是否完全相同（顺序敏感，因为顺序决定展示优先级）。
    返回差异列表。
    """
    diffs = []
    try:
        with open(BUILD_LATEST_PATH, encoding="utf-8") as f:
            py_src = f.read()
    except Exception as e:
        return [f"无法读取 build_latest.py 进行关键词校验: {e}"]
    try:
        with open(TREND_JS_PATH, encoding="utf-8") as f:
            js_src = f.read()
    except Exception as e:
        return [f"无法读取 trend.js 进行关键词校验: {e}"]

    py_kws = _extract_keywords(py_src, "MARKET_KEYWORDS")
    js_kws = _extract_keywords(js_src, "const keywords = [")

    if not py_kws:
        diffs.append("build_latest.py: 未解析到 MARKET_KEYWORDS 列表（解析逻辑可能需更新）")
    if not js_kws:
        diffs.append("trend.js: 未解析到关键词数组（解析逻辑可能需更新）")
    if not py_kws or not js_kws:
        return diffs

    # N50: 顺序敏感（决定展示优先级），集合相同但顺序不同也报错
    if py_kws != js_kws:
        py_set, js_set = set(py_kws), set(js_kws)
        only_py = py_set - js_set
        only_js = js_set - py_set
        if only_py or only_js:
            diffs.append(
                f"MARKET_KEYWORDS 双副本不一致: build_latest.py {len(py_kws)} 项 vs trend.js {len(js_kws)} 项"
            )
            if only_py:
                diffs.append(f"  仅 build_latest.py 有: {sorted(only_py)}")
            if only_js:
                diffs.append(f"  仅 trend.js 有: {sorted(only_js)}")
        else:
            diffs.append(
                f"MARKET_KEYWORDS 顺序不一致（集合相同，{len(py_kws)} 项）："
                "请以 build_latest.py 为准对齐 trend.js"
            )
    return diffs


def diff(json_cfg: dict, js_cfg: dict) -> list:
    """返回差异列表，每条为可读字符串。"""
    diffs = []
    json_pids = set(json_cfg.keys())
    js_pids = set(js_cfg.keys())
    for pid in sorted(json_pids ^ js_pids):
        if pid in json_pids:
            diffs.append(f"平台 {pid}: platforms.json 有，但 config.js 缺失")
        else:
            diffs.append(f"平台 {pid}: config.js 有，但 platforms.json 缺失")
    for pid in sorted(json_pids & js_pids):
        jp, sp = json_cfg[pid], js_cfg[pid]
        if jp["name"] != sp["name"]:
            diffs.append(f"平台 {pid}.name: platforms.json={jp['name']!r} vs config.js={sp['name']!r}")
        if jp["color"] != sp["color"]:
            diffs.append(f"平台 {pid}.color: platforms.json={jp['color']!r} vs config.js={sp['color']!r}")
        jr, sr = jp["ranks"], sp["ranks"]
        # N57: 正则解析 config.js 遇 rank 字段值含 '}' 等会漏解析整条 rank（假阳性）。
        # 数量级交叉校验：两边 rank 数不一致时显式提示「可能是解析不完整」，
        # 便于区分真实漂移与解析器局限，避免排查绕路。
        if len(jr) != len(sr):
            diffs.append(
                f"平台 {pid}: 榜单数量 platforms.json={len(jr)} vs config.js={len(sr)}"
                "（若下方无逐榜差异，可能是 config.js 正则解析不完整，需人工核对 config.js）"
            )
        for rid in sorted(set(jr.keys()) ^ set(sr.keys())):
            if rid in jr:
                diffs.append(f"平台 {pid} 榜单 {rid}: platforms.json 有，config.js 缺失")
            else:
                diffs.append(f"平台 {pid} 榜单 {rid}: config.js 有，platforms.json 缺失")
        for rid in sorted(set(jr.keys()) & set(sr.keys())):
            for field in ("name", "gender"):
                if jr[rid][field] != sr[rid][field]:
                    diffs.append(
                        f"平台 {pid} 榜单 {rid}.{field}: platforms.json={jr[rid][field]!r} vs config.js={sr[rid][field]!r}"
                    )
    # N106: icon 是前端专属字段（platforms.json 无），只校验 config.js 侧
    # 每个平台必须有非空 icon，否则新增平台忘加 icon 会静默降级（用 plat.name.charAt(0) 兜底）
    for pid in sorted(js_pids):
        icon = js_cfg[pid].get("icon", "")
        if not icon:
            diffs.append(f"平台 {pid}: config.js 缺少 icon 或 icon 为空（前端依赖 icon 渲染平台标识）")
    return diffs


def main():
    if not os.path.exists(JSON_PATH):
        print(f"❌ 配置文件不存在: {JSON_PATH}")
        return 2
    if not os.path.exists(JS_PATH):
        print(f"❌ 前端配置不存在: {JS_PATH}")
        return 2
    try:
        json_cfg = load_json_config()
    except Exception as e:
        print(f"❌ 解析 {JSON_PATH} 失败: {e}")
        return 2
    try:
        js_cfg = load_js_config()
    except Exception as e:
        print(f"❌ 解析 {JS_PATH} 失败: {e}")
        return 2

    print(f"platforms.json 平台数: {len(json_cfg)}（{', '.join(sorted(json_cfg.keys()))}）")
    print(f"config.js 平台数:      {len(js_cfg)}（{', '.join(sorted(js_cfg.keys()))}）")

    diffs = diff(json_cfg, js_cfg)
    # N8: 关键词双副本校验
    diffs.extend(check_market_keywords())
    if not diffs:
        print("✅ 配置同步校验通过：platforms.json 与 config.js 的平台/rank/name/gender/color 完全一致，"
              "config.js 每平台 icon 非空，且 build_latest.py MARKET_KEYWORDS 与 trend.js 关键词数组一致")
        return 0
    print(f"❌ 发现 {len(diffs)} 处配置不一致（前端 config.js 是硬编码副本，需手动同步）：")
    for d in diffs:
        print(f"  - {d}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
