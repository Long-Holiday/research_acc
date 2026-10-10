# Daily arXiv / Research Assistant

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

# 全量重新提取增强论文的关键词，重建索引和统计
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

- 重提关键词前备份目标数据库，在维护窗口暂停 Web 服务及相关定时写入任务，避免跨进程并发写入。
- 全量重建会清空并逐批写入关键词索引，不保证失败时原子回滚；修复错误后可重新运行。不存在数据目录或没有增强数据文件时，在清空索引前终止。
- 独立命令无法清除运行中的 Web 进程缓存。统计缓存最长约 300 秒；等待缓存过期后刷新，或重启 Web 服务以立即加载新数据。
- Advisor 生成会写入/覆盖对应研报；历史 `--force` 会重做所有有源文件的日期。
