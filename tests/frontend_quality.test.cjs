const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const root = path.join(__dirname, '..');

function loadRankConfig(search) {
    const location = new URL(`https://example.invalid/index.html${search}`);
    const sandbox = vm.createContext({
        window: { location }, URL, URLSearchParams,
        document: { addEventListener() {} },
        history: { replaceState(_state, _title, url) { sandbox.cleanedURL = url; } },
    });
    vm.runInContext(fs.readFileSync(path.join(root, 'js/config.js'), 'utf8'), sandbox);
    return sandbox;
}

test('all nine rank routes resolve to their own configuration', () => {
    const slugs = [
        'fanqie_male_new', 'fanqie_male_read', 'fanqie_female_new', 'fanqie_female_read',
        'qidian_hotsales', 'qidian_signnewbook', 'qidian_newsign', 'qidian_sanjiang', 'jjwxc_month',
    ];
    for (const slug of slugs) {
        assert.equal(loadRankConfig(`?rank=${slug}`).getRankConfig().prefix, slug);
    }
});

test('legacy rank links keep working', () => {
    for (const legacy of ['male_new', 'male_read', 'female_new', 'female_read']) {
        assert.equal(loadRankConfig(`?rank=${legacy}`).getRankConfig().prefix, `fanqie_${legacy}`);
    }
});

test('unknown and inherited object keys fall back without breaking the page', () => {
    for (const invalid of ['unknown', '__proto__', 'constructor', 'toString', 'hasOwnProperty']) {
        const sandbox = loadRankConfig(`?rank=${invalid}&cat=fixture#books`);
        assert.equal(sandbox.getRankConfig().prefix, 'fanqie_female_new', invalid);
        assert.equal(sandbox.cleanedURL, '/index.html?cat=fixture#books');
    }
});

const context = vm.createContext({
    window: {}, document: { addEventListener() {} }, XLSX: {},
});

