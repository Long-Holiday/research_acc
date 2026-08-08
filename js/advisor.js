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

        // Tab switcher
        document.querySelectorAll('.idea-tab-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const tabIndex = parseInt(e.currentTarget.getAttribute('data-tab'));
                this.switchIdeaTab(tabIndex);
            });
        });
    },

    async loadSettings() {
        try:
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

        this.flatpickrInstance = flatpickr('#calendarButton', {
            defaultDate: this.currentDate,
            enable: this.availableDates.length > 0 ? this.availableDates : [this.currentDate],
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

        if (fullMd.includes('## 2. 时序演进对比')) {
            const parts = fullMd.split('## 2. 时序演进对比');
            part1 = parts[0].trim();
            part2 = '## 2. 时序演进对比\n\n' + (parts[1] ? parts[1].split('## 3. 3篇落地')[0].trim() : '');
        } else {
            part1 = fullMd;
            part2 = '暂无时序演进信息';
        }

        document.getElementById('part1Content').innerHTML = typeof marked !== 'undefined' ? marked.parse(part1) : part1;
        document.getElementById('part2Content').innerHTML = typeof marked !== 'undefined' ? marked.parse(part2) : part2;

        this.renderIdeas(report.ideas_json || []);
    },

    renderIdeas(ideas) {
        for (let i = 0; i < 3; i++) {
            const idea = ideas[i] || {};
            const titleEl = document.getElementById(`ideaTitle${i}`);
            const gridEl = document.getElementById(`ideaGrid${i}`);

            if (titleEl) {
                titleEl.textContent = idea.title || `思路 ${i + 1}`;
            }

            if (gridEl) {
                gridEl.innerHTML = `
                    <div class="idea-detail-box full-width">
                        <div class="detail-label">🎯 研究痛点与动机</div>
                        <div class="detail-value">${idea.motivation || '暂无说明'}</div>
                    </div>
                    <div class="idea-detail-box full-width">
                        <div class="detail-label">⚙️ 核心方法设计</div>
                        <div class="detail-value">${idea.method || '暂无说明'}</div>
                    </div>
                    <div class="idea-detail-box">
                        <div class="detail-label">📊 推荐公开数据集与 Baseline</div>
                        <div class="detail-value">${idea.datasets || '暂无说明'}</div>
                    </div>
                    <div class="idea-detail-box">
                        <div class="detail-label">🧪 实验验证与消融方案</div>
                        <div class="detail-value">${idea.experiments || '暂无说明'}</div>
                    </div>
                    <div class="idea-detail-box full-width">
                        <div class="detail-label">🛡️ 审稿人潜在质疑点与防守策略</div>
                        <div class="detail-value">${idea.defense || '暂无说明'}</div>
                    </div>
                `;
            }
        }

        this.switchIdeaTab(0);
    },

    switchIdeaTab(tabIndex) {
        document.querySelectorAll('.idea-tab-btn').forEach((btn, idx) => {
            btn.classList.toggle('active', idx === tabIndex);
        });

        document.querySelectorAll('.idea-pane').forEach((pane, idx) => {
            pane.classList.toggle('active', idx === tabIndex);
        });
    },

    copyIdeaExperiment(index) {
        if (!this.currentReport || !this.currentReport.ideas_json || !this.currentReport.ideas_json[index]) {
            this.showToast('无法复制：无有效的思路数据');
            return;
        }

        const idea = this.currentReport.ideas_json[index];
        const copyContent = `### 【${idea.type || '科研思路'}】${idea.title}\n\n` +
            `**【研究痛点与动机】**:\n${idea.motivation || '无'}\n\n` +
            `**【核心方法设计】**:\n${idea.method || '无'}\n\n` +
            `**【推荐公开数据集与 Baseline】**:\n${idea.datasets || '无'}\n\n` +
            `**【实验验证与消融方案】**:\n${idea.experiments || '无'}\n\n` +
            `**【审稿人潜在质疑点与防守策略】**:\n${idea.defense || '无'}`;

        navigator.clipboard.writeText(copyContent).then(() => {
            this.showToast('📋 已复制该篇科研思路与完整实验设计！');
        }).catch(err => {
            console.error('Clipboard copy failed:', err);
            this.showToast('复制失败，请手动选择复制');
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

    async handleBackfill() {
        if (!confirm('确定开始扫描并补全所有缺失的历史研报吗？系统将在后台按时间升序挨个生成。')) {
            return;
        }

        try {
            const resp = await Auth.fetchWithAuth('/api/advisor/backfill', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ force: false })
            });

            if (resp.ok) {
                this.showToast('⏳ 历史研报补漏任务已提交后台后台处理中...');
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
