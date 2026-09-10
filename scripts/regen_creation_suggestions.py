"""补跑创作建议：加载各榜已生成的 theme_trends/competitive/reader_profile，
调用 build_latest.build_creation_suggestions 生成 author/creation_suggestions_*.json。

用途：build_latest 全量构建时若 SKIP_CREATION_SUGGESTIONS=1 跳过了创作建议，
事后用本脚本单独补，避免重跑整榜。
逐榜写盘（断点续跑），失败不阻断后续榜单。
"""
import glob
import json
import os
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from scripts.runtime_state import single_run

AUTHOR_DIR = os.path.join(BASE, "data", "author")

# 复用 build_latest 的函数与配置加载
from dotenv import load_dotenv  # noqa: E402

# P1 同步：.env 可能是 GBK（setup.bat chcp 936 生成），按与 build_latest 相同的
# utf-8-sig → gbk 顺序容错加载，任一生效即可；均失败则降级为系统环境变量。
_dotenv_loaded = False
_env_path = os.path.join(BASE, ".env")
for _enc in ("utf-8-sig", "gbk"):
    try:
        load_dotenv(_env_path, encoding=_enc)
        _dotenv_loaded = True
        break
    except (UnicodeDecodeError, ValueError):
        continue
if not _dotenv_loaded:
    print("[WARN] .env 编码无法识别（utf-8-sig/gbk 均失败），已跳过 .env，将使用系统环境变量")
import build_latest  # noqa: E402
from ai_client import AIClient, load_ai_endpoints, mask_secrets  # noqa: E402

from config.loader import iter_ranks  # noqa: E402

_endpoints = load_ai_endpoints()

# B11: 需补的榜单由 config/platforms.json 动态派生（与 build_latest 的 all_slugs 同源），
# 不再硬编码 7 个 slug。--rank 可限定单榜。
ALL_SLUGS = [r["slug"] for r in iter_ranks()]


def _latest_snapshot_date(slug):
    """取该 slug 最新快照文件（data/{slug}_ranks_*.json 最新）的 date 字段；无快照返回 ""。"""
    paths = sorted(glob.glob(os.path.join(BASE, "data", f"{slug}_ranks_*.json")))
    if not paths:
        return ""
    try:
        with open(paths[-1], encoding="utf-8") as f:
            return json.load(f).get("date", "")
    except Exception:
        return ""


def need_regenerate(slug):
    """判断某榜创作建议是否需要重新生成：缺失 / 空壳(genre_suggestions全0) / 日期非最新快照。"""
    path = os.path.join(AUTHOR_DIR, f"creation_suggestions_{slug}.json")
    if not os.path.exists(path):
        return True
    try:
        # N12: with 关闭句柄，避免 Windows 同步盘场景残留文件锁
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return True
    # B4: 以最新快照 date 为基准比较，而非脚本运行日——快照滞后时不应每次都补跑
    base_date = _latest_snapshot_date(slug)
    if not base_date:
        return True  # 无快照可对，视为需补跑
    if d.get("date", "") != base_date:
        return True
    # 空壳检测：任一窗口有 genre_suggestions 即视为有效
    periods = d.get("periods", {})
    has_content = any(
        len(pd.get("genre_suggestions", {})) > 0 for pd in periods.values()
    )
    return not has_content


