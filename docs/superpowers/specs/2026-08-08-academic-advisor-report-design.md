# AI 学术导师前沿分析与科研思路生成系统 - 设计文档

**文档版本**: 1.1.0  
**日期**: 2026-08-08  
**状态**: Design Refined & Approved for Implementation  
**目标路径**: `docs/superpowers/specs/2026-08-08-academic-advisor-report-design.md`

---

## 1. 背景与核心目标 (Background & Goals)

### 1.1 背景
当前系统每天自动爬取 arXiv 与 OpenAlex 顶会/顶刊论文，并对单篇论文进行基础信息提取。然而，科研人员在日常科研中面临从“海量单篇阅读”到“把握宏观前沿趋势、寻找创新 Idea、设计可验证实验”的巨大跨度。

### 1.2 核心目标 (Core Goals)
1. **学术导师角色定位**：将 AI 设定为遥感智能解译（Remote Sensing Image Interpretation）与计算机视觉领域的资深学术导师、博导与顶会审稿人（CVPR/ICCV/ECCV/TGRS）。
2. **纯粹基于原始论文数据**：不依赖词频统计或聚类等后分析，仅通过当天爬取的原始论文元数据（Title、Abstract、Authors、Categories）进行直接理解与前沿推演。
3. **多阶段对话解耦架构 (Multi-stage Dialogue & Attention Management)**：
   - **痛点**：单次提示词同时要求分析 50~80 篇论文的宏观趋势、7天/30天时序对比、以及构思 3 篇具有严密实验设计和审稿防守的科研选题，极易导致**上下文窗口注意力分散**、**推理深度不足**以及**输出 Token 溢出截断**。
   - **方案**：将生成任务解耦拆分为 **Stage 1 (宏观趋势与时序演进研判)** 和 **Stage 2 (3大落地科研选题与实验设计工坊)** 两次聚焦对话，保证每个阶段的注意力最强、输出最详尽。
4. **历史未处理数据自动回溯补全机制 (Historical Backlog Sequential Processing)**：
   - 当系统首次启用或存在中断遗留的历史论文数据（如 `data/` 下存在历史 `.jsonl` 但未生成导师研报）时，系统能够自动发现未处理的历史日期，并**按时间先后顺序（从最早到最新）递进补全生成**，确保 7天与30天时序演进上下文的连续性与完整性。
5. **自动化流水线无缝集成**：在每日爬取去重完成后，自动触发导师研报生成与历史补漏，实现无需人工干预的“爬取即分析”。
6. **3篇梯队化落地科研思路与实验设计**：输出高创新性、高可行性的具体科研方案，包含研究痛点、核心方法、数据集/Baseline 对齐、实验验证与审稿人质疑防守。
7. **极简聚焦的前端呈现 (`advisor.html`)**：界面设计干净专注，无多余冗杂图表，聚焦研报阅读、时序对比与实验方案一键复用。

---

## 2. 总体架构设计 (System Architecture)