test('untrusted HTML and spreadsheet formulas stay inert', () => {
    const payload = '<img src=x onerror=alert(1)>';
    assert.equal(context.escapeHtml(payload), '&lt;img src=x onerror=alert(1)&gt;');
    assert.doesNotMatch(context.renderMarkdown('**' + payload + '**'), /<img/);
    for (const value of ['=1+1', '+SUM(A1:A2)', '-1+2', '@SUM(A1:A2)', '\t=1+1']) {
        assert.ok(context.sanitizeSpreadsheetValue(value).startsWith("'"), value);
    }
});
vm.runInContext(fs.readFileSync(path.join(root, 'js/utils.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(path.join(root, 'js/export.js'), 'utf8'), context);

test('trend windows fetch seven days initially and reuse cached days when expanded', async () => {
    const days = Array.from({ length: 30 }, (_, i) => `2026-01-${String(i + 1).padStart(2, '0')}`);
    const requested = [];
    const load = context.createTrendWindowLoader(days, async day => {
        requested.push(day);
        return { date: day, trends: { fixture: {} } };
    });
    assert.equal((await load(7)).length, 7);
    assert.equal(requested.length, 7);
    assert.equal((await load(14)).length, 14);
    assert.equal(requested.length, 14);
    await load(7);
    assert.equal(requested.length, 14);
    assert.equal((await load('all')).length, 30);
    assert.equal(requested.length, 30);
});

test('sparse trend history fills from older observations with at most six requests at once', async () => {
    const days = Array.from({ length: 20 }, (_, i) => `2026-01-${String(i + 1).padStart(2, '0')}`);
    let active = 0;
    let peak = 0;
    const load = context.createTrendWindowLoader(days, async day => {
        active += 1;
        peak = Math.max(peak, active);
        await new Promise(resolve => setImmediate(resolve));
        active -= 1;
        if (day === days[19]) throw new Error('missing');
        if (day === days[18]) return { date: 'wrong-date', trends: {} };
        return { date: day, trends: {} };
    });
    const rows = await load(7);
    assert.equal(rows.length, 7);
    assert.equal(rows.at(-1).date, days[17]);
    assert.ok(peak <= 6);
    await load('all');
    assert.ok(peak <= 6);
});

test('snapshot dates are sorted, unique and bounded by the built latest snapshot', () => {
    assert.deepEqual(Array.from(context.normalizeSnapshotDates([
        '2026-09-08', '2026-09-06', '2026-09-07', '2026-09-07', null, '../invalid',
    ], '2026-09-07')), ['2026-09-06', '2026-09-07']);
    assert.deepEqual(Array.from(context.normalizeSnapshotDates(null, '2026-09-07')), ['2026-09-07']);
    assert.deepEqual(Array.from(context.normalizeSnapshotDates('2026-09-07')), []);
});

test('historical export filenames retain the date of the displayed data', () => {
    const originalExport = context.exportToCSV;
    const originalData = context.window.authorData;
    let filename;
    try {
        context.exportToCSV = (_rows, name) => { filename = name; };
        context.window.authorData = { latestRanks: {
            date: '2026-09-06', categories: [{ name: 'fixture', books: [{ title: 'fixture' }] }],
        } };
        context.exportData('csv', 'raw_rankings');
        assert.match(filename, /排行榜数据_2026-09-06$/);
    } finally {
        context.exportToCSV = originalExport;
        context.window.authorData = originalData;
    }
});

test('metric parsing rejects nonfinite, negative and partially numeric values', () => {
    for (const value of ['NaN', 'Infinity', '1e309', '1e308万', '-2万', '12万unknown', true, [12]]) {
        assert.equal(context.parseReads(value), 0, String(value));
    }
    for (const [value, expected] of [['10.5万字', 105000], [' 1.2 G ', 1200000000], ['1,234', 1234], ['2e3', 2000]]) {
        assert.equal(context.parseReads(value), expected, value);
    }
});

test('clipboard works asynchronously and preserves the exact text', async () => {
    let copied = '';
    context.navigator = { clipboard: { async writeText(text) { copied = text; } } };
    assert.equal(await context.copyText('fixture\n字数采集日 2026-09-06'), true);
    assert.equal(copied, 'fixture\n字数采集日 2026-09-06');
});

test('clipboard fallback handles unavailable API and reports failed copies', async () => {
    let removed = 0;
    const original = context.document;
    context.document = {
        createElement: () => ({ style: {}, select() {}, remove() { removed++; } }),
        body: { appendChild() {} }, execCommand: () => false,
    };
    try {
        context.navigator = {};
        assert.equal(await context.copyText('fixture'), false);
        context.navigator = { clipboard: { async writeText() { throw new Error('denied'); } } };
        context.document.execCommand = () => true;
        assert.equal(await context.copyText('fixture'), true);
        assert.equal(removed, 2);
    } finally {
        context.document = original;
    }
});

test('missing metrics and unknown observation times remain explicit', () => {
    assert.equal(context.metricObservation({ metric_type: 'word_count', reads: '' }), '字数待补全');
    assert.equal(context.metricObservation({ metric_type: 'word_count', reads: '12万字' }), '字数采集时间未知');
    assert.equal(context.metricObservation({ metric_type: 'views', reads: '12万' }), '');
});

test('cache timestamps are not replaced by the snapshot date', () => {
    const message = context.metricObservation({
        metric_type: 'word_count', reads: '12万字', word_count_source: 'cache',
        word_count_observed_at: '2026-09-06',
    });
    assert.equal(message, '缓存 · 字数采集日 2026-09-06');
});

test('quality notice describes missing details independently of ranking status', () => {
    const message = context.dataQualityMessage({ quality: {
        rank_status: 'complete', book_count: 420, metrics_missing: 154, metrics_cached: 3,
    } });
    assert.match(message, /154\/420/);
    assert.match(message, /3 本字数使用缓存/);
    assert.doesNotMatch(message, /分类尚未/);
});

test('ranking exports retain metric observation provenance', () => {
    const rows = context.flattenRankings({ platform: 'qidian', categories: [{ name: '玄幻', books: [{
        title: 'fixture', reads: '12万字', metric_type: 'word_count',
        word_count_observed_at: '2026-09-06', word_count_source: 'cache',
    }] }] });
    assert.equal(rows[0]['字数采集说明'], '缓存 · 字数采集日 2026-09-06');
});

test('competitive exports retain metric type and observation provenance', () => {
    const rows = context.flattenCompetitiveAnalysis({ categories: { '玄幻': { top10_books: [{
        title: 'fixture', reads: '12万字', metric_type: 'word_count',
        word_count_observed_at: '2026-09-06', word_count_source: 'cache',
    }] } } });
    assert.equal(rows[0]['字数'], '12万字');
    assert.equal(rows[0]['字数采集说明'], '缓存 · 字数采集日 2026-09-06');
});