@single_run("build")
def main():
    import argparse
    parser = argparse.ArgumentParser(description="补生成创作建议")
    parser.add_argument("--rank", type=str, default="", help="仅补指定 slug")
    parser.add_argument("--force", action="store_true", help="强制重跑，跳过 need_regenerate 判断")
    args = parser.parse_args()

    # #11(0902): SKIP_CREATION_SUGGESTIONS=1 时 build_creation_suggestions 直接返回空，
    # 本脚本会全程空跑且每榜打印误导性的「返回空（API 调用失败）」。入口显式报错退出
    if os.environ.get("SKIP_CREATION_SUGGESTIONS", "") == "1":
        print("❌ SKIP_CREATION_SUGGESTIONS=1 仍生效：build_creation_suggestions 会直接返回空，"
              "补跑将全程空转。请在 .env 中删除该变量（或设为 0）后重跑本脚本。")
        sys.exit(1)

    slugs = [args.rank] if args.rank else ALL_SLUGS

    if not _endpoints:
        print("❌ AI 未配置（API_BASE_URL/API_KEY/API_MODEL 或 API_1_* 等编号配置），无法生成。")
        sys.exit(1)
    _ai_client = AIClient(_endpoints)
    print(f"AI: 已配置 {len(_endpoints)} 个 endpoint")

    if args.force:
        todo = list(slugs)
        skip = []
    else:
        todo = [s for s in slugs if need_regenerate(s)]
        skip = [s for s in slugs if s not in todo]
    print(f"待补: {len(todo)} 榜 {todo}")
    if skip:
        print(f"跳过(已有效): {skip}")

    for i, slug in enumerate(todo, 1):
        print(f"\n{'='*60}\n[{i}/{len(todo)}] {slug}\n{'='*60}")
        tt_path = os.path.join(AUTHOR_DIR, f"theme_trends_{slug}.json")
        cp_path = os.path.join(AUTHOR_DIR, f"competitive_analysis_{slug}.json")
        rp_path = os.path.join(AUTHOR_DIR, f"reader_profile_{slug}.json")
        for p in (tt_path, cp_path, rp_path):
            if not os.path.exists(p):
                print(f"  ❌ 缺输入数据 {p}，跳过该榜")
                break
        else:
            t0 = time.time()
            try:
                # N12: with 关闭句柄
                with open(tt_path, encoding="utf-8") as f:
                    theme_trends = json.load(f)
                with open(cp_path, encoding="utf-8") as f:
                    competitive = json.load(f)
                with open(rp_path, encoding="utf-8") as f:
                    reader_profile = json.load(f)
                suggestions = build_latest.build_creation_suggestions(
                    theme_trends, competitive, reader_profile, _ai_client,
                )
                if suggestions:
                    out = os.path.join(AUTHOR_DIR, f"creation_suggestions_{slug}.json")
                    # 增量 merge：与旧文件合并，保留未本次生成的窗口（CREATION_PERIODS 限定窗口时，
                    # 旧文件的 7/14/30 等窗口保留，只更新本次生成的窗口）
                    merged = {"date": suggestions.get("date", ""), "periods": {}}
                    if os.path.exists(out):
                        try:
                            # N12: with 关闭句柄
                            with open(out, encoding="utf-8") as f:
                                old = json.load(f)
                            # B3: 新 date 非空才取新（快照日推进会更新 date），否则保留旧值；
                            # 原实现旧 date 恒胜，日期永不更新
                            if not merged["date"]:
                                merged["date"] = old.get("date", merged["date"])
                            merged["periods"] = dict(old.get("periods", {}))
                        except Exception:
                            pass
                    for pk, pd in suggestions.get("periods", {}).items():
                        # N30: 本次该窗口失败（genre 与 cross 均空）且旧文件有旧值时保留旧值，
                        # 避免空壳覆盖历史有效建议；本次有内容、或旧文件本无该窗口时才写入。
                        if (pd.get("genre_suggestions") or pd.get("cross_genre_opportunities")) \
                                or not merged["periods"].get(pk):
                            merged["periods"][pk] = pd
                        else:
                            print(f"  ⚠️ 窗口 {pk} 本次生成失败，保留旧值")
                    build_latest.write_json(out, merged)
                    n = sum(len(pd.get("genre_suggestions", {}))
                            for pd in merged.get("periods", {}).values())
                    new_pks = list(suggestions.get("periods", {}).keys())
                    print(f"  ✅ 完成 {slug}（{time.time()-t0:.0f}s，本次更新窗口{new_pks}，"
                          f"合并后共{n}条建议）")
                else:
                    print(f"  ⚠️  {slug} 返回空（API 调用失败），保留原文件")
            except Exception as e:
                # N32: 异常文本脱敏后打印，防 API key/带 key 的 URL 泄入日志
                print(f"  ❌  {slug} 异常: {type(e).__name__}: {mask_secrets(e)}")

    print("\n补跑完成")


if __name__ == "__main__":
    main()
