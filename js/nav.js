/* ============================================================
   顶部导航栏组件 + API配置弹窗
   在所有页面注入统一的导航栏
   ============================================================ */

(function () {
    const currentRank = getRankTypeFromURL();

    // 确定当前页面类型
    const page = location.pathname.split("/").pop() || "index.html";
    const isTrend = page === "trend.html";
    const isAuthor = page === "author.html";

    function getPageBase() {
        if (isTrend) return "trend.html";
        if (isAuthor) return "author.html";
        return "index.html";
    }

    // 构建导航栏HTML：两级导航 = 平台选择 + 平台内榜单 tab
    function buildNavHTML() {
        const currentConfig = getRankConfig();
        const currentPlatform = currentConfig.platform;

        // 平台选择 tab（仅显示有榜单的平台）
        const platformTabs = Object.entries(PLATFORMS)
            .filter(([key, plat]) => Object.keys(plat.ranks).length > 0)
            .map(([key, plat]) => {
                const active = key === currentPlatform ? " active" : "";
                // 切换平台时默认选该平台第一个榜单
                const firstSlug = Object.keys(plat.ranks).map(r => `${key}_${r}`)[0];
                const targetSlug = (key === currentPlatform) ? currentRank : firstSlug;
                const href = buildPageURL(targetSlug).replace(page, getPageBase());
                const styleAttr = active ? ` style="border-color:${plat.color}"` : "";
                return `<a class="nav-platform-tab${active}" data-platform="${key}" href="${href}" title="${plat.name}" aria-label="${plat.name}"${styleAttr}><span class="nav-platform-icon" style="color:${plat.color}">${plat.icon}</span><span class="nav-platform-name">${plat.name}</span></a>`;
            }).join("");

        // 当前平台内的榜单 tab
        const plat = PLATFORMS[currentPlatform] || { ranks: {} };
        const rankTabs = Object.entries(plat.ranks).map(([rankId, rank]) => {
            const slug = `${currentPlatform}_${rankId}`;
            const active = slug === currentRank ? " active" : "";
            const href = buildPageURL(slug).replace(page, getPageBase());
            return `<a class="nav-tab${active}" href="${href}"${active ? ' aria-current="page"' : ''}>${rank.name}</a>`;
        }).join("");

        const extraLinks = [];
        // N68: currentRank 拼入 href 前统一 escapeAttr（当前非法值会被回退默认榜，
        // 属防御性对齐，与书卡 href 白名单同一品味）
        const safeRank = escapeAttr(currentRank);
        if (!isTrend) extraLinks.push(`<a class="nav-extra-link" href="trend.html?rank=${safeRank}">趋势</a>`);
        if (!isAuthor) extraLinks.push(`<a class="nav-extra-link" href="author.html?rank=${safeRank}">灵感</a>`);
        if (isTrend || isAuthor) extraLinks.push(`<a class="nav-extra-link" href="index.html?rank=${safeRank}">榜单</a>`);

        return `
        <div class="top-nav-inner">
            <div class="nav-brand">
                <span class="nav-brand-icon">📖</span>
                <span class="nav-brand-text">小说指数</span>
            </div>
            <div class="nav-platforms">${platformTabs}</div>
            <div class="nav-tabs">${rankTabs}</div>
            <div class="nav-right">
                ${extraLinks.join("")}
                <button class="nav-settings-btn" id="nav-settings-btn" title="API 配置" aria-label="设置">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="3"></circle>
                        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
                    </svg>
                </button>
            </div>
        </div>`;
    }

    // 构建API配置弹窗HTML
    function buildModalHTML() {
        return `
        <div class="api-modal-overlay" id="api-modal-overlay">
            <div class="api-modal" role="dialog" aria-modal="true" aria-labelledby="api-modal-title">
                <div class="api-modal-header">
                    <h3 id="api-modal-title">API 配置</h3>
                    <button class="api-modal-close" id="api-modal-close" aria-label="关闭设置">&times;</button>
                </div>
                <div class="api-modal-body">
                    <p class="api-modal-desc">AI 分析在生成榜单数据时完成，浏览器不需要保存 API Key。此处用于清理旧版本在本机保存的配置。</p>
                    <p class="api-modal-desc" id="api-legacy-hint" style="display:none;color:var(--accent,#c45)">检测到本机曾存过密钥类字段，点「清除本机记录」即可删除。</p>
                </div>
                <div class="api-modal-footer">
                    <button class="api-btn api-btn-clear" id="api-btn-clear">清除本机记录</button>
                    <button class="api-btn api-btn-save" id="api-btn-save">关闭</button>
                </div>
            </div>
        </div>`;
    }

    // 渲染导航栏
    function renderNav() {
        const navEl = document.getElementById("top-nav");
        if (!navEl) return;
        navEl.innerHTML = buildNavHTML();
        navEl.querySelector('.nav-tab.active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });

        // 插入弹窗到body
        document.body.insertAdjacentHTML("beforeend", buildModalHTML());

        // 绑定事件
        bindModalEvents();
    }

    // N12: localStorage key 迁移 fanqie_api_config → novelindex_api_config
    const API_CONFIG_KEY = 'novelindex_api_config';
    const API_CONFIG_KEY_LEGACY = 'fanqie_api_config';

    // N60: 不再读写 API Key；打开设置时顺带清掉历史误存
    // F2-3: 页面加载即清除 key，故模块级缓存"是否曾存在 key"，弹窗时读缓存决定提示
    let hadLegacyApiSecret = false;

    function purgeLegacyApiSecrets() {
        try {
            let had = false;
            for (const key of [API_CONFIG_KEY, API_CONFIG_KEY_LEGACY]) {
                const raw = localStorage.getItem(key);
                if (!raw) continue;
                had = true;
                try {
                    const obj = JSON.parse(raw);
                    if (obj && (obj.apiKey || obj.baseUrl || obj.model)) had = true;
                } catch { /* ignore */ }
                localStorage.removeItem(key);
            }
            return had;
        } catch {
            return false;
        }
    }

    function bindModalEvents() {
        const overlay = document.getElementById("api-modal-overlay");
        const settingsBtn = document.getElementById("nav-settings-btn");
        const closeBtn = document.getElementById("api-modal-close");
        const saveBtn = document.getElementById("api-btn-save");
        const clearBtn = document.getElementById("api-btn-clear");

        if (!settingsBtn || !overlay) return;

        // 页面加载即清除历史密钥，避免共机残留；同时记录是否曾存在过，供弹窗提示
        hadLegacyApiSecret = purgeLegacyApiSecrets();

        function openModal() {
            const hint = document.getElementById("api-legacy-hint");
            if (hint) hint.style.display = hadLegacyApiSecret ? "block" : "none";
            overlay.classList.add("open");
            document.querySelectorAll('#top-nav, .app-container, .trend-shell, .author-shell').forEach(el => { el.inert = true; });
            document.body.classList.add('modal-open');
            closeBtn.focus();
        }

        function closeModal() {
            overlay.classList.remove("open");
            document.querySelectorAll('#top-nav, .app-container, .trend-shell, .author-shell').forEach(el => { el.inert = false; });
            document.body.classList.remove('modal-open');
            settingsBtn.focus();
        }

        settingsBtn.addEventListener("click", openModal);
        closeBtn.addEventListener("click", closeModal);
        overlay.addEventListener("click", (e) => {
            if (e.target === overlay) closeModal();
        });

        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && overlay.classList.contains("open")) {
                closeModal();
            } else if (e.key === 'Tab' && overlay.classList.contains('open')) {
                if (e.shiftKey && document.activeElement === closeBtn) {
                    e.preventDefault();
                    saveBtn.focus();
                } else if (!e.shiftKey && document.activeElement === saveBtn) {
                    e.preventDefault();
                    closeBtn.focus();
                }
            }
        });

        saveBtn.addEventListener("click", () => {
            closeModal();
        });

        clearBtn.addEventListener("click", () => {
            purgeLegacyApiSecrets();
            hadLegacyApiSecret = false;
            const hint = document.getElementById("api-legacy-hint");
            if (hint) hint.style.display = "none";
            if (typeof showToast === "function") showToast("本机 API 记录已清除");
        });
    }

    // 动态更新页面标题 + 主题配色
    function updatePageTitle() {
        const cfg = getRankConfig();
        const pageTitle = isTrend ? '类型风向标' : isAuthor ? '创作灵感' : '榜单';
        document.title = `${cfg.platform_name}${cfg.name} · ${pageTitle} · 小说指数`;
        // 按当前平台主题色设置 CSS 变量
        if (cfg.color) {
            document.documentElement.style.setProperty("--platform-color", cfg.color);
        }
        const subtitle = document.querySelector(".sidebar-subtitle");
        if (subtitle) {
            const nameNoGender = cfg.name.replace(/^(男频|女频)/, "");
            subtitle.textContent = `${cfg.platform_name} · ${cfg.gender === "综合" ? "" : cfg.gender}${nameNoGender}追踪`;
        }
    }

    // DOM Ready后执行
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => { renderNav(); updatePageTitle(); });
    } else {
        renderNav();
        updatePageTitle();
    }
})();
