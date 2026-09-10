/**
 * 共享工具函数 — 供 app.js / trend.js / author.js / export.js 使用
 */

function escapeHtml(str) {
    return String(str == null ? '' : str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function escapeAttr(str) {
    return escapeHtml(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** Clipboard API may be unavailable on a LAN HTTP preview. Report real success. */
async function copyText(text) {
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
            return true;
        }
    } catch (e) { /* Fall back when permission is denied. */ }
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    try {
        document.body.appendChild(textarea);
        textarea.select();
        return document.execCommand('copy');
    } catch (e) {
        return false;
    } finally {
        textarea.remove();
    }
}

function renderMarkdown(text) {
    if (!text) return '';
    let html = escapeHtml(text);
    html = html.replace(/^### (.+)$/gm, '<h3 style="font-size:1.05rem; margin:1em 0 0.5em; color:var(--text-primary);">$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2 style="font-size:1.15rem; margin:1em 0 0.5em; color:var(--text-primary);">$1</h2>');
    // 先用占位符替换 **粗体**，再处理 *斜体*，避免正则冲突
    const boldPlaceholders = [];
    html = html.replace(/\*\*(.+?)\*\*/g, (match, p1) => {
        const ph = `\x00B${boldPlaceholders.length}\x00`;
        boldPlaceholders.push(`<strong>${p1}</strong>`);
        return ph;
    });
    html = html.replace(/\*([^*]+?)\*/g, '<em>$1</em>');
    // 还原粗体占位符
    // #13-6(0902): 函数形式 replacement——字符串形式时 replacement 内的 $&、$' 等
    // 特殊序列会被 replace 解释为匹配引用，正文含 $ 的粗体文本还原出错（无 XSS，仅显示缺陷）
    boldPlaceholders.forEach((replacement, i) => {
        html = html.replace(`\x00B${i}\x00`, () => replacement);
    });
    html = html.replace(/《(.+?)》/g, '<span style="color:var(--accent);font-weight:500">《$1》</span>');
    html = html.replace(/^[-*] (.+)$/gm, '<span style="display:block;padding-left:1em;text-indent:-0.6em">• $1</span>');
    html = html.replace(/^(\d+)\. (.+)$/gm, '<span style="display:block;padding-left:1em;text-indent:-0.6em">$1. $2</span>');
    html = html.replace(/\n/g, '<br>');
    return html;
}

function parseReads(readsStr) {
    if (typeof readsStr !== 'string' && typeof readsStr !== 'number') return 0;
    const s = String(readsStr).trim().replace(/,/g, '').replace(/字$/, '').trim();
    const match = s.match(/^([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:e[+-]?[0-9]+)?)\s*([万亿KMG]?)$/i);
    if (!match) return 0;
    const multipliers = { '万': 10000, '亿': 100000000, K: 1000, M: 1000000, G: 1000000000 };
    const value = Number(match[1]) * (multipliers[match[2].toUpperCase()] || 1);
    return Number.isFinite(value) && value > 0 ? value : 0;
}

/**
 * 按 metric_type 返回 reads 的展示标签（D4治本：不同平台 reads 量纲不同，标签区分避免误导）。
 * metric_type: "views"=阅读量(番茄) / "word_count"=字数(起点) / "score"=积分(晋江)。
 * 旧数据无 metric_type 时按 reads 字符串特征兜底推断。
 */
function readsLabel(book) {
    const mt = book && book.metric_type;
    if (mt === 'views') return '在读';
    if (mt === 'word_count') return '字数';
    if (mt === 'score') return '积分';
    // 旧数据兜底：reads 含"万字"判字数，含 K/M/G 判积分，否则阅读量
    const r = book && book.reads ? String(book.reads) : '';
    if (r.includes('万字') || r.includes('字')) return '字数';
    if (/[KMG]$/.test(r.trim())) return '积分';
    return '在读';
}

/** N30: 平台级默认指标名（无 book 时用于排序/导出列名/UI） */
function platformMetricLabel(platform) {
    if (platform === 'qidian') return '字数';
    if (platform === 'jjwxc') return '积分';
    return '在读';
}

/** A cached value keeps its observation time, including in exports. */
function metricObservation(book) {
    if (!book || book.metric_type !== 'word_count') return '';
    if (!parseReads(book.reads)) return '字数待补全';
    const observed = String(book.word_count_observed_at || '');
    if (!/^\d{4}-\d{2}-\d{2}(?:T|$)/.test(observed) || !Number.isFinite(Date.parse(observed))) {
        return '字数采集时间未知';
    }
    const source = book.word_count_source === 'cache' ? '缓存 · ' : '';
    if (observed.length === 10) return `${source}字数采集日 ${observed}`;
    const date = new Date(observed).toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', hour12: false
    });
    return `${source}字数采集于 ${date}（北京时间）`;
}

function dataQualityMessage(data) {
    const q = data && data.quality;
    if (!q || typeof q !== 'object') return '';
    const count = key => Number.isFinite(Number(q[key])) ? Math.max(0, Math.floor(Number(q[key]))) : 0;
    const parts = [];
    if (q.rank_status === 'partial') parts.push('部分分类尚未抓取完成');
    if (count('metrics_missing')) parts.push(`${count('metrics_missing')}/${count('book_count')} 本的指标待补全`);
    if (count('metrics_cached')) parts.push(`${count('metrics_cached')} 本字数使用缓存，采集时间见书卡`);
    if (count('authors_missing')) parts.push(`${count('authors_missing')} 本缺作者`);
    if (count('intros_missing')) parts.push(`${count('intros_missing')} 本缺简介`);
    return parts.join('；');
}

/** 从数据样本推断指标列名 */
function metricLabelFromData(data) {
    const books = [];
    if (data && Array.isArray(data.categories)) {
        for (const cat of data.categories) {
            if (cat.books && cat.books[0]) {
                books.push(cat.books[0]);
                break;
            }
        }
    }
    if (books[0]) return readsLabel(books[0]);
    try {
        const cfg = typeof getRankConfig === 'function' ? getRankConfig() : null;
        if (cfg && cfg.platform) return platformMetricLabel(cfg.platform);
    } catch (e) { /* ignore */ }
    return '在读';
}
// 日期索引可能先于 latest 更新；历史范围以已构建的最新快照为上限。
function normalizeSnapshotDates(dates, latestDate) {
    const valid = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value);
    const available = Array.isArray(dates) ? dates.filter(valid) : [];
    if (valid(latestDate)) {
        return [...new Set([...available.filter(date => date <= latestDate), latestDate])].sort();
    }
    return [...new Set(available)].sort();
}

/** Load only the requested number of observed trend days; reuse prior requests. */
function createTrendWindowLoader(dates, fetchDay) {
    const available = [...new Set(dates)].sort();
    const cache = new Map();
    const fetchOne = day => {
        if (!cache.has(day)) {
            cache.set(day, Promise.resolve().then(() => fetchDay(day)).then(item => {
                if (!item || item.date !== day || !item.trends || typeof item.trends !== 'object') return null;
                return { date: day, prevDate: item.prev_date, trends: item.trends };
            }).catch(() => null));
        }
        return cache.get(day);
    };
    return async days => {
        const limit = days === 'all' ? Infinity : Math.max(1, Number(days) || 7);
        const rows = [];
        let end = available.length;
        while (end > 0 && rows.length < limit) {
            const start = Math.max(0, end - Math.min(6, limit - rows.length));
            const batch = await Promise.all(available.slice(start, end).map(fetchOne));
            rows.push(...batch.filter(Boolean));
            end = start;
        }
        return rows.sort((a, b) => a.date.localeCompare(b.date));
    };
}
