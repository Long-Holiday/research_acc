# Daily arXiv / Research Assistant

## arXiv 爬取

每日爬虫直接读取配置分类的 `https://arxiv.org/list/{category}/new` 页面，从列表中提取标题、作者、分类、备注和摘要，只保留新投稿及交叉投稿，跳过替换更新。通过环境变量 `CATEGORIES` 指定分类（逗号分隔）。

缺少标题或摘要的条目会记录丢弃原因，不再调用 arXiv API 补全，也不填充空值后继续输出。网络请求超时为 30 秒，临时错误最多重试 1 次；保留请求间隔和并发限制。后续期刊抓取、历史去重及 AI 处理沿用原流程。

## AI 关键词与统计

AI 增强在同一次模型调用中返回 `AI.keywords` 字符串数组，提取 3–8 个英文研究主题、方法或任务术语，使用标准名称并避免重复概念。摘要缺失或增强失败时使用空数组。

入库只读取该字段，统一空白和大小写、单篇去重，每个关键词按命中论文数汇总，不再加载 spaCy 模型、分析标题摘要或计算 IDF，也不再混入 OpenAlex concepts。没有关键词的论文仍正常入库并计入论文总数。

历史文件和数据库不做回填或自动全量重建；没有 `AI.keywords` 的数据不再补提取。已有未修改文件及其统计沿用原记录。

## 低资源服务器运行方式

- Web 服务只查询已入库数据，不再在启动时或每 5 分钟扫描文件、提取关键词。正常 Web 导入不加载 spaCy 或模型客户端，保持单个 Uvicorn worker 即可。
- 每日 `run.sh` 在 AI 增强成功后执行一次 `data-process`，统一处理新增及同名修改的增强文件，再生成研报；处理失败会停止后续流程。周末补录/修复不新增调度或处理步骤，两步均使用已有的 `--skip-db-sync` 跳过即时数据库同步，修改后的增强文件留到周一完整流水线统一检测处理。
- 每天 **服务器本地时间 04:00（含）–05:00（不含）** 为固定维护窗口：网页显示“论文正在更新”，所有 `/api/` 请求直接返回 JSON `503` 与 `Retry-After`，不执行正常业务。无需 Nginx，Web 进程保持运行来提供静态维护提示。
- **05:00 自动恢复，不检查更新是否已经完成**；任务超时会在恢复后继续占用资源，固定窗口不保证任务在一小时内完成。窗口外手动运行流水线也不会自动开启维护页。
- 单日、日期范围及可用日期都以已处理数据库为准，未入库的文件不会立即出现在页面。单日和范围分页在 SQLite 中筛选、计数和 `LIMIT/OFFSET`，Python 只解析当前页；关键词/作者搜索保留大小写不敏感的字面子串匹配。不传 `page` 的旧接口仍兼容返回全量列表。
- 入库沿用同语言跨日期论文 ID 去重，所以单日数量可能少于源文件行数。同名修改会在事务内替换该日期/语言的论文、关键词及统计，处理失败保留上一版；依据文件纳秒修改时间和大小识别变更。
- 离线处理后，运行中的统计缓存最多约 300 秒过期；缓存读写时清理过期项，最多保留 128 项，并通过线程锁保护并发访问。不额外引入缓存通知系统。关键词趋势只为最终返回的前 100 项计算，排名规则不变。
- 入库在关键词分析前按批次查询历史 ID 并去重，不再为每个文件加载同语言全部历史 ID。周末补录/修复的 AI 待处理任务数受 `--max-workers` 限制；`--skip-db-sync` 时不再累计保留跨日期同步结果。
- 网页“加载更多”使用 `include_category_counts=false` 跳过重复分类汇总；第一页及默认 API 调用仍返回分类统计。显式传入该参数时，分页响应省略 `category_counts`、`categories`、`total_all`，保留论文、总数和页数。
- `run.sh`、`run_fix_abstracts.sh` 与 `run_maintenance.sh` 的入库、关键词重建、研报写入命令共用项目根目录 `.maintenance.lock`，需要系统提供 `flock`（通常来自 util-linux）。任务重叠时立即记录提示并返回 `75`，不排队、不终止已运行任务；失败后需稍后重试。锁文件保留，进程退出自动释放锁，不要手动删除运行中的锁文件。日志清理和关键词过滤不参与此锁。直接运行 Python 命令不受 Bash 入口锁保护。
- 依赖安装需显式执行 `bash startup.sh install`，不再安装 spaCy 或下载关键词模型；日常 `start`/`restart` 不安装依赖。使用 uv 启动时指定 `--no-sync`，避免启动过程中隐式同步依赖。保持单 worker 与现有科学计算库单线程限制；此处未设置系统 CPU/内存硬上限，需依据实际服务器配置另行配置。

**首次部署或依赖升级，先安装依赖；备份数据库并手动运行一次增量入库，然后重启 Web 服务：**

```bash
bash startup.sh install
bash run_maintenance.sh data-process
# 可选：指定数据目录和目标数据库
bash run_maintenance.sh data-process --data-dir /path/to/data --db-path /path/to/statistics.db
```

首次处理会为旧库补齐文件变更记录；没有 `AI.keywords` 的文件不会补提取关键词。后续只处理新增或修改的文件。不要同时运行多个写入/重建任务。

## 日志定时清理

