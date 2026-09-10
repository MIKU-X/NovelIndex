/**
 * export.js — 数据导出核心逻辑
 * 支持 CSV、Excel (.xlsx)、JSON 三种格式
 */

// 全局数据存储（由 app.js 或 author.js 设置）
// 使用合并策略，避免覆盖其他脚本已设置的数据
window.authorData = Object.assign({
    themeTrends: null,
    competitiveAnalysis: null,
    readerProfile: null,
    creationSuggestions: null,
    latestRanks: null,
}, window.authorData || {});

/**
 * 显示 Toast 通知
 * 使用 .copy-toast 类名与 app.js 保持一致
 */
function showToast(message) {
    let toast = document.querySelector('.copy-toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.className = 'copy-toast';
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2000);
}

// N69: xlsx 来自第三方 CDN（单点依赖）。CSP 禁止内联 onerror，改为脚本加载后
// 主动检测：CDN 被墙/失效时（XLSX 未就绪）给用户一个可见提示，
// JSON/CSV 导出不受影响，避免静默少一个导出能力。
(function () {
    if (typeof XLSX !== 'undefined') return;
    const notify = () => {
        if (typeof XLSX === 'undefined' && typeof showToast === 'function') {
            showToast('Excel 导出库加载失败（CDN 不可用），JSON/CSV 导出不受影响');
        }
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', notify);
    } else {
        notify();
    }
})();

/**
 * 消毒导出值，防 CSV/Excel 公式注入（F2-1）
 * 以 = + - @ 或制表符/回车开头的字符串前置单引号中性化，避免 Excel 打开时执行公式
 */
function sanitizeSpreadsheetValue(value) {
    if (typeof value === 'string' && /^[=+\-@\t\r]/.test(value)) {
        return `'${value}`;
    }
    return value;
}

/**
 * 消毒导出数组（深拷贝），供 json_to_sheet 使用，防公式注入
 */
function sanitizeSheetRows(rows) {
    if (!Array.isArray(rows)) return rows;
    return rows.map(row => {
        const out = {};
        for (const [k, v] of Object.entries(row)) {
            out[k] = sanitizeSpreadsheetValue(v);
        }
        return out;
    });
}

/**
 * 下载文件
 */