```
                 ┌──────────────────────────────────────────────┐
                 │             每日定时任务 (cron / run.sh)       │
                 └──────────────────────┬───────────────────────┘
                                        │ 1. 爬取 arXiv & OpenAlex
                                        ▼
                      ┌───────────────────────────────────┐
                      │ 原始论文数据: data/{today}.jsonl   │
                      └─────────────────┬─────────────────┘
                                        │ 2. 自动触发
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                 AI 导师引擎 (ai/advisor.py - 多阶段解耦架构)                │
│                                                                             │
│  [历史回溯检查] 扫描 data/*.jsonl，发现未生成研报的历史日期，按时间递进处理  │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ 🔹 对话一 (Stage 1): 宏观趋势研判与时序蒸馏                            │  │
│  │   • 输入: 今日原始论文集 + 7天/30天历史研报摘要上下文                 │  │
│  │   • 任务: 研判今日技术演进、2-3篇重点论文深度点评、跨领域交叉启发、    │  │
│  │           7天/30天时序趋势演变与审稿偏好、提取150字精炼沉淀            │  │
│  │   • 输出: Part 1 & Part 2 Markdown + summary_takeaway                 │  │
│  └───────────────────────────────────┬───────────────────────────────────┘  │
│                                      │ (传递趋势研判与重点论文)             │
│                                      ▼                                      │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ 🔹 对话二 (Stage 2): 3大落地科研选题与实验设计工坊                     │  │
│  │   • 输入: Stage 1 研判成果 + 今日代表性前沿论文 + 导师科研主题         │  │
│  │   • 任务: 构思 3 个梯队选题（顶会理论/落地痛点/多模态大模型），        │  │
│  │           输出选题、痛点、方法设计、数据集/Baseline、消融、审稿防守    │  │
│  │   • 输出: Part 3 结构化科研思路 Markdown + ideas_json                 │  │
│  └───────────────────────────────────┬───────────────────────────────────┘  │
│                                      │                                      │
│                                      ▼ 结果拼接与结构化落库                 │
└──────────────────────────────────────┼──────────────────────────────────────┘
                                       │
                                       ▼ 3. 持久化
                          ┌────────────────────────┐
                          │ SQLite: advisor_reports│
                          └────────────┬───────────┘
                                       │
                                       ▼ 4. API 响应 / 页面渲染
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FastAPI 接口层 & 极简页面                            │
│                                                                             │
│  • API 接口:                                                                │
│    - GET /api/advisor/dates                                                 │
│    - GET /api/advisor/report?date=YYYY-MM-DD                                │
│    - POST /api/advisor/generate                                             │
│    - POST /api/advisor/backfill                                             │
│    - GET/POST /api/advisor/settings                                         │
│                                                                             │
│  • 极简前端: advisor.html                                                   │
│    - 🌟 今日前沿速递与导师研判 (Card 1)                                      │
│    - 📈 时序演进对比（7天/30天趋势）(Card 2)                                 │
│    - 💡 3篇落地科研选题与实验设计（支持一键复制实验方案）(Card 3)            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 详细设计与核心模块

### 3.1 多阶段对话解耦设计 (Multi-Stage Dialogue Workflow)

#### 对话一：宏观趋势研判与时序蒸馏 (Stage 1: Macro Trend & Temporal Analysis)
* **目标**：专注对今日所有原始论文和过去 7天/30天的时序演进做宏观学术研判，提炼趋势与重点论文。
* **Prompt 设计**：
  - **System Prompt**: 
    > 你是遥感图像智能解译与计算机视觉领域的资深博导与顶会资深审稿人。请根据输入的今日原始论文集与历史时序脉络，产出宏观前沿研判。
  - **Input**:
    - 科研关注主题 `{topic}`
    - 目标日期 `{date}`
    - 今日原始论文紧凑集（Title, Categories, Abstract 核心）
    - 过去 7 天历史研报精炼总结
    - 过去 30 天宏观趋势脉络
  - **Output 格式**:
    ```markdown
    # 今日遥感智能解译前沿与学术导师研判 ({date})

    ## 1. 今日前沿速递与导师研判
    - **核心技术演进**：...
    - **重点论文深度点评**：挑选 2-3 篇最值得关注的论文点评（分析切入点、创新机制与领域启示）
    - **跨领域交叉启发**：通用 CV/NLP/大模型领域的哪些新范式可迁移至遥感任务中

    ## 2. 时序演进对比（7天/30天趋势）
    - **7天技术演变观察**：升温方向 vs 套路化/红海方向
    - **30天宏观脉络与顶会审稿偏好**：录用潜力的创新范式与审稿人最反感的缺陷

    ## 核心精炼摘要
    [此处给出 150-200 字的精炼摘要，用于后续系统沉淀]
    ```

#### 对话二：3大落地科研选题与实验设计工坊 (Stage 2: Research Ideation & Experiment Design)
* **目标**：专注科研思路的深度推演、方法架构设计、严密的实验方案及审稿人防守策略，避免与宏观分析争夺注意力与输出 Token。
* **Prompt 设计**：
  - **System Prompt**:
    > 你是遥感图像智能解译与计算机视觉领域的资深学术导师与顶会资深审稿人。请结合今日前沿研判成果与重点突破方向，为课题组研究生构思 3 篇高质量、高可行性、论证严密的科研选题与完整实验设计。
  - **Input**:
    - 科研关注主题 `{topic}`
    - 目标日期 `{date}`
    - Stage 1 产出的前沿研判与趋势成果
    - 今日最具创新代表性的 5-10 篇核心论文摘要
  - **Output 格式**:
    ```markdown
    ## 3. 3篇落地科研思路与实验设计

    ### 思路1【顶会理论/架构创新型】
    - **【选题名称】**: 中英文题目
    - **【研究痛点与动机】**: 现有方案瓶颈与核心洞察
    - **【核心方法设计】**: 网络架构设计构想、关键模块、核心机制/公式设计
    - **【推荐公开数据集与Baseline】**: 明确评测数据集与典型对比 Baseline 方法
    - **【实验验证与消融方案】**: 核心对比实验指标、关键消融实验设定
    - **【审稿人潜在质疑点与防守策略】**: 预判审稿人可能指出的软肋及防守方案

    ### 思路2【高价值痛点/任务落地型】
    - **【选题名称】**: 中英文题目
    - **【研究痛点与动机】**: 复杂场景下的具体应用瓶颈
    - **【核心方法设计】**: 针对性解耦、轻量化、弱监督或先验引导方案
    - **【推荐公开数据集与Baseline】**: 评测数据集与强基线
    - **【实验验证与消融方案】**: 验证方案与关键消融
    - **【审稿人潜在质疑点与防守策略】**: 潜在质疑与应对方案

    ### 思路3【多模态/大模型跨界融合型】
    - **【选题名称】**: 中英文题目
    - **【研究痛点与动机】**: 遥感多模态大模型、视觉-语言对齐或图文交互难点
    - **【核心方法设计】**: 适配遥感特性的跨模态融合机制或指令微调框架
    - **【推荐公开数据集与Baseline】**: 多模态基准数据集与主流多模态基线
    - **【实验验证与消融方案】**: 零样本/少样本泛化与消融实验
    - **【审稿人潜在质疑点与防守策略】**: 针对泛化性/计算代价的质疑与防守
    ```

#### 结果聚合与容错机制 (Result Merging & Fault Tolerance)
1. **聚合拼装**：将 Stage 1 的 Part 1、Part 2 与 Stage 2 的 Part 3 拼接为完整的 `report_markdown`，同时抽取结构化 `ideas_json` 与 `summary_takeaway`。
2. **独立重试**：若 Stage 2 失败，可重试 Stage 2，而无需重复执行 Stage 1，节省 Token 消耗与运行时间。

---

### 3.2 历史未处理数据自动回溯补全机制 (Historical Backlog Processing)

#### 3.2.1 遗留历史数据的检测算法
1. 扫描 `data/` 目录下的所有 `.jsonl` 文件。
2. 提取文件名中的有效日期集合 $D_{files} = \{ \text{YYYY-MM-DD} \}$。
3. 从 SQLite 的 `advisor_reports` 表中查询已生成研报的日期集合 $D_{reports} = \{ \text{report\_date} \}$。
4. 计算缺失日期集合：$D_{missing} = D_{files} \setminus D_{reports}$。
5. 对 $D_{missing}$ 按照**时间正序（升序，从最久远到最新）**排序：$D_{sorted} = [d_1, d_2, ..., d_k]$。

#### 3.2.2 时序递进补全机制 (Progressive Chronological Backfill)
* **执行逻辑**：
  ```python
  for missing_date in sorted(missing_dates):
      print(f"正在时序回溯生成 {missing_date} 的导师研报...")
      generate_advisor_report(date_str=missing_date, force=False, db_path=db_path)
  ```
* **时序链条构建**：由于按照时间先后顺序生成，$d_1$ 生成后立即落库 $d_1$ 的摘要；当处理 $d_2$ 时，$d_2$ 就能读取到 $d_1$ 的研报摘要作为历史演进上下文。这样整个历史的时序链条得以自然完整地闭环建立。

#### 3.2.3 触发通道 (Trigger Channels)
1. **CLI 命令行**：
   - `python ai/advisor.py --backfill`：扫描并按顺序补全所有缺失日期的研报。
   - `python ai/advisor.py --date YYYY-MM-DD --backfill`：先按时序补全目标日期之前的所有缺失研报，再生成目标日期的研报。
2. **自动化流水线 (`run.sh`)**：
   - 在步骤4中执行 `python ai/advisor.py --date ${today} --backfill`，确保如果之前某些天离线或爬取未运行，自动按序把断档的研报全部补齐。
3. **后台 API 与定时扫描**：
   - 提供 `POST /api/advisor/backfill` 接口供按需触发。
   - `server_modules/processor.py` 在 `scan_and_process_files()` 完成论文入库后，可检查缺失的研报日期并异步触发补全。

---

### 3.3 数据库设计 (Database Schema)

在 `data/statistics.db` 中建立 `advisor_reports` 与 `advisor_settings` 表：

```sql
CREATE TABLE IF NOT EXISTS advisor_reports (
    report_date TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    summary_takeaway TEXT,          -- 简短概要（供未来时序对比使用，约150字）
    report_markdown TEXT NOT NULL,  -- 完整研报 Markdown
    ideas_json TEXT,                -- 3个科研思路的结构化 JSON
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS advisor_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_advisor_reports_date ON advisor_reports (report_date);
```

---

### 3.4 后端 API 接口设计 (`server_modules/advisor.py`)

1. **`GET /api/advisor/dates`**
   * 返回：`{"dates": ["2026-08-08", "2026-08-07", ...]}`（已生成的研报日期列表，按日期倒序）。
2. **`GET /api/advisor/report?date=YYYY-MM-DD`**
   * 返回：指定日期的完整研报详情及 3 个科研思路的结构化数据。
3. **`POST /api/advisor/generate`**
   * 请求体：`{"date": "YYYY-MM-DD", "topic": "可选自定义主题", "force": false}`
   * 功能：调用 2 阶段 LLM 生成目标日期研报并落库。
4. **`POST /api/advisor/backfill`**
   * 请求体：`{"force": false}`
   * 功能：自动检测所有未生成的历史日期并按时序顺序批量补全。
5. **`GET /api/advisor/settings` 与 `POST /api/advisor/settings`**
   * 功能：读取/修改导师科研关注主题（默认：“遥感图像的处理与信息提取（目标检测、语义分割等）”）。

---

### 3.5 自动化流水线集成 (`run.sh`)

在 `run.sh` 增加 **步骤4：AI 学术导师前沿研报生成与历史补漏**：

```bash
# 第四步：AI 学术导师前沿研报生成与历史补漏 / Step 4: Academic Advisor Analysis & Backlog Processing
if [ "$PARTIAL_MODE" = "false" ]; then
    echo "步骤4：生成学术导师前沿分析与科研思路研报... / Step 4: Generating Academic Advisor Report..."
    python ai/advisor.py --date "${today}" --backfill
    
    if [ $? -ne 0 ]; then
        echo "⚠️ 学术导师研报生成跳过或遇到警告，不影响主爬取数据"
    else
        echo "✅ 学术导师研报生成完成 / Academic Advisor report generated successfully"
    fi
else
    echo "⏭️  跳过学术导师研报生成（部分模式）/ Skipping Academic Advisor report (partial mode)"
fi
```

---

### 3.6 前端极简页面设计 (`advisor.html`, `css/advisor.css`, `js/advisor.js`)

#### 页面结构与交互：
* **顶部控制栏**：
  * 返回首页按钮
  * 日期选择器（Flatpickr 快捷选择已生成的研报日期，标注哪些日期已有研报）
  * 导师主题标签徽章（点击可弹出微调弹窗）
  * “补全历史研报” / “重新生成” 按钮
* **三大核心卡片 (Cards Layout)**：
  1. **Card 1: 🌟 今日前沿速递与导师研判**
     - Markdown 渲染、重点论文高亮点评、跨领域交叉启发。
  2. **Card 2: 📈 时序演进对比（7天/30天趋势）**
     - 升温方向与红海方向对照、顶会审稿偏好指导。
  3. **Card 3: 💡 3大落地科研思路工坊**
     - 顶部 3 个 Tab 切换：
       - `💡 思路1：顶会理论/架构创新型`
       - `🎯 思路2：高价值痛点/任务落地型`
       - `🧩 思路3：多模态/大模型跨界融合型`
     - 每个思路展示完整的动机、方法设计、数据集与 Baseline、消融方案、审稿人质疑防守。
     - 配备独立的 **“📋 复制本篇实验设计”** 按钮（点击后自动将整篇思路与实验方案复制至剪贴板，并显示绿色反馈 Toast）。

---

## 4. 验证与测试方案 (Verification Plan)

1. **多阶段对话单元测试**：
   - 验证 Stage 1（趋势与时序）能独立运行并提炼 `summary_takeaway`。
   - 验证 Stage 2（科研选题）能基于 Stage 1 结果生成符合规范的 3 个结构化思路。
   - 验证整体拼接合并逻辑。
2. **历史补漏回溯测试**：
   - 模拟存在多个历史 `.jsonl` 文件但数据库为空的场景，验证 `backfill` 能按时间正序依次处理并构建时序上下文。
3. **API 与权限测试**：
   - 验证所有 `/api/advisor/*` 路由具备 Bearer Token 鉴权保护。
   - 验证 `/api/advisor/backfill` 与 `/api/advisor/generate` 正常工作。
4. **前端端到端验证**：
   - 访问 `advisor.html`，切换日期、切换科研思路 Tab、点击复制实验方案并检查交互流畅性。
