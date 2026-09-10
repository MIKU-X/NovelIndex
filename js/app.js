document.addEventListener('DOMContentLoaded', () => {
    const rankConfig = getRankConfig();
    const prefix = rankConfig.prefix;
    // N30: 排序下拉标签随平台 metric
    const sortReadsOpt = document.getElementById('sort-reads-option');
    if (sortReadsOpt) {
        sortReadsOpt.textContent = `按${platformMetricLabel(rankConfig.platform)}`;
    }

    const categoryList = document.getElementById('category-list');
    const waterfall = document.getElementById('books-waterfall');
    const updateDate = document.getElementById('update-date');
    const categoryTitle = document.getElementById('current-category-title');
    const trendStats = document.getElementById('trend-stats');
    const aiContent = document.getElementById('ai-content');
    const trendPanel = document.getElementById('trend-panel');
    const mobileMenuBtn = document.getElementById('mobile-menu-btn');
    const sidebar = document.getElementById('sidebar');
    const dateDisplay = document.getElementById('date-display');
    const datePickerBtn = document.getElementById('date-picker-btn');
    const dateInput = document.getElementById('date-input');
    const datePrevBtn = document.getElementById('date-prev');
    const dateNextBtn = document.getElementById('date-next');

    let allData = null;
    let availableDates = [];   // sorted list of "YYYY-MM-DD"
    let currentDateIndex = -1; // index into availableDates
    let currentCategory = null; // preserve selected category across date switches
    let loadRequestId = 0;     // E1: 数据加载请求序号，用于丢弃过期 fetch 回调，防止切换榜单/日期时旧请求污染新状态

    // Cache-busting: 每10分钟一个新key，避免浏览器缓存旧JSON
    const cacheBuster = `v=${Math.floor(Date.now() / 600000)}`;

    // ========== Copy Toast ==========
    // showToast 由 export.js 提供全局版本

    function copyBookInfo(e, book) {
        e.preventDefault();
        e.stopPropagation();
        // currentTarget 在异步剪贴板回调时已被浏览器清空，先保留按钮引用。
        const btn = e.currentTarget;
        const observation = metricObservation(book);
        const text = `${book.title}\n作者：${book.author}\n${readsLabel(book)}：${book.reads || '待补全'}${observation ? '\n' + observation : ''}\n简介：${book.intro || '无'}\n链接：${book.url || '无'}`;
        copyText(text).then(copied => {
            if (!copied) {
                showToast('复制失败，请手动选择文字复制');
                return;
            }
            btn.classList.add('copied');
            btn.textContent = '已复制';
            showToast('已复制');
            setTimeout(() => {
                btn.classList.remove('copied');
                btn.textContent = '复制信息';
            }, 1500);
        });
    }

    // ========== Mobile menu ==========
    let overlay = document.createElement('div');
    overlay.className = 'sidebar-overlay';
    document.body.appendChild(overlay);

    const mobileLayout = window.matchMedia('(max-width: 860px)');
    function setSidebarOpen(open) {
        const wasOpen = sidebar.classList.contains('open');
        open = open && mobileLayout.matches;
        sidebar.classList.toggle('open', open);
        overlay.classList.toggle('show', open);
        sidebar.inert = mobileLayout.matches && !open;
        document.querySelector('.main-content').inert = open;
        document.getElementById('top-nav').inert = open;
        document.body.classList.toggle('sidebar-open', open);
        mobileMenuBtn.setAttribute('aria-expanded', String(open));
        if (open) {
            (sidebar.querySelector('.active') || sidebar.querySelector('[tabindex]'))?.focus();
        } else if (wasOpen && mobileLayout.matches) {
            mobileMenuBtn.focus();
        }
    }
    mobileLayout.addEventListener('change', () => setSidebarOpen(false));
    setSidebarOpen(false);

    mobileMenuBtn.addEventListener('click', () => setSidebarOpen(!sidebar.classList.contains('open')));
    overlay.addEventListener('click', () => setSidebarOpen(false));
    document.getElementById('sidebar-close').addEventListener('click', () => setSidebarOpen(false));

    // Escape 键关闭侧边栏
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && sidebar.classList.contains('open')) {
            setSidebarOpen(false);
        }
    });

    // ========== Export Dropdown ==========
    const exportToggle = document.getElementById('export-toggle-btn');
    const exportMenu = document.getElementById('export-dropdown-menu');
    if (exportToggle && exportMenu) {
        exportToggle.addEventListener('click', (e) => {
            e.stopPropagation();
            exportMenu.classList.toggle('show');
        });
        exportMenu.querySelectorAll('.export-btn-item').forEach(btn => {
            btn.addEventListener('click', () => {
                exportMenu.classList.remove('show');
                if (typeof exportData === 'function') {
                    exportData(btn.dataset.format, 'raw_rankings');
                }
            });
        });
        document.addEventListener('click', () => exportMenu.classList.remove('show'));
    }

    // ========== 搜索筛选排序 ==========
    const searchInput = document.getElementById('book-search');
    const filterSelect = document.getElementById('book-filter');
    const sortSelect = document.getElementById('book-sort');

    // 防抖搜索
    let searchTimer = null;
    if (searchInput) {
        searchInput.addEventListener('input', () => {
            if (searchTimer) clearTimeout(searchTimer);
            searchTimer = setTimeout(() => {
                if (currentCategory) {
                    const cat = allData.categories.find(c => c.name === currentCategory);
                    if (cat) renderBooks(cat);
                }
            }, 200);
        });
    }

    if (filterSelect) {
        filterSelect.addEventListener('change', () => {
            if (currentCategory) {
                const cat = allData.categories.find(c => c.name === currentCategory);
                if (cat) renderBooks(cat);
            }
        });
    }

    if (sortSelect) {
        sortSelect.addEventListener('change', () => {
            if (currentCategory) {
                const cat = allData.categories.find(c => c.name === currentCategory);
                if (cat) renderBooks(cat);
            }
        });
    }

    // parseReads 已移至 js/utils.js

    function getFilteredBooks(cat) {
        const changeMap = buildPrevRankMap(cat.name);
        let books = (cat.books || []).map((book, index) => ({
            ...book,
            rank: index + 1,
            change: changeMap[book.title] || null,
            readsNum: parseReads(book.reads)
        }));

        // 搜索过滤（F2-2: 缺字段时以空串兜底，避免 TypeError 整榜崩溃）
        const query = searchInput ? searchInput.value.trim().toLowerCase() : '';
        if (query) {
            books = books.filter(b =>
                (b.title || '').toLowerCase().includes(query) ||
                (b.author || '').toLowerCase().includes(query)
            );
        }

        // 筛选
        const filter = filterSelect ? filterSelect.value : 'all';
        if (filter === 'new') {
            books = books.filter(b => b.change === 'new');
        } else if (filter === 'risers') {
            books = books.filter(b => b.change && b.change.startsWith('+'));
        } else if (filter === 'top10') {
            books = books.filter(b => b.rank <= 10);
        } else if (filter === 'top20') {
            books = books.filter(b => b.rank <= 20);
        }

        // 排序
        const sort = sortSelect ? sortSelect.value : 'rank';
        if (sort === 'reads') {
            books.sort((a, b) => b.readsNum - a.readsNum);
        } else if (sort === 'title') {
            books.sort((a, b) => a.title.localeCompare(b.title, 'zh'));
        }
        // sort === 'rank' 保持原序

        return books;
    }

    // ========== Date Navigation ==========
    function updateDateNav() {
        const isLatest = currentDateIndex === availableDates.length - 1;
        const isFirst = currentDateIndex <= 0;

        datePrevBtn.disabled = isFirst;
        dateNextBtn.disabled = isLatest;

        const currentDate = availableDates[currentDateIndex];
        dateDisplay.textContent = currentDate || '加载中...';

        // Highlight if viewing historical (non-latest) data
        if (isLatest) {
            datePickerBtn.classList.remove('is-historical');
        } else {
            datePickerBtn.classList.add('is-historical');
        }

        // Sync preset button active state
        updatePresetButtons();
    }

    // ========== Preset Buttons ==========
    const presetBtns = document.querySelectorAll('.preset-btn');

    function updatePresetButtons() {
        const isLatest = currentDateIndex === availableDates.length - 1;
        const isYesterday = availableDates.length >= 2 && currentDateIndex === availableDates.length - 2;

        presetBtns.forEach(btn => {
            const preset = btn.dataset.preset;
            if (preset === 'latest' && isLatest) {
                btn.classList.add('active');
            } else if (preset === 'yesterday' && isYesterday) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });
    }

    presetBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const preset = btn.dataset.preset;
            if (preset === 'latest' && availableDates.length > 0) {
                currentDateIndex = availableDates.length - 1;
                loadDateData(availableDates[currentDateIndex]);
            } else if (preset === 'yesterday' && availableDates.length >= 2) {
                currentDateIndex = availableDates.length - 2;
                loadDateData(availableDates[currentDateIndex]);
                // #13-5(0902): 「昨日」实为「上一快照日」——数据滞后多日时与真实昨日不是
                // 同一天，toast 提示实际查看的日期，避免把更旧的数据当昨日数据读
                const shownDate = availableDates[currentDateIndex];
                const _yd = new Date(Date.now() - 86400000);
                const realYesterday = `${_yd.getFullYear()}-${String(_yd.getMonth() + 1).padStart(2, '0')}-${String(_yd.getDate()).padStart(2, '0')}`;
                if (shownDate !== realYesterday) {
                    showToast(`已切换至上一快照日 ${shownDate}（最新数据为 ${availableDates[availableDates.length - 1]}）`);
                }
            }
        });
    });

    datePrevBtn.addEventListener('click', () => {
        if (currentDateIndex > 0) {
            currentDateIndex--;
            loadDateData(availableDates[currentDateIndex]);
        }
    });

    dateNextBtn.addEventListener('click', () => {
        if (currentDateIndex < availableDates.length - 1) {
            currentDateIndex++;
            loadDateData(availableDates[currentDateIndex]);
        }
    });

    datePickerBtn.addEventListener('click', () => {
        // Trigger native date picker
        dateInput.showPicker ? dateInput.showPicker() : dateInput.click();
    });

    dateInput.addEventListener('change', () => {
        const selected = dateInput.value; // YYYY-MM-DD
        if (!selected) return;
        const idx = availableDates.indexOf(selected);
        if (idx !== -1) {
            currentDateIndex = idx;
            loadDateData(selected);
        } else if (availableDates.length > 0) {
            // Find nearest available date and show friendly hint
            const nearest = availableDates.reduce((prev, curr) =>
                Math.abs(new Date(curr) - new Date(selected)) < Math.abs(new Date(prev) - new Date(selected)) ? curr : prev
            );
            const nearIdx = availableDates.indexOf(nearest);
            currentDateIndex = nearIdx;
            loadDateData(nearest);
            showToast(`${selected} 无数据，已跳转至最近的 ${nearest}`);
        }
    });

    // ========== Load dates index, then load latest ==========
    // 显示骨架屏
    function showSkeletonLoading() {
        waterfall.innerHTML = Array(6).fill('').map(() =>
            '<div class="skeleton skeleton-card"></div>'
        ).join('');
        trendStats.innerHTML = Array(3).fill('').map(() =>
            '<span class="skeleton skeleton-chip"></span>'
        ).join('');
    }
    showSkeletonLoading();

    // file:// 协议下 fetch 本地 JSON 会被浏览器 CORS 拦截，显式提示而非静默失败
    if (location.protocol === 'file:') {
        const mainContent = document.querySelector('.main-content');
        if (mainContent) {
            mainContent.innerHTML = '';
            const tip = document.createElement('div');
            tip.style.cssText = 'padding:2rem;text-align:center;color:var(--text-secondary);line-height:1.8;';
            tip.innerHTML = '⚠️ 无法直接以文件方式打开<br>请使用安全预览：<code style="background:var(--bg-card);padding:2px 6px;border-radius:4px">python scripts/serve_preview.py 8000</code><br>然后访问 <code style="background:var(--bg-card);padding:2px 6px;border-radius:4px">http://127.0.0.1:8000</code>';
            mainContent.appendChild(tip);
        }
        return;
    }

    fetch(`data/dates_${prefix}.json?${cacheBuster}`)
        .then(r => r.ok ? r.json() : Promise.reject('No dates.json'))
        .then(idx => {
            availableDates = normalizeSnapshotDates(idx?.dates);
            if (availableDates.length > 0) {
                // Set min/max for native date input
                dateInput.min = availableDates[0];
                dateInput.max = availableDates[availableDates.length - 1];
            }
            // Start by loading latest_ranks.json (already has trend data baked in)
            return loadLatestData();
        })
        .catch(() => {
            // Fallback: no dates.json available, just load latest
            console.warn(`dates_${prefix}.json not found, falling back to latest only`);
            // 确保 availableDates 是空数组，而不是 undefined
            if (!Array.isArray(availableDates)) {
                availableDates = [];
            }
            loadLatestData();
        });

    function loadLatestData() {
        const myReqId = ++loadRequestId;
        return fetch(`data/latest_${prefix}_ranks.json?${cacheBuster}`)
            .then(r => {
                if (!r.ok) throw new Error('Network error');
                return r.json();
            })
            .then(data => {
                if (myReqId !== loadRequestId) return; // E1: 过期请求丢弃
                if (!data || !Array.isArray(data.categories)) throw new Error('Invalid ranking data');
                allData = data;
                // Set current index from dates list
                const latestDate = data.date;
                availableDates = normalizeSnapshotDates(availableDates, latestDate);
                currentDateIndex = availableDates.indexOf(latestDate);
                dateInput.min = availableDates[0] || '';
                dateInput.max = availableDates[availableDates.length - 1] || '';
                applyData(data);
            })
            .catch(err => {
                if (myReqId !== loadRequestId) return; // E1: 过期请求丢弃
                console.error(err);
                if (restoreLoadedData('最新数据暂时无法加载')) return;
                updateDate.textContent = '数据加载失败';
                dateDisplay.textContent = '暂无数据';
                categoryList.innerHTML = '<li class="loading-item">暂无可用分类</li>';
                datePrevBtn.disabled = true;
                dateNextBtn.disabled = true;
                trendStats.innerHTML = '';
                aiContent.textContent = '';
                waterfall.innerHTML = `
                    <div class="loading-state">
                        <p class="error-hint">数据加载失败</p>
                        <button class="retry-btn" type="button">点击重试</button>
                    </div>`;
                // N103-P2: 改用 addEventListener 挂载 click，消除 inline handler，兼容 CSP（script-src 'self' 无 unsafe-inline）
                const retryBtn = waterfall.querySelector('.retry-btn');
                if (retryBtn) {
                    retryBtn.addEventListener('click', () => location.reload());
                }
            });
    }

    function loadDateData(dateStr) {
        // dateStr = "YYYY-MM-DD", file = {prefix}_ranks_YYYYMMDD.json
        // N65: 拼接 fetch 路径前做严格格式校验——dates.json 若被异常数据污染
        // （含 ../ 等片段），不会拼接出指向 data/ 之外的请求路径
        if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr || '')) {
            console.warn('非法历史日期，忽略加载:', dateStr);
            return;
        }
        const fileDateStr = dateStr.replace(/-/g, '');
        const isLatest = currentDateIndex === availableDates.length - 1;

        if (isLatest) {
            // Just load the pre-built latest with trends
            loadLatestData();
            return;
        }

        // Show loading state
        waterfall.innerHTML = '<p style="color:var(--text-muted);padding:20px;">加载中...</p>';

        const myReqId = ++loadRequestId; // E1: 数据加载请求序号
        const snapshotUrl = `data/${prefix}_ranks_${fileDateStr}.json?${cacheBuster}`;
        const trendUrl = `data/trends/${prefix}_${dateStr}.json?${cacheBuster}`;

        // Load snapshot + trends in parallel
        Promise.all([
            fetch(snapshotUrl).then(r => r.ok ? r.json() : Promise.reject('No snapshot')),
            fetch(trendUrl).then(r => r.ok ? r.json() : null).catch(() => null)
        ]).then(([snapshot, trendData]) => {
            if (myReqId !== loadRequestId) return; // E1: 过期请求丢弃
            if (!snapshot || !Array.isArray(snapshot.categories)) throw new Error('Invalid historical data');
            // Build a data object in the same shape as latest_ranks.json
            const combined = {
                date: snapshot.date,
                platform: snapshot.platform,
                quality: snapshot.quality,
                collected_at: snapshot.collected_at,
                prev_date: (trendData && trendData.prev_date) || (currentDateIndex > 0 ? availableDates[currentDateIndex - 1] : ''),
                categories: snapshot.categories.map(cat => ({
                    name: cat.name,
                    trend: trendData && trendData.trends ? (trendData.trends[cat.name] || {}) : {},
                    books: cat.books || []
                }))
            };
            allData = combined;
            applyData(combined);
        }).catch(err => {
            if (myReqId !== loadRequestId) return; // E1: 过期请求丢弃
            console.error('Failed to load historical data:', err);
            const failedDate = availableDates[currentDateIndex] || dateStr;
            if (!restoreLoadedData(`${failedDate} 数据不可用`)) {
                waterfall.innerHTML = `<div class="empty-state">
                    <p>📭 该日期（${escapeHtml(failedDate)}）暂无数据</p>
                    <p class="empty-hint">可尝试切换到其他日期查看</p>
                </div>`;
                updateDateNav();
            }
        });
    }

    function restoreLoadedData(message) {
        if (!allData) return false;
        currentDateIndex = availableDates.indexOf(allData.date);
        applyData(allData);
        showToast(`${message}，继续显示 ${allData.date}`);
        return true;
    }

    function findNearestAvailableDate(targetDate) {
        // Try nearby dates, preferring the latest
        if (availableDates.length === 0) return null;
        return availableDates.reduce((prev, curr) =>
            Math.abs(new Date(curr) - new Date(targetDate)) < Math.abs(new Date(prev) - new Date(targetDate)) ? curr : prev
        );
    }

    function applyData(data) {
        const prevInfo = data.prev_date ? ` (对比 ${data.prev_date})` : '';
        updateDate.textContent = `${data.date}${prevInfo}`;
        updateDateNav();

        document.getElementById('quality-warning')?.remove();
        const qualityMessage = dataQualityMessage(data);
        if (qualityMessage) {
            const warning = document.createElement('div');
            warning.id = 'quality-warning';
            warning.className = 'sparse-warning';
            warning.textContent = qualityMessage;
            const content = document.querySelector('.main-content');
            if (content) content.insertBefore(warning, content.children[1]);
        }

        // 数据稀疏警告
        const sparseWarning = document.getElementById('sparse-warning');
        if (sparseWarning) sparseWarning.remove();
        if (availableDates.length > 0 && availableDates.length < 3) {
            const warn = document.createElement('div');
            warn.id = 'sparse-warning';
            warn.className = 'sparse-warning';
            warn.innerHTML = `<span class="warn-icon">⚠</span> 当前榜单仅有 ${availableDates.length} 天数据，趋势分析可能不够准确。数据积累中，请持续运行爬虫以获取更完整的分析。`;
            const mainContent = document.querySelector('.main-content');
            if (mainContent) mainContent.insertBefore(warn, mainContent.children[1]);
        }

        // D2: 跨期数据缺口警告（gap_warning 非空时显示）
        const gapWarning = document.getElementById('gap-warning');
        if (gapWarning) gapWarning.remove();
        if (data.gap_warning && typeof data.gap_warning === 'string' && data.gap_warning.trim() !== '') {
            const gwarn = document.createElement('div');
            gwarn.id = 'gap-warning';
            gwarn.className = 'sparse-warning';
            const days = (typeof data.gap_days === 'number') ? data.gap_days : '?';
            gwarn.innerHTML = `<span class="warn-icon">⚠</span> ${escapeHtml(data.gap_warning)}`;
            const mainContent = document.querySelector('.main-content');
            if (mainContent) mainContent.insertBefore(gwarn, mainContent.children[1]);
        }

        // P3-10(0902): 数据过期标识——最新视图下 latest 的 date 距今 >=2 天时显示醒目横幅。
        // 场景：当日/昨日抓取失败或未跑，build_latest 静默回退旧快照构建，前端此前无任何提示，
        // 用户把旧数据当当日数据看（0908 起点风控日 latest 停 0907 即踩中）。
        // 仅最新视图提示（isLatestView）；历史日期查看是用户主动行为，不提示。
        const staleWarning = document.getElementById('stale-warning');
        if (staleWarning) staleWarning.remove();
        const isLatestView = availableDates.length > 0
            && currentDateIndex === availableDates.length - 1;
        if (document.body.dataset?.demo !== 'true' && isLatestView && data.date && /^\d{4}-\d{2}-\d{2}$/.test(data.date)) {
            const ageDays = Math.floor(
                (Date.now() - new Date(`${data.date}T00:00:00`).getTime()) / 86400000
            );
            if (ageDays >= 2) {
                const swarn = document.createElement('div');
                swarn.id = 'stale-warning';
                swarn.className = 'sparse-warning';
                swarn.innerHTML = `<span class="warn-icon">⚠</span> 数据已 ${ageDays} 天未更新（最新为 ${escapeHtml(data.date)}），当日抓取可能失败或未运行，请检查爬虫与构建日志。`;
                const mainContent = document.querySelector('.main-content');
                if (mainContent) mainContent.insertBefore(swarn, mainContent.children[1]);
            }
        }

        // 暴露数据给 export.js 使用
        window.authorData = Object.assign(window.authorData || {}, { latestRanks: data });

        // Remember current category before re-rendering
        const savedCategory = getCategoryFromURL() || currentCategory;
        renderCategories();

        // Try to restore previously selected category, otherwise pick first
        const categoryExists = savedCategory && data.categories.some(c => c.name === savedCategory);
        if (categoryExists) {
            selectCategory(savedCategory);
            // Also update sidebar active state
            document.querySelectorAll('#category-list li').forEach(el => {
                el.classList.toggle('active', el.dataset.category === savedCategory);
            });
        } else if (data.categories.length > 0) {
            selectCategory(data.categories[0].name);
            document.querySelectorAll('#category-list li').forEach((el, i) => {
                el.classList.toggle('active', i === 0);
            });
        } else {
            // #13-2(0902): categories 为空数组时上面两个分支都不走，骨架屏永久残留——
            // 清掉骨架给出空态提示（复用 loadDateData 的 empty-state 样式）
            trendStats.innerHTML = '';
            aiContent.textContent = '';
            currentCategory = null;
            categoryTitle.textContent = '暂无分类';
            waterfall.innerHTML = `<div class="empty-state">
                <p>📭 该榜单暂无分类数据（当日抓取可能失败或未运行）</p>
                <p class="empty-hint">请检查爬虫日志或切换其他榜单/日期查看</p>
            </div>`;
        }
    }

    // ========== Render sidebar categories ==========
    function renderCategories() {
        categoryList.innerHTML = '';
        allData.categories.forEach((cat, i) => {
            const li = document.createElement('li');
            li.dataset.category = cat.name;
            li.setAttribute('role', 'button');
            li.setAttribute('tabindex', '0');

            const nameSpan = document.createElement('span');
            nameSpan.textContent = cat.name;
            li.appendChild(nameSpan);

            // New entry badge
            const trend = cat.trend || {};
            if (trend.new_count > 0) {
                const badge = document.createElement('span');
                badge.className = 'cat-badge new';
                badge.textContent = `+${trend.new_count}`;
                li.appendChild(badge);
            }

            // Mark active: either the saved category or first item
            if ((currentCategory && cat.name === currentCategory) || (!currentCategory && i === 0)) {
                li.classList.add('active');
            }

            const activate = () => {
                document.querySelectorAll('#category-list li').forEach(el => el.classList.remove('active'));
                li.classList.add('active');
                selectCategory(cat.name);
                // Close mobile sidebar
                setSidebarOpen(false);
            };

            li.addEventListener('click', activate);
            li.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    activate();
                }
            });

            categoryList.appendChild(li);
        });
    }

    // ========== Select a category ==========
    function selectCategory(categoryName) {
        currentCategory = categoryName; // persist selection
        categoryTitle.textContent = categoryName;

        // 更新 URL 参数
        const url = new URL(window.location.href);
        url.searchParams.set('cat', categoryName);
        history.replaceState(null, '', url);

        const cat = allData.categories.find(c => c.name === categoryName);
        if (!cat) return;
        renderTrend(cat);
        renderBooks(cat);
    }

    // 从 URL 恢复分类选择
    function getCategoryFromURL() {
        const params = new URLSearchParams(window.location.search);
        return params.get('cat') || null;
    }

    // ========== Build a url->rank lookup for previous day ==========
    function buildPrevRankMap(categoryName) {
        // We infer prev rank from trend data
        // Actually, the trend data already has this info baked in.
        // For the card badges we need to know if a book is new or changed rank.
        const cat = allData.categories.find(c => c.name === categoryName);
        if (!cat || !cat.trend) return {};

        const map = {};
        // Mark new books
        (cat.trend.new_books || []).forEach(title => {
            map[title] = 'new';
        });
        // Risers
        (cat.trend.top_risers || []).forEach(r => {
            map[r.title] = r.change;
        });
        // Fallers
        (cat.trend.top_fallers || []).forEach(f => {
            map[f.title] = f.change;
        });
        return map;
    }

    // ========== Render Trend Panel ==========
    function renderTrend(cat) {
        const trend = cat.trend || {};
        trendStats.innerHTML = '';

        // Stat chips
        const chips = [];

        if (trend.new_count > 0) {
            chips.push({ icon: 'NEW', text: `${trend.new_count} 本新上榜`, cls: 'new-entry' });
        }
        if (trend.dropped_count > 0) {
            chips.push({ icon: 'OUT', text: `${trend.dropped_count} 本掉出`, cls: 'down' });
        }
        if (trend.top_risers && trend.top_risers.length > 0) {
            trend.top_risers.forEach(r => {
                chips.push({ icon: 'UP', text: `${r.title} ${r.change}`, cls: 'up' });
            });
        }
        if (trend.reads_growth && trend.reads_growth.length > 0) {
            chips.push({ icon: 'HOT', text: `${trend.reads_growth[0].title} ${trend.reads_growth[0].growth}`, cls: 'up' });
        }

        if (chips.length === 0) {
            chips.push({ icon: 'STABLE', text: '榜单无明显变动', cls: '' });
        }

        chips.forEach(chip => {
            const el = document.createElement('span');
            el.className = `stat-chip ${chip.cls}`;
            el.textContent = `${chip.icon} ${chip.text}`;
            trendStats.appendChild(el);
        });

        // AI Summary
        const summary = trend.summary || '';
        renderAiContent(summary);
    }

    // renderMarkdown 已移至 js/utils.js

    // ========== AI 内容渲染 ==========
    function renderAiContent(text) {
        if (!text) {
            aiContent.innerHTML = '<span class="ai-loading">暂无分析数据</span>';
            return;
        }
        aiContent.innerHTML = renderMarkdown(text);
    }

    // escapeHtml 已移至 js/utils.js

    // ========== Render Books (Waterfall) ==========
    function renderBooks(cat) {
        waterfall.innerHTML = '';

        const books = getFilteredBooks(cat);

        if (books.length === 0) {
            const query = searchInput ? searchInput.value.trim() : '';
            const filter = filterSelect ? filterSelect.value : 'all';
            if (query) {
                waterfall.innerHTML = `<p style="color:var(--text-muted);padding:20px;">未找到匹配「${escapeHtml(query)}」的书籍。</p>`;
            } else if (filter !== 'all') {
                waterfall.innerHTML = '<p style="color:var(--text-muted);padding:20px;">当前筛选条件下暂无书籍。</p>';
            } else {
                waterfall.innerHTML = '<p style="color:var(--text-muted);padding:20px;">该分类暂无书籍。</p>';
            }
            return;
        }

        const fragment = document.createDocumentFragment();

        books.forEach((book) => {
            const card = document.createElement('a');
            // N1: 协议白名单校验，仅允许绝对 http(s) 或站内单斜杠相对路径（F2-4: 排除协议相对 //evil.com），防止 javascript: 等 XSS
            const safeUrl = (book.url && book.url !== '#' && /^(https?:\/\/|\/[^/])/i.test(book.url)) ? book.url : 'javascript:void(0)';
            card.href = safeUrl;
            if (safeUrl !== 'javascript:void(0)') card.target = '_blank';
            // N14: 加 noreferrer——点击外链不再向目标站点泄漏本地预览 referrer
            card.rel = 'noopener noreferrer';
            card.className = 'book-card';

            // Rank badge class
            let rankCls = '';
            if (book.rank === 1) rankCls = 'rank-1';
            else if (book.rank === 2) rankCls = 'rank-2';
            else if (book.rank === 3) rankCls = 'rank-3';

            // Change indicator（N4 扩展：escapeHtml 防 XSS）
            let changeHtml = '';
            if (book.change === 'new') {
                changeHtml = '<span class="book-change new">NEW</span>';
            } else if (book.change && book.change.startsWith('+')) {
                changeHtml = `<span class="book-change up">↑${escapeHtml(book.change)}</span>`;
            } else if (book.change && book.change.startsWith('-')) {
                changeHtml = `<span class="book-change down">↓${escapeHtml(String(book.change).replace('-', ''))}</span>`;
            }

            // Cover：与 book.url 一致的 http(s) 白名单；referrerpolicy 缓解晋江防盗链
            const safeCover = (book.cover && /^https?:\/\//i.test(book.cover)) ? book.cover : '';
            const coverHtml = safeCover
                ? `<div class="book-cover"><img src="${escapeAttr(safeCover)}" alt="${escapeAttr(book.title)}" loading="lazy" referrerpolicy="no-referrer"></div>`
                : `<div class="book-cover"><div class="no-cover">暂无封面</div></div>`;

            card.innerHTML = `
                <span class="book-rank ${rankCls}">${escapeHtml(book.rank)}</span>
                ${changeHtml}
                ${coverHtml}
                <div class="book-info">
                    <h3 class="book-title" title="${escapeAttr(book.title)}">${escapeHtml(book.title)}</h3>
                    <div class="book-meta">
                        <span class="book-author">${escapeHtml(book.author)}</span>
                        <span class="book-reads">${readsLabel(book)}：${escapeHtml(book.reads || '待补全')}</span>
                    </div>
                    ${metricObservation(book) ? `<div class="book-observed">${escapeHtml(metricObservation(book))}</div>` : ''}
                    <p class="book-intro">${escapeHtml(book.intro).replace(/\r?\n/g, '<br>')}</p>
                    <button class="book-copy-btn" type="button">复制信息</button>
                </div>
            `;

            // Bind copy button
            const copyBtn = card.querySelector('.book-copy-btn');
            copyBtn.addEventListener('click', (e) => copyBookInfo(e, book));

            // N94: 封面 403/签名过期回退占位（事件监听而非内联 onerror，兼容未来 CSP）
            const coverImg = card.querySelector('.book-cover img');
            if (coverImg) {
                coverImg.addEventListener('error', () => {
                    const placeholder = document.createElement('div');
                    placeholder.className = 'no-cover';
                    placeholder.textContent = '暂无封面';
                    if (coverImg.replaceWith) {
                        coverImg.replaceWith(placeholder);
                    } else {
                        // 旧浏览器兜底
                        if (coverImg.parentNode) {
                            coverImg.parentNode.replaceChild(placeholder, coverImg);
                        }
                    }
                });
            }

            fragment.appendChild(card);
        });

        waterfall.appendChild(fragment);
    }

    // escapeAttr 已移至 js/utils.js
});
