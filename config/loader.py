"""平台/榜单配置加载工具。

唯一配置源：config/platforms.json
爬虫、构建引擎(build_latest.py)、前端生成脚本共用本模块。

核心概念：
- platform: 平台 ID（fanqie/qidian/jjwxc/zongheng/qimao）
- rank_id: 平台内榜单 ID（male_new/male/...）
- slug: 文件名前缀 = {platform}_{rank_id}（如 fanqie_male_new、qidian_hotsales）
        番茄原始快照 fanqie_male_new_ranks_*.json 天然符合 {slug}_ranks_*.json 规则。
"""
import json
import os

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "platforms.json"
)
_CACHE = None


def load_config():
    """加载并缓存 platforms.json 全量配置。"""
    global _CACHE
    if _CACHE is None:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            _CACHE = json.load(f)
    return _CACHE


def get_platform(platform):
    """取某平台配置 dict。"""
    return load_config()["platforms"][platform]


def get_rank(platform, rank_id):
    """取某平台某榜单配置 dict，含注入的 platform/rank_id/slug/name/platform_name 字段。"""
    plat = get_platform(platform)
    rank = dict(plat["ranks"][rank_id])
    rank["platform"] = platform
    rank["rank_id"] = rank_id
    rank["slug"] = f"{platform}_{rank_id}"
    rank["platform_name"] = plat["name"]
    # name 形如"男频新书榜"；榜单全名=平台名+榜单名（如"番茄小说男频新书榜"），
    # 但历史数据 rank_type 字段只存榜单名本身（如"女频新书榜"），保持兼容。
    rank["rank_type"] = rank["name"]
    return rank


def iter_ranks():
    """遍历所有已配置榜单，每项是 get_rank 返回的 dict。跳过 _status=pending 的空平台。

    N46: 补齐 docstring 声称的 _status 过滤——platforms.json 中预留平台
    （zongheng/qimao）标记 `_status: "pending_investigation"`，即使未来先填了
    ranks 而爬虫未接入，也不会被纳入 all_slugs()/构建流程（防"假红灯"）。
    """
    for platform, plat in load_config()["platforms"].items():
        status = str(plat.get("_status", ""))
        if status.startswith("pending"):
            continue
        for rank_id in plat.get("ranks", {}):
            yield get_rank(platform, rank_id)


def all_slugs():
    """所有榜单 slug 列表。"""
    return [r["slug"] for r in iter_ranks()]


if __name__ == "__main__":
    for r in iter_ranks():
        print(f"{r['slug']:25s} {r['platform_name']}·{r['name']}")
