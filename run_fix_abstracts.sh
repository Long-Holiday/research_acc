#!/bin/bash

# 定时任务执行脚本：自动检测并修正过去 15-30 天内缺失摘要的论文，并执行 AI 增强与数据库同步
# Scheduled Fix Script: Automatically detect & fix missing abstracts for papers published 15-30 days ago

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

# 加载 .env 配置文件
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

echo "=========================================================================="
echo "🕒 [$(date '+%Y-%m-%d %H:%M:%S')] 启动周期性论文缺失摘要检测与 AI 修复任务..."
echo "📂 项目目录: $PROJECT_DIR"
echo "🎯 时间范围: 过去 15 至 30 天的论文"
echo "=========================================================================="

# 激活 Python 虚拟环境
if [ -d ".venv" ]; then
    PYTHON_EXEC=".venv/bin/python"
elif command -v uv >/dev/null 2>&1; then
    PYTHON_EXEC="uv run python"
else
    PYTHON_EXEC="python3"
fi

# 执行修复脚本，默认检查过去 15 至 30 天的论文
$PYTHON_EXEC fix_missing_abstracts.py --days-range 15 30 --max-workers 2 "$@"
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] 摘要修复任务顺利完成！"
else
    echo "❌ [$(date '+%Y-%m-%d %H:%M:%S')] 摘要修复任务执行异常，退出码: $EXIT_CODE"
fi
echo "=========================================================================="

exit $EXIT_CODE