`startup.sh start` 启动成功后会注册日志清理 cron，每小时服务器本地时间 **:17** 执行一次。旧版服务使用非追加方式打开 `server.log`，**首次升级请用 `bash startup.sh restart` 重启一次**，切换到追加写入，避免截断后产生稀疏空洞；之后可用 `bash startup.sh cron-add` 注册，无需重启；`cron-status` 查看状态，`cron-remove` 或 `stop` 会移除项目的定时任务（包含原有爬取、修复任务）。需要系统安装并运行 cron。

- **不扫描、不删除、不修改 `data/` 下的任何文件，包括日志、论文和数据库。**
- 仅处理根目录白名单：`server.log`、`cron_crawl.log`、`cron_fix_abstracts.log`、`cron_log_cleanup.log`。
- 单个日志超过 **10 MiB** 时，原地保留末尾 10 MiB；未超限的不动，不生成额外归档。保留原 inode，服务日志改为追加写入，清理后运行中的进程可继续输出，无需重启。
- 每小时检查而非实时限额，两次检查之间日志仍可能超过阈值。原地复制截断与并发写入之间有小窗口，可能丢失少量日志；末尾内容也可能从半行开始，不适合审计日志。
- 符号链接不跟随，单个文件失败会继续检查其他日志并返回退出码 `1`。清理命令不加载 Web、模型或数据库模块。

```bash
# 预览，不修改任何日志
bash run_maintenance.sh logs-cleanup --dry-run

# 手动清理（默认保留末尾 10 MiB）
bash run_maintenance.sh logs-cleanup

# 自定义单个日志保留大小（正整数，单位 MiB）
bash run_maintenance.sh logs-cleanup --max-size-mb 20

# 为已有部署注册定时任务；重复执行不会重复添加
bash startup.sh cron-add
bash startup.sh cron-status
```

以上是配置和使用方式；升级代码本身不会立即清理现有日志或修改系统 crontab。

## 独立维护命令

Advisor 页面的“历史补漏”“重新生成”，以及 Statistics 页面的“重新提取关键词”“AI 智能过滤”已迁移到命令行。页面保留报告阅读、日期选择、主题设置、统计展示及手动关键词排除；维护操作不再提供 Web API。

使用项目根目录的 Bash 入口，无需启动 Web 服务或登录：

```bash
bash run_maintenance.sh --help
bash run_maintenance.sh keywords-filter --help

# 只补齐缺失历史研报，按日期升序执行，不额外生成今日报告
bash run_maintenance.sh advisor-backfill

# 强制重做全部历史日期（会覆盖已有研报，并产生模型调用费用）
bash run_maintenance.sh advisor-backfill --force

# 强制重新生成指定日期研报
bash run_maintenance.sh advisor-regenerate --date 2026-08-12

# 可选：从现有 AI.keywords 全量重建索引和统计（不调用模型；无该字段的旧数据无关键词）
bash run_maintenance.sh keywords-reextract

# 按日期、语言与分类选取热门前 100 个关键词，输出建议排除名单
bash run_maintenance.sh keywords-filter \
  --start-date 2026-08-01 --end-date 2026-08-12 \
  --lang Chinese --category cs.CV,eess.IV > excluded-keywords.json

# 或读取 JSON 字符串数组（文件输入最多检查 150 个非空、不重复词）
printf '%s\n' '["proposed method", "CLIP", "Remote Sensing"]' | \
  bash run_maintenance.sh keywords-filter --keywords-file -
```

默认使用项目内的 `data/` 和 `data/statistics.db`，可用 `--data-dir`、`--db-path` 指定目标（`keywords-filter` 不使用 `--data-dir`）。Advisor 命令支持 `--topic`，过滤命令支持 `--model-name`。脚本优先使用项目 `uv run python`，其次 `.venv/bin/python`，最后 `python3`；Python 入口加载项目 `.env`，模型配置沿用现有业务逻辑。

过滤 stdout 是 JSON，包含 `excluded_keywords`、`total_checked` 和 `excluded_count`；日志写入 stderr。无 API Key 或模型调用失败时，沿用启发式规则回退。**过滤只提供建议，不删除数据库关键词，也不自动修改浏览器排除列表**；可在 Statistics 页面已有手动排除控件中选择这些词。

任务成功退出码为 `0`，执行失败（包括历史补漏部分日期失败）为 `1`，参数错误为 `2`。补漏遇到单日错误会继续处理其他日期。原有 `run.sh` 每日流水线和 `ai/advisor.py --date ... --backfill` 保持兼容。

### 全量重建注意事项

- 日常升级无需全量重建，也不会回填历史数据。手动重建只读取 `AI.keywords`，会移除没有该字段的旧数据在索引中的关键词。
- 重建关键词前备份目标数据库，在维护窗口暂停 Web 服务及相关定时写入任务，避免跨进程并发写入。
- 全量重建会清空并逐批写入关键词索引，不保证失败时原子回滚；修复错误后可重新运行。不存在数据目录或没有增强数据文件时，在清空索引前终止。
- 独立命令无法清除运行中的 Web 进程缓存。统计缓存最长约 300 秒；等待缓存过期后刷新，或重启 Web 服务以立即加载新数据。
- Advisor 生成会写入/覆盖对应研报；历史 `--force` 会重做所有有源文件的日期。
