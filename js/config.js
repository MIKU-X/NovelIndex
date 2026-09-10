/* ============================================================
   平台/榜单注册表 + URL参数解析
   所有页面共享此配置。通过 ?rank=<slug> 参数确定当前榜单。

   slug = {platform}_{rank_id}，如 fanqie_female_new / qidian_hotsales / jjwxc_month
   兼容旧 URL：?rank=female_new（无平台前缀）自动映射到 fanqie_female_new
   ============================================================ */

// 平台注册表：平台 -> {name, color, ranks: {rank_id: {name, gender}}}
const PLATFORMS = {
    fanqie: {
        name: "番茄小说", color: "#ff6b35", icon: "番",
        ranks: {
            male_new:    { name: "男频新书榜", gender: "男频" },
            male_read:   { name: "男频阅读榜", gender: "男频" },
            female_new:  { name: "女频新书榜", gender: "女频" },
            female_read: { name: "女频阅读榜", gender: "女频" },
        }
    },
    qidian: {
        name: "起点中文网", color: "#c83c23", icon: "起",
        ranks: {
            hotsales: { name: "每日畅销榜", gender: "综合" },
            signnewbook: { name: "签约新书榜", gender: "综合" },
            newsign: { name: "新人签约新书榜", gender: "综合" },
            sanjiang: { name: "三江榜", gender: "综合" },
        }
    },
    jjwxc: {
        name: "晋江文学城", color: "#e6488a", icon: "晋",
        ranks: {
            month: { name: "积分月榜", gender: "综合" },
        }
    },
    zongheng: {
        name: "纵横中文网", color: "#1e90ff", icon: "纵",
        ranks: {}  // 待接入
    },
    qimao: {
        name: "七猫小说", color: "#9b59b6", icon: "猫",
        ranks: {}   // 待接入
    },
};

// 扁平化：slug -> {platform, platform_name, color, rank_id, name, gender, prefix, icon}
const RANK_TYPES = {};
let DEFAULT_RANK_TYPE = "fanqie_female_new";
for (const [platformKey, plat] of Object.entries(PLATFORMS)) {
    for (const [rankId, rank] of Object.entries(plat.ranks)) {
        const slug = `${platformKey}_${rankId}`;
        RANK_TYPES[slug] = {
            platform: platformKey,
            platform_name: plat.name,
            color: plat.color,
            icon: plat.icon || plat.name.charAt(0),
            rank_id: rankId,
            name: rank.name,
            gender: rank.gender,
            prefix: slug,   // 文件名前缀，下游统一用 rankConfig.prefix
        };
    }
}

// 旧 slug -> 新 slug 映射（番茄旧 URL ?rank=female_new）
const LEGACY_SLUG_MAP = {
    male_new: "fanqie_male_new",
    male_read: "fanqie_male_read",
    female_new: "fanqie_female_new",
    female_read: "fanqie_female_read",
};

function getRankTypeFromURL() {
    const params = new URLSearchParams(window.location.search);
    let rank = params.get("rank");
    if (!rank) return DEFAULT_RANK_TYPE;
    // 旧 slug 兼容
    if (Object.hasOwn(LEGACY_SLUG_MAP, rank)) rank = LEGACY_SLUG_MAP[rank];
    if (Object.hasOwn(RANK_TYPES, rank)) return rank;
    // 非法 ?rank= 参数：静默回退默认榜，并用 replaceState 清掉地址栏坏参数
    params.delete("rank");
    const cleanSearch = params.toString();
    const cleanUrl = window.location.pathname + (cleanSearch ? `?${cleanSearch}` : '') + window.location.hash;
    history.replaceState(null, '', cleanUrl);
    return DEFAULT_RANK_TYPE;
}

function getRankConfig() {
    return RANK_TYPES[getRankTypeFromURL()];
}

// 为当前页面URL设置rank参数，用于导航切换
function buildPageURL(rankType) {
    const url = new URL(window.location.href);
    url.search = '';  // F2-9: 跨页跳转只保留 rank，去掉 cat/type 等无关 query
    url.searchParams.set("rank", rankType);
    return url.pathname + url.search;
}

// 获取某平台的所有榜单 slug 列表
function platformRankSlugs(platformKey) {
    const plat = PLATFORMS[platformKey];
    if (!plat) return [];
    return Object.keys(plat.ranks).map(rid => `${platformKey}_${rid}`);
}

// 页面加载后，为所有硬编码的导航链接追加当前 rank 参数
document.addEventListener('DOMContentLoaded', () => {
    const currentRank = getRankTypeFromURL();
    document.querySelectorAll('.trend-link-btn[href]').forEach(link => {
        const href = link.getAttribute('href');
        if (href && !href.includes('rank=')) {
            const sep = href.includes('?') ? '&' : '?';
            link.setAttribute('href', `${href}${sep}rank=${currentRank}`);
        }
    });
});
