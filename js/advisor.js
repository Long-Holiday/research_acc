/**
 * AI Academic Advisor Page Logic
 */

const AdvisorApp = {
    currentDate: null,
    availableDates: [],
    currentReport: null,
    flatpickrInstance: null,

    async init() {
        this.bindEvents();
        await this.loadSettings();
        await this.loadAvailableDates();
    },

    bindEvents() {
        document.getElementById('backfillBtn').addEventListener('click', () => this.handleBackfill());
        document.getElementById('regenerateBtn').addEventListener('click', () => this.handleGenerate(true));
        document.getElementById('emptyGenerateBtn').addEventListener('click', () => this.handleGenerate(true));
        document.getElementById('editTopicBtn').addEventListener('click', () => this.openTopicModal());
        document.getElementById('closeTopicModalBtn').addEventListener('click', () => this.closeTopicModal());
        document.getElementById('cancelTopicModalBtn').addEventListener('click', () => this.closeTopicModal());
        document.getElementById('saveTopicModalBtn').addEventListener('click', () => this.saveTopicModal());

        const dateDisplay = document.querySelector('.date-display');
        if (dateDisplay) {
            dateDisplay.addEventListener('click', (e) => {
                const calBtn = document.getElementById('calendarButton');
                if (this.flatpickrInstance && calBtn && !calBtn.contains(e.target) && e.target !== calBtn) {
                    this.flatpickrInstance.open();
                }
            });
        }

    },

    async loadSettings() {
        try {
            const resp = await Auth.fetchWithAuth('/api/advisor/settings');
            if (resp.ok) {
                const data = await resp.json();
                if (data.topic) {
                    document.getElementById('topicValue').textContent = data.topic;
                }
            }
        } catch (e) {
            console.error('Failed to load settings:', e);
        }
    },

    async loadAvailableDates() {
        try {
            const resp = await Auth.fetchWithAuth('/api/advisor/dates');
            if (resp.ok) {
                const data = await resp.json();
                this.availableDates = data.dates || [];
            }
        } catch (e) {
            console.error('Failed to load dates:', e);
            this.availableDates = [];
        }

        if (this.availableDates.length > 0) {
            this.currentDate = this.availableDates[0];
        } else {
            const today = new Date();
            this.currentDate = today.toISOString().split('T')[0];
        }

        this.initDatePicker();
        await this.loadReportForDate(this.currentDate);
    },

    initDatePicker() {
        document.getElementById('currentDate').textContent = this.currentDate;

        if (this.flatpickrInstance) {
            this.flatpickrInstance.destroy();
        }

        const dateBtn = document.getElementById('calendarButton');
        if (!dateBtn) return;

        this.flatpickrInstance = flatpickr(dateBtn, {
            dateFormat: "Y-m-d",
            defaultDate: this.currentDate,
            enable: this.availableDates.length > 0 ? this.availableDates : [this.currentDate],
            disableMobile: true,
            onChange: (selectedDates, dateStr) => {
                if (dateStr) {
                    this.currentDate = dateStr;
                    document.getElementById('currentDate').textContent = dateStr;
                    this.loadReportForDate(dateStr);
                }
            }
        });
    },

    async loadReportForDate(dateStr) {
        this.showState('loading', '正在加载研报数据...');
        try {
            const resp = await Auth.fetchWithAuth(`/api/advisor/report?date=${dateStr}`);
            if (resp.status === 404) {
                this.showState('empty');
                return;
            }
            if (!resp.ok) {
                throw new Error('网络请求失败');
            }

            const report = await resp.json();
            this.currentReport = report;
            this.renderReport(report);
            this.showState('report');
        } catch (e) {
            console.error('Failed to load report:', e);
            this.showState('empty');
        }
    },

    renderReport(report) {
        const fullMd = report.report_markdown || '';
        
        let part1 = '';
        let part2 = '';
        let part3 = '';

        // Match Section 2 header: ## 2. 时序演进对比...
        const sec2Regex = /(?:^|\n)(#{1,3}\s*2[\.、\s].*时序演进.*)(?:\n|$)/i;
        // Match Section 3 header: ## 3. 3篇梯队化... or # 三篇梯队化... or # 3篇梯队化... or ### 思路1...
        const sec3Regex = /(?:^|\n)(#{1,3}\s*(?:3[\.、\s].*|[3三]篇.*|科研选题.*|.*梯队化科研选题.*)|#{1,3}\s*思路1.*)(?:\n|$)/i;

        const match2 = fullMd.match(sec2Regex);
        const match3 = fullMd.match(sec3Regex);

        const idx2 = match2 ? match2.index + (match2[0].startsWith('\n') ? 1 : 0) : -1;
        const idx3 = match3 ? match3.index + (match3[0].startsWith('\n') ? 1 : 0) : -1;

        if (idx2 !== -1 && idx3 !== -1 && idx2 < idx3) {
            part1 = fullMd.substring(0, idx2).trim();
            part2 = fullMd.substring(idx2, idx3).trim();
            part3 = fullMd.substring(idx3).trim();
        } else if (idx2 !== -1) {
            part1 = fullMd.substring(0, idx2).trim();
            part2 = fullMd.substring(idx2).trim();
            part3 = '*暂无独立的选题与实验设计方案*';
        } else if (idx3 !== -1) {
            part1 = fullMd.substring(0, idx3).trim();
            part2 = '*暂无过去7天与30天的历史研报数据对比*';
            part3 = fullMd.substring(idx3).trim();
        } else {
            part1 = fullMd;
            part2 = '*暂无过去7天与30天的历史研报数据对比*';
            part3 = '*暂无独立的选题与实验设计方案*';
        }

        // Remove redundant markdown headers that duplicate HTML card titles
        part1 = part1.replace(/^#\s+[^\n]+\n*/, '');
        part1 = part1.replace(/^##\s*1[\.、\s][^\n]+\n*/, '').trim();
        
        part2 = part2.replace(/^#{1,3}\s*2[\.、\s][^\n]+\n*/, '').trim();
        
        part3 = part3.replace(/^#{1,3}\s*(?:3[\.、\s][^\n]*|[3三]篇[^\n]*|科研选题[^\n]*|.*梯队化科研选题[^\n]*)\n*/i, '').trim();

        const el1 = document.getElementById('part1Content');
        const el2 = document.getElementById('part2Content');
        const el3 = document.getElementById('part3Content');

        if (el1) el1.innerHTML = this.renderMarkdown(part1);
        if (el2) el2.innerHTML = this.renderMarkdown(part2);
        
        if (report.ideas_json && report.ideas_json.length > 0) {
            if (el3) {
                el3.innerHTML = this.renderIdeasTabs(report.ideas_json);
                this.bindIdeaTabsAndCopy(el3, report.ideas_json);
            }
        } else {
            if (el3) el3.innerHTML = this.renderMarkdown(part3);
        }

        this.postProcessContainers([el1, el2, el3]);
    },

    renderIdeasTabs(ideas) {
        let navHtml = '<div class="idea-tabs-nav">';
        let panesHtml = '<div class="idea-panes-container">';

        ideas.forEach((idea, index) => {
            const isActive = index === 0 ? 'active' : '';
            // Short title for tab
            let shortType = "思路";
            if (idea.type.includes("理论") || idea.type.includes("架构")) shortType = "理论/架构创新";
            else if (idea.type.includes("落地") || idea.type.includes("痛点")) shortType = "高价值落地";
            else if (idea.type.includes("跨界") || idea.type.includes("模态")) shortType = "跨界融合";
            
            navHtml += `<button class="idea-tab-btn ${isActive}" data-target="idea-pane-${index}">思路 ${index + 1}: ${shortType}</button>`;

            panesHtml += `
            <div id="idea-pane-${index}" class="idea-pane ${isActive}">
                <div class="idea-action-bar">
                    <span class="idea-type-title">${idea.type || '科研选题'}</span>
                    <button class="copy-exp-btn" data-idx="${index}">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="vertical-align: middle; margin-right: 4px;">
                            <path d="M16 1H4C2.9 1 2 1.9 2 3V17H4V3H16V1ZM19 5H8C6.9 5 6 5.9 6 7V21C6 22.1 6.9 23 8 23H19C20.1 23 21 22.1 21 21V7C21 5.9 20.1 5 19 5ZM19 21H8V7H19V21Z" fill="currentColor"/>
                        </svg>复制方案
                    </button>
                </div>
                <div class="idea-details-grid">
                    <div class="idea-detail-box full-width">
                        <div class="detail-label">🎯 选题名称</div>
                        <div class="detail-value">${this.renderMarkdown(idea.title || '*暂无*')}</div>
                    </div>
                    <div class="idea-detail-box">
                        <div class="detail-label">💡 研究痛点与动机</div>
                        <div class="detail-value">${this.renderMarkdown(idea.motivation || '*暂无*')}</div>
                    </div>
                    <div class="idea-detail-box">
                        <div class="detail-label">⚙️ 核心方法设计</div>
                        <div class="detail-value">${this.renderMarkdown(idea.method || '*暂无*')}</div>
                    </div>
                    <div class="idea-detail-box">
                        <div class="detail-label">📊 推荐公开数据集与Baseline</div>
                        <div class="detail-value">${this.renderMarkdown(idea.datasets || '*暂无*')}</div>
                    </div>
                    <div class="idea-detail-box">
                        <div class="detail-label">🔬 实验验证与消融方案</div>
                        <div class="detail-value">${this.renderMarkdown(idea.experiments || '*暂无*')}</div>
                    </div>
                    <div class="idea-detail-box full-width">
                        <div class="detail-label">🛡️ 审稿人潜在质疑点与防守策略</div>
                        <div class="detail-value">${this.renderMarkdown(idea.defense || '*暂无*')}</div>
                    </div>
                </div>
            </div>`;
        });

        navHtml += '</div>';
        panesHtml += '</div>';

        return navHtml + panesHtml;
    },

    bindIdeaTabsAndCopy(container, ideas) {
        const tabs = container.querySelectorAll('.idea-tab-btn');
        const panes = container.querySelectorAll('.idea-pane');
        const copyBtns = container.querySelectorAll('.copy-exp-btn');

        tabs.forEach(tab => {
            tab.addEventListener('click', (e) => {
                const targetId = e.target.getAttribute('data-target');
                
                // Update active state for tabs
                tabs.forEach(t => t.classList.remove('active'));
                e.target.classList.add('active');
                
                // Update active state for panes
                panes.forEach(p => {
                    if (p.id === targetId) {
                        p.classList.add('active');
                    } else {
                        p.classList.remove('active');
                    }
                });
            });
        });

        copyBtns.forEach(btn => {
            btn.addEventListener('click', async (e) => {
                const idx = e.currentTarget.getAttribute('data-idx');
                const idea = ideas[idx];
                if (idea && idea.raw_text) {
                    try {
                        await navigator.clipboard.writeText(idea.raw_text);
                        const originalText = e.currentTarget.innerHTML;
                        e.currentTarget.innerHTML = '✅ 已复制';
                        setTimeout(() => {
                            e.currentTarget.innerHTML = originalText;
                        }, 2000);
                    } catch (err) {
                        console.error('Failed to copy:', err);
                    }
                }
            });
        });
    },

    renderMarkdown(text) {
        if (!text) return '';
        if (typeof marked === 'undefined') return text;

        const mathBlocks = [];
        const mathInlines = [];

        // 0. Convert ```math and ```latex into $$ ... $$
        let processed = text.replace(/```(?:math|latex)\s*\n([\s\S]*?)```/gi, (match, math) => {
            return `\n$$\n${math}\n$$\n`;
        });

        // 1. Protect code blocks to avoid messing up math-like syntax inside code
        const codeBlocks = [];
        processed = processed.replace(/(```[\s\S]*?```|`[^`\n]+`)/g, (match) => {
            const id = `CODEBLOCKPLACEHOLDER${codeBlocks.length}END`;
            codeBlocks.push(match);
            return id;
        });

        // 2. Extract block math: $$...$$ and \[...\]
        processed = processed.replace(/\$\$([\s\S]+?)\$\$/g, (match, math) => {
            const id = `MATHBLOCKPLACEHOLDER${mathBlocks.length}END`;
            mathBlocks.push(math.trim());
            return `\n\n${id}\n\n`;
        });
        processed = processed.replace(/\\\[([\s\S]+?)\\\]/g, (match, math) => {
            const id = `MATHBLOCKPLACEHOLDER${mathBlocks.length}END`;
            mathBlocks.push(math.trim());
            return `\n\n${id}\n\n`;
        });

        // 3. Extract inline math: $...$ and \(...\)
        processed = processed.replace(/(?<!\\)\$([^\$]+?)(?<!\\)\$/g, (match, math) => {
            const id = `MATHINLINEPLACEHOLDER${mathInlines.length}END`;
            mathInlines.push(math.trim());
            return id;
        });
        processed = processed.replace(/\\\(([\s\S]+?)\\\)/g, (match, math) => {
            const id = `MATHINLINEPLACEHOLDER${mathInlines.length}END`;
            mathInlines.push(math.trim());
            return id;
        });

        // 4. Restore code blocks before marked parsing
        processed = processed.replace(/CODEBLOCKPLACEHOLDER(\d+)END/g, (match, idx) => {
            return codeBlocks[parseInt(idx, 10)];
        });

        // 5. Parse Markdown with marked
        let html = marked.parse(processed);

        // 6. Replace block math placeholders with rendered KaTeX
        html = html.replace(/<p>\s*(MATHBLOCKPLACEHOLDER\d+END)\s*<\/p>/g, '$1');
        html = html.replace(/MATHBLOCKPLACEHOLDER(\d+)END/g, (match, idx) => {
            const math = mathBlocks[parseInt(idx, 10)];
            if (typeof katex !== 'undefined') {
                try {
                    const rendered = katex.renderToString(math, {
                        displayMode: true,
                        throwOnError: false
                    });
                    return `<div class="katex-block-wrapper">${rendered}</div>`;
                } catch (e) {
                    console.error('KaTeX block render error:', e);
                    return `<div class="katex-block-wrapper">$$${math}$$</div>`;
                }
            }
            return `<div class="katex-block-wrapper">$$${math}$$</div>`;
        });

        // 7. Replace inline math placeholders with rendered KaTeX
        html = html.replace(/MATHINLINEPLACEHOLDER(\d+)END/g, (match, idx) => {
            const math = mathInlines[parseInt(idx, 10)];
            if (typeof katex !== 'undefined') {
                try {
                    const rendered = katex.renderToString(math, {
                        displayMode: false,
                        throwOnError: false
                    });
                    return `<span class="katex-inline-wrapper">${rendered}</span>`;
                } catch (e) {
                    console.error('KaTeX inline render error:', e);
                    return `$${math}$`;
                }
            }
            return `$${math}$`;
        });

        return html;
    },

    postProcessContainers(elements) {
        elements.forEach(container => {
            if (!container) return;

            // Fallback KaTeX auto-render for any missed math elements
            if (typeof renderMathInElement !== 'undefined') {
                try {
                    renderMathInElement(container, {
                        delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '\\[', right: '\\]', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false}
                        ],
                        throwOnError: false
                    });
                } catch (e) {
                    console.warn('Auto-render math warning:', e);
                }
            }

            // Post-process ASCII architecture frameworks into Blueprint Cards
            const preElements = container.querySelectorAll('pre');
            preElements.forEach(pre => {
                const code = pre.querySelector('code');
                const text = (code ? code.textContent : pre.textContent) || '';

                // Detect ASCII box drawing / flowcharts
                const hasBoxDrawing = /[┌┐└┘├┤┬┴┼─│═║▶▼▲◀]/.test(text) || (text.includes('+--') && text.includes('|'));
                if (hasBoxDrawing && !pre.closest('.architecture-diagram-container')) {
                    const wrapper = document.createElement('div');
                    wrapper.className = 'architecture-diagram-container';
                    wrapper.innerHTML = `
                        <div class="diagram-toolbar">
                            <span class="diagram-badge">📐 核心网络与模块架构设计蓝图</span>
                            <button class="diagram-copy-btn" title="复制架构图代码">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                                    <path d="M16 1H4C2.9 1 2 1.9 2 3V17H4V3H16V1ZM19 5H8C6.9 5 6 5.9 6 7V21C6 22.1 6.9 23 8 23H19C20.1 23 21 22.1 21 21V7C21 5.9 20.1 5 19 5ZM19 21H8V7H19V21Z" fill="currentColor"/>
                                </svg>
                                <span>复制架构</span>
                            </button>
                        </div>
                        <div class="diagram-code-wrapper"></div>
                    `;

                    pre.parentNode.insertBefore(wrapper, pre);
                    const codeWrap = wrapper.querySelector('.diagram-code-wrapper');
                    codeWrap.appendChild(pre);

                    const copyBtn = wrapper.querySelector('.diagram-copy-btn');
                    copyBtn.addEventListener('click', async () => {
                        try {
                            await navigator.clipboard.writeText(text);
                            copyBtn.classList.add('copied');
                            copyBtn.querySelector('span').textContent = '已复制！';
                            setTimeout(() => {
                                copyBtn.classList.remove('copied');
                                copyBtn.querySelector('span').textContent = '复制架构';
                            }, 2000);
                        } catch (err) {
                            console.error('Failed to copy diagram:', err);
                        }
                    });
                }
            });
        });
    },

    async handleGenerate(force = false) {
        this.showState('loading', '正在调用 AI 学术导师引擎研判与构思（约需 15-30 秒）...');
        try {
            const resp = await Auth.fetchWithAuth('/api/advisor/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    date: this.currentDate,
                    force: force
                })
            });

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || '生成失败');
            }

            const data = await resp.json();
            this.currentReport = data.report;
            this.renderReport(data.report);
            this.showState('report');
            this.showToast('✅ 导师研报生成成功！');
            await this.loadAvailableDates();
        } catch (e) {
            console.error('Generate failed:', e);
            alert(`生成导师研报失败: ${e.message}`);
            this.showState('empty');
        }
    },

    async handleBackfill(force = false) {
        if (!force && !confirm('确定开始扫描并补全所有缺失的历史研报吗？系统将在后台按时间升序挨个生成。')) {
            return;
        }

        try {
            const resp = await Auth.fetchWithAuth('/api/advisor/backfill', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ force: force })
            });

            if (resp.ok) {
                const data = await resp.json();
                if (data.status === 'already_complete') {
                    if (confirm('所有缺失研报已补全。是否要强制重新生成所有历史研报？（此操作耗时较长）')) {
                        this.handleBackfill(true);
                    }
                } else {
                    this.showToast('⏳ 历史研报补漏任务已提交后台处理中...');
                }
            } else {
                throw new Error('提交补漏任务失败');
            }
        } catch (e) {
            console.error('Backfill error:', e);
            alert(`历史补漏失败: ${e.message}`);
        }
    },

    showState(state, loadingText = '') {
        const loadingEl = document.getElementById('loadingState');
        const emptyEl = document.getElementById('emptyState');
        const reportEl = document.getElementById('reportContainer');

        loadingEl.style.display = state === 'loading' ? 'block' : 'none';
        emptyEl.style.display = state === 'empty' ? 'block' : 'none';
        reportEl.style.display = state === 'report' ? 'grid' : 'none';

        if (loadingText) {
            document.getElementById('loadingText').textContent = loadingText;
        }
    },

    openTopicModal() {
        const currentTopic = document.getElementById('topicValue').textContent;
        document.getElementById('modalTopicInput').value = currentTopic;
        document.getElementById('topicModal').style.display = 'flex';
    },

    closeTopicModal() {
        document.getElementById('topicModal').style.display = 'none';
    },

    async saveTopicModal() {
        const newTopic = document.getElementById('modalTopicInput').value.trim();
        if (!newTopic) {
            alert('主题不能为空');
            return;
        }

        try {
            const resp = await Auth.fetchWithAuth('/api/advisor/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ topic: newTopic })
            });

            if (resp.ok) {
                const data = await resp.json();
                document.getElementById('topicValue').textContent = data.topic;
                this.closeTopicModal();
                this.showToast('🎯 导师科研关注主题已成功更新！');
            } else {
                throw new Error('保存失败');
            }
        } catch (e) {
            alert(`修改保存失败: ${e.message}`);
        }
    },

    showToast(message) {
        const container = document.getElementById('toastContainer');
        const toast = document.createElement('div');
        toast.className = 'toast';
        toast.textContent = message;
        container.appendChild(toast);

        setTimeout(() => {
            toast.remove();
        }, 3000);
    }
};

document.addEventListener('DOMContentLoaded', () => {
    AdvisorApp.init();
});