function downloadFile(content, filename, mimeType) {
    if (document.body.dataset?.demo === 'true') filename = `演示数据_${filename}`;
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

/**
 * 导出为 JSON
 */
function exportToJSON(data, filename) {
    const content = JSON.stringify(data, null, 2);
    downloadFile(content, `${filename}.json`, 'application/json');
    showToast(`已导出 ${filename}.json`);
}

/**
 * 导出为 CSV
 */
function exportToCSV(data, filename) {
    if (!Array.isArray(data) || data.length === 0) {
        showToast('没有可导出的数据');
        return;
    }

    // 获取所有列名（L35: 合并所有行的键，避免仅取首行键导致丢列）
    const headers = [...data.reduce((acc, row) => {
        Object.keys(row || {}).forEach(k => acc.add(k));
        return acc;
    }, new Set())];

    // 生成 CSV 内容
    const csvRows = [
        headers.join(','),  // 表头
        ...data.map(row =>
            headers.map(header => {
                let value = row[header];
                if (value === null || value === undefined) value = '';
                if (typeof value === 'object') value = JSON.stringify(value);
                // 防公式注入：以 = + - @ 或制表符/回车开头的前置单引号
                value = sanitizeSpreadsheetValue(String(value));
                // 转义引号和逗号
                value = value.replace(/"/g, '""');
                // #13-3(0902): 引号判定补 \r——RFC 4180 要求含 CR 的字段加引号，
                // 此前中间孤立 CR（无配对 LF）不加引号，Excel 打开会错位
                if (value.includes(',') || value.includes('"') || value.includes('\n') || value.includes('\r')) {
                    value = `"${value}"`;
                }
                return value;
            }).join(',')
        )
    ];

    // 添加 BOM 以支持中文（显式转义，避免不可见字面字符被误删）
    const bom = '\ufeff';
    const content = bom + csvRows.join('\n');
    downloadFile(content, `${filename}.csv`, 'text/csv;charset=utf-8');
    showToast(`已导出 ${filename}.csv`);
}

/**
 * 导出为 Excel
 */
function exportToExcel(data, filename) {
    if (typeof XLSX === 'undefined') {
        // N69: 提示语明确 CDN 依赖与 JSON/CSV 替代路径
        showToast('Excel 导出库未加载（CDN 不可用），请检查网络或改用 JSON/CSV 导出');
        return;
    }

    const wb = XLSX.utils.book_new();

    if (Array.isArray(data)) {
        // 单个数据表
        const ws = XLSX.utils.json_to_sheet(sanitizeSheetRows(data));
        XLSX.utils.book_append_sheet(wb, ws, 'Sheet1');
    } else if (typeof data === 'object') {
        // 多 Sheet 工作簿
        for (const [sheetName, sheetData] of Object.entries(data)) {
            if (Array.isArray(sheetData) && sheetData.length > 0) {
                const ws = XLSX.utils.json_to_sheet(sanitizeSheetRows(sheetData));
                // N68: Sheet 名先过滤 Excel 非法字符（: \ / ? * [ ]）再截断 31 字符，
                // 防止非法字符导致 book_append_sheet 抛错/静默改名
                const safeName = (sheetName || '').replace(/[\\\/\?\*\[\]:]/g, '_').substring(0, 31) || 'Sheet';
                XLSX.utils.book_append_sheet(wb, ws, safeName); // Sheet 名称最长 31 字符
            }
        }
    }

    const demoPrefix = document.body.dataset?.demo === 'true' ? '演示数据_' : '';
    XLSX.writeFile(wb, `${demoPrefix}${filename}.xlsx`);
    showToast(`已导出 ${filename}.xlsx`);
}

/**
 * 扁平化题材趋势数据
 */
function flattenThemeTrends(data) {
    if (!data || !data.periods) return [];

    const rows = [];
    for (const [periodKey, periodData] of Object.entries(data.periods)) {
        for (const theme of (periodData.themes || [])) {
            rows.push({
                '时间窗口': periodData.period || periodKey,
                '题材': theme.name,
                '总命中数': theme.total_count,
                '覆盖分类数': theme.category_count,
                '趋势方向': theme.trend_direction,
                '趋势变化%': theme.trend_pct,
                '分类列表': (theme.categories || []).join('、'),
            });
        }
    }
    return rows;
}

/**
 * 扁平化竞品分析数据
 */
function flattenCompetitiveAnalysis(data) {
    if (!data || !data.categories) return [];

    const rows = [];
    for (const [catName, catData] of Object.entries(data.categories)) {
        for (const book of (catData.top10_books || [])) {
            const keywords = (catData.shared_keywords || [])
                .filter(k => k.presence >= 0.3)
                .map(k => k.keyword)
                .join('、');

            rows.push({
                '分类': catName,
                '排名': book.rank,
                '书名': book.title,
                '作者': book.author,
                [readsLabel(book)]: book.reads,
                '共性关键词': keywords,
                '标题平均字数': catData.title_patterns?.avg_length || '',
                '含标点比例': catData.title_patterns?.has_punctuation || '',
                '常见标题结构': (catData.title_patterns?.common_structures || []).join('、'),
                '简介钩子': (catData.intro_patterns?.common_hooks || []).join('、'),
                '背景设定': (catData.intro_patterns?.common_settings || []).join('、'),
                ...(book.metric_type === 'word_count' ? { '字数采集说明': metricObservation(book) } : {}),
            });
        }
    }
    return rows;
}

/**
 * 扁平化读者画像数据
 */
function flattenReaderProfile(data) {
    if (!data || !data.genre_profiles) return [];

    const rows = [];
    for (const [genreName, profile] of Object.entries(data.genre_profiles)) {
        // 热门元素
        for (const element of (profile.top_elements || [])) {
            rows.push({
                '分类': genreName,
                '数据类型': '热门元素',
                '项目': element.keyword,
                '数值': element.weight,
                '百分比': '',
            });
        }

        // 情感偏好
        const emotions = profile.emotional_preference || {};
        for (const [emotion, value] of Object.entries(emotions)) {
            const emotionNames = { sweet: '甜宠', angst: '虐恋', power_fantasy: '爽文', daily_life: '日常' };
            rows.push({
                '分类': genreName,
                '数据类型': '情感元素',
                '项目': emotionNames[emotion] || emotion,
                '数值': value,
                '百分比': `${Math.round(value * 100)}%`,
            });
        }

        // 金手指偏好
        for (const gf of (profile.golden_finger_preference || [])) {
            rows.push({
                '分类': genreName,
                '数据类型': '金手指元素',
                '项目': gf.type,
                '数值': gf.frequency,
                '百分比': `${Math.round(gf.frequency * 100)}%`,
            });
        }

        // 背景设定偏好
        const settings = profile.setting_preference || {};
        for (const [setting, value] of Object.entries(settings)) {
            rows.push({
                '分类': genreName,
                '数据类型': '背景设定',
                '项目': setting,
                '数值': value,
                '百分比': `${Math.round(value * 100)}%`,
            });
        }
    }
    return rows;
}

/**
 * 扁平化创作建议数据
 */
function flattenCreationSuggestions(data) {
    if (!data || !data.periods) return [];

    const rows = [];
    for (const [periodKey, periodData] of Object.entries(data.periods)) {
        // 各分类建议
        for (const [genreName, suggestion] of Object.entries(periodData.genre_suggestions || {})) {
            rows.push({
                '时间窗口': periodKey === 'all' ? '全部' : `${periodKey}日`,
                '分类': genreName,
                '数据类型': '市场格局',
                '内容': suggestion.market_position || '',
            });
            rows.push({
                '时间窗口': periodKey === 'all' ? '全部' : `${periodKey}日`,
                '分类': genreName,
                '数据类型': '推荐题材',
                '内容': (suggestion.recommended_themes || []).join('、'),
            });
            rows.push({
                '时间窗口': periodKey === 'all' ? '全部' : `${periodKey}日`,
                '分类': genreName,
                '数据类型': '差异化机会',
                '内容': (suggestion.gap_opportunities || []).join('；'),
            });
            rows.push({
                '时间窗口': periodKey === 'all' ? '全部' : `${periodKey}日`,
                '分类': genreName,
                '数据类型': '书名参考',
                '内容': (suggestion.title_suggestions || []).join('、'),
            });
            rows.push({
                '时间窗口': periodKey === 'all' ? '全部' : `${periodKey}日`,
                '分类': genreName,
                '数据类型': '饱和题材',
                '内容': (suggestion.avoid_themes || []).join('、'),
            });
            rows.push({
                '时间窗口': periodKey === 'all' ? '全部' : `${periodKey}日`,
                '分类': genreName,
                '数据类型': '综合建议',
                '内容': suggestion.summary || '',
            });
        }

        // 跨分类机会
        for (const opp of (periodData.cross_genre_opportunities || [])) {
            rows.push({
                '时间窗口': periodKey === 'all' ? '全部' : `${periodKey}日`,
                '分类': '跨分类',
                '数据类型': '题材组合',
                '内容': `${opp.combination} - ${opp.reasoning}`,
            });
        }
    }
    return rows;
}

/**
 * 扁平化排行榜数据
 */
function flattenRankings(data) {
    if (!data || !data.categories) return [];

    const metricCol = metricLabelFromData(data);
    const rows = [];
    for (const cat of data.categories) {
        for (let i = 0; i < (cat.books || []).length; i++) {
            const book = cat.books[i];
            rows.push({
                '分类': cat.name,
                '排名': i + 1,
                '书名': book.title,
                '作者': book.author,
                [metricCol]: book.reads,
                ...(book.metric_type === 'word_count' ? { '字数采集说明': metricObservation(book) } : {}),
                '简介': (book.intro || '').substring(0, 100),
                '链接': book.url || '',
            });
        }
    }
    return rows;
}

/**
 * 获取当前榜单类型名称（用于导出文件名）
 */
function getRankTypeName() {
    try {
        const config = getRankConfig();
        return config ? config.name : '';
    } catch (e) {
        return '';
    }
}

/**
 * #13-8(0902): 本地时区日期串（YYYY-MM-DD）——toISOString 是 UTC，
 * 北京时间 0-8 点导出的文件名日期会是前一天
 */
function localDateString() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/**
 * 导出指定类型的数据
 */
function exportData(format, type) {
    const data = window.authorData;
    let exportContent;
    let filename;
    const rawData = {
        theme_trends: data.themeTrends,
        competitive_analysis: data.competitiveAnalysis,
        reader_profile: data.readerProfile,
        creation_suggestions: data.creationSuggestions,
        raw_rankings: data.latestRanks,
    };
    const sourceDate = rawData[type]?.date;
    const date = typeof sourceDate === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(sourceDate)
        ? sourceDate : localDateString();
    const rankName = getRankTypeName();
    const prefixStr = rankName ? `${rankName}_` : '';

    switch (type) {
        case 'theme_trends':
            exportContent = flattenThemeTrends(data.themeTrends);
            filename = `${prefixStr}题材热度_${date}`;
            break;

        case 'competitive_analysis':
            exportContent = flattenCompetitiveAnalysis(data.competitiveAnalysis);
            filename = `${prefixStr}竞品分析_${date}`;
            break;

        case 'reader_profile':
            exportContent = flattenReaderProfile(data.readerProfile);
            filename = `${prefixStr}榜单样本分析_${date}`;
            break;

        case 'creation_suggestions':
            exportContent = flattenCreationSuggestions(data.creationSuggestions);
            filename = `${prefixStr}创作建议_${date}`;
            break;

        case 'raw_rankings':
            exportContent = flattenRankings(data.latestRanks);
            filename = `${prefixStr}排行榜数据_${date}`;
            break;

        default:
            showToast('未知的数据类型');
            return;
    }

    if (!exportContent || exportContent.length === 0) {
        showToast('没有可导出的数据，请先加载数据');
        return;
    }

    switch (format) {
        case 'csv':
            exportToCSV(exportContent, filename);
            break;
        case 'excel':
            exportToExcel(exportContent, filename);
            break;
        case 'json':
            // JSON 导出原始数据（M14/L7: 显式包含 raw_rankings 键，避免依赖 ?? 回退）
            exportToJSON(rawData[type] ?? exportContent, filename);
            break;
    }
}

/**
 * 导出全部数据为 Excel 多 Sheet 工作簿
 */
function exportAll() {
    const data = window.authorData;
    const date = localDateString();
    const rankName = getRankTypeName();
    const prefixStr = rankName ? `${rankName}_` : '';
    // N22: 平台名动态拼接（勿默认写死番茄）
    let platformName = '未知平台';
    try {
        const cfg = getRankConfig();
        if (cfg && cfg.platform_name) platformName = cfg.platform_name;
    } catch (e) { /* 保留默认 */ }

    const sheets = {};
    const emptySheets = [];

    // Sheet 1: 排行榜数据
    const rankings = flattenRankings(data.latestRanks);
    if (rankings.length > 0) sheets['排行榜数据'] = rankings;
    else emptySheets.push('排行榜数据');

    // Sheet 2: 题材热度
    const themes = flattenThemeTrends(data.themeTrends);
    if (themes.length > 0) sheets['题材热度'] = themes;
    else emptySheets.push('题材热度');

    // Sheet 3: 竞品分析
    const competitive = flattenCompetitiveAnalysis(data.competitiveAnalysis);
    if (competitive.length > 0) sheets['竞品分析'] = competitive;
    else emptySheets.push('竞品分析');

    // Sheet 4: 读者画像
    const profile = flattenReaderProfile(data.readerProfile);
    if (profile.length > 0) sheets['榜单样本分析'] = profile;
    else emptySheets.push('榜单样本分析');

    // Sheet 5: 创作建议
    const suggestions = flattenCreationSuggestions(data.creationSuggestions);
    if (suggestions.length > 0) sheets['创作建议'] = suggestions;
    else emptySheets.push('创作建议');

    if (Object.keys(sheets).length === 0) {
        showToast('没有可导出的数据，请先加载数据');
        return;
    }

    // 提示缺失的 sheet
    if (emptySheets.length > 0) {
        showToast(`已导出，但以下数据为空：${emptySheets.join('、')}`);
    }

    exportToExcel(sheets, `${prefixStr}${platformName}分析_${date}`);
}

// 绑定导出按钮事件
document.addEventListener('DOMContentLoaded', () => {
    // 单模块导出按钮
    document.querySelectorAll('.export-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const format = btn.dataset.format;
            const type = btn.dataset.type;
            exportData(format, type);
        });
    });

    // 全局导出按钮
    const exportAllBtn = document.getElementById('export-all-btn');
    if (exportAllBtn) {
        exportAllBtn.addEventListener('click', exportAll);
    }
});
