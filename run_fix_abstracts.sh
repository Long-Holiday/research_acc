#!/bin/bash

# 定时任务执行脚本：
# 1. 自动检测并补全所有期刊在过去 15-30 天内缺失的论文并进行 AI 增强
# 2. 自动检测并修复过去 15-30 天内缺失摘要的论文，完成最终 AI 增强与数据库同步
# Scheduled Fix Script:
# 1. Backfill missing papers for all journals published 15-30 days ago
# 2. Detect & fix missing abstracts for papers, and sync statistics database

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

# 加载 .env 配置文件
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

echo "=========================================================================="
echo "🕒 [$(date '+%Y-%m-%d %H:%M:%S')] 启动周期性期刊论文补录与缺失摘要修复工作流..."
echo "📂 项目目录: $PROJECT_DIR"
echo "🎯 目标范围: 默认过去 15 至 30 天的论文 (全期刊补录 + 全源摘要挽救)"
echo "=========================================================================="

# 激活 Python 虚拟环境
if [ -d ".venv" ]; then
    PYTHON_EXEC=".venv/bin/python"
elif command -v uv >/dev/null 2>&1; then
    PYTHON_EXEC="uv run python"
else
    PYTHON_EXEC="python3"
fi

# 处理传入参数：如果外部没有显式传入日期/天数参数，则默认应用 --days-range 15 30 --max-workers 2
PARAMS=("$@")
if [ $# -eq 0 ]; then
    PARAMS=("--days-range" "15" "30" "--max-workers" "2")
fi

echo ""
echo "▶️  [步骤 1/2] 开始扫描并补全所有期刊缺失的论文..."
echo "--------------------------------------------------------------------------"
# 步骤 1 执行：补全所有期刊论文，带 --skip-db-sync 避免中间阶段重复重建数据库
$PYTHON_EXEC backfill_ieee_papers.py --all-journals --skip-db-sync "${PARAMS[@]}"
STEP1_EXIT=$?

if [ $STEP1_EXIT -ne 0 ]; then
    echo "⚠️ [$(date '+%Y-%m-%d %H:%M:%S')] 步骤 1 (论文补录) 报告异常 (退出码: $STEP1_EXIT)，继续执行步骤 2..."
else
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] 步骤 1 (论文补录) 执行完毕。"
fi

echo ""
echo "▶️  [步骤 2/2] 开始检测并补全缺失摘要，并同步统计数据库..."
echo "--------------------------------------------------------------------------"
# 步骤 2 执行：检测并补全所有缺失摘要，完成 AI 增强并同步 statistics.db
$PYTHON_EXEC fix_missing_abstracts.py "${PARAMS[@]}"
STEP2_EXIT=$?

if [ $STEP2_EXIT -ne 0 ]; then
    echo "❌ [$(date '+%Y-%m-%d %H:%M:%S')] 步骤 2 (摘要修复与数据库同步) 执行异常 (退出码: $STEP2_EXIT)"
    FINAL_EXIT=$STEP2_EXIT
else
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] 步骤 2 (摘要修复与数据库同步) 执行完毕。"
    FINAL_EXIT=0
fi

echo "=========================================================================="
if [ $FINAL_EXIT -eq 0 ]; then
    echo "🎉 [$(date '+%Y-%m-%d %H:%M:%S')] 整个期刊论文补录与摘要修复工作流顺利完成！"
else
    echo "⚠️ [$(date '+%Y-%m-%d %H:%M:%S')] 工作流执行结束，但存在异常 (退出码: $FINAL_EXIT)"
fi
echo "=========================================================================="

exit $FINAL_EXIT
