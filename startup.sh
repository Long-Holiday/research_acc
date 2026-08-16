#!/bin/bash

# 项目一键启动/管理脚本
# Project Startup/Management Script

APP_NAME="daily-paper-server"
PORT=8000
HOST="0.0.0.0"
LOG_FILE="server.log"
PID_FILE=".server.pid"

# 获取脚本所在目录，确保在项目根目录下执行
# Get the directory of this script, ensure execution in the project root
cd "$(dirname "$0")"

# 获取项目的绝对路径
PROJECT_DIR="$(pwd)"

# 1. 每日爬取与 AI 研报定时任务 (每天凌晨 4:09 自动执行)
CRON_SCHEDULE_CRAWL="9 4 * * *"
CRON_IDENTIFIER_CRAWL="PROJECT_IDENTIFIER=daily_arxiv_crawl"
CRON_LINE_CRAWL="$CRON_SCHEDULE_CRAWL export PATH=\$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin && cd $PROJECT_DIR && $CRON_IDENTIFIER_CRAWL ./run.sh >> $PROJECT_DIR/cron_crawl.log 2>&1"

# 2. 缺失摘要定期检测与修复定时任务 (每月 15 日和 30 日 18:00 自动执行，修复过去 15-30 天的论文)
CRON_SCHEDULE_FIX="0 18 15,30 * *"
CRON_IDENTIFIER_FIX="PROJECT_IDENTIFIER=daily_paper_fix_abstracts"
CRON_LINE_FIX="$CRON_SCHEDULE_FIX export PATH=\$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin && cd $PROJECT_DIR && $CRON_IDENTIFIER_FIX ./run_fix_abstracts.sh >> $PROJECT_DIR/cron_fix_abstracts.log 2>&1"

# 定时任务辅助函数
add_cron() {
    chmod +x ./run.sh ./run_fix_abstracts.sh 2>/dev/null
    
    # 1. 添加每日爬取任务
    if crontab -l 2>/dev/null | grep -F "$CRON_IDENTIFIER_CRAWL" >/dev/null; then
        echo "每日爬取定时任务 (4:09) 已配置，无需重复添加。"
    else
        (crontab -l 2>/dev/null; echo "$CRON_LINE_CRAWL") | crontab -
        echo "✅ 每日爬取定时任务已成功添加到系统 crontab 中 (每天 4:09 自动执行)。"
    fi

    # 2. 添加摘要修复任务
    if crontab -l 2>/dev/null | grep -F "$CRON_IDENTIFIER_FIX" >/dev/null; then
        echo "摘要修复定时任务 (每月 15/30 日 18:00) 已配置，无需重复添加。"
    else
        (crontab -l 2>/dev/null; echo "$CRON_LINE_FIX") | crontab -
        echo "✅ 摘要修复定时任务已成功添加到系统 crontab 中 (每月 15 日和 30 日 18:00 自动执行，覆盖过去 15-30 天论文)。"
    fi
}

remove_cron() {
    local modified=false
    if crontab -l 2>/dev/null | grep -F "$CRON_IDENTIFIER_CRAWL" >/dev/null; then
        crontab -l 2>/dev/null | grep -v -F "$CRON_IDENTIFIER_CRAWL" | crontab -
        echo "每日爬取定时任务已从系统 crontab 中移除。"
        modified=true
    fi
    if crontab -l 2>/dev/null | grep -F "$CRON_IDENTIFIER_FIX" >/dev/null; then
        crontab -l 2>/dev/null | grep -v -F "$CRON_IDENTIFIER_FIX" | crontab -
        echo "摘要修复定时任务已从系统 crontab 中移除。"
        modified=true
    fi
    if [ "$modified" = "false" ]; then
        echo "未发现相关的定时任务 (cron)，无需移除。"
    fi
}

check_cron_status() {
    echo "=== 定时任务 (cron) 状态检查 ==="
    if crontab -l 2>/dev/null | grep -F "$CRON_IDENTIFIER_CRAWL" >/dev/null; then
        echo "1. 每日论文爬取任务: [已启用]"
        local expr1
        expr1=$(crontab -l 2>/dev/null | grep -F "$CRON_IDENTIFIER_CRAWL")
        echo "   $expr1"
    else
        echo "1. 每日论文爬取任务: [已禁用]"
    fi

    if crontab -l 2>/dev/null | grep -F "$CRON_IDENTIFIER_FIX" >/dev/null; then
        echo "2. 摘要定期修复任务: [已启用]"
        local expr2
        expr2=$(crontab -l 2>/dev/null | grep -F "$CRON_IDENTIFIER_FIX")
        echo "   $expr2"
    else
        echo "2. 摘要定期修复任务: [已禁用]"
    fi
}

# 检查 uv 命令是否存在
# Check if uv command exists
if command -v uv >/dev/null 2>&1; then
    RUN_CMD="uv run uvicorn server:app --host $HOST --port $PORT"
else
    # 尝试激活虚拟环境或直接使用 uvicorn
    # Try to activate virtual env or use uvicorn directly
    if [ -d ".venv" ]; then
        RUN_CMD=".venv/bin/uvicorn server:app --host $HOST --port $PORT"
    else
        RUN_CMD="uvicorn server:app --host $HOST --port $PORT"
    fi
fi

# 检查并安装依赖和模型
# Check and install dependencies and models
install_dependencies() {
    echo "正在检查并安装项目依赖及 spaCy 模型..."
    if command -v uv >/dev/null 2>&1; then
        echo "检测到 uv，正在确保依据 pyproject.toml 安装项目依赖..."
        uv pip install -r pyproject.toml
        if ! uv run python -c "import spacy; spacy.load('en_core_web_sm')" >/dev/null 2>&1; then
            echo "正在下载 spaCy 模型 en_core_web_sm..."
            uv run python -m spacy download en_core_web_sm
        else
            echo "spaCy 模型 en_core_web_sm 已存在，无需下载。"
        fi
    elif [ -d ".venv" ]; then
        echo "检测到 .venv，正在安装项目依赖..."
        if [ -f "pyproject.toml" ]; then
            .venv/bin/pip install -r pyproject.toml
        else
            .venv/bin/pip install spacy
        fi
        if ! .venv/bin/python -c "import spacy; spacy.load('en_core_web_sm')" >/dev/null 2>&1; then
            echo "正在下载 spaCy 模型 en_core_web_sm..."
            .venv/bin/python -m spacy download en_core_web_sm
        else
            echo "spaCy 模型 en_core_web_sm 已存在，无需下载。"
        fi
    else
        echo "未检测到虚拟环境，尝试全局/当前 Python 环境安装..."
        if [ -f "pyproject.toml" ]; then
            pip install -r pyproject.toml
        else
            pip install spacy
        fi
        if ! python -c "import spacy; spacy.load('en_core_web_sm')" >/dev/null 2>&1; then
            echo "正在下载 spaCy 模型 en_core_web_sm..."
            python -m spacy download en_core_web_sm
        else
            echo "spaCy 模型 en_core_web_sm 已存在，无需下载。"
        fi
    fi
    echo "依赖及模型检查完成。"
}

start() {
    # 启动前先检查并安装依赖和模型
    install_dependencies

    # 检查是否已经在运行
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "服务 $APP_NAME 已经在运行，PID: $PID"
            exit 0
        fi
    fi

    echo "正在启动服务 $APP_NAME..."
    echo "运行命令: $RUN_CMD"
    
    # 后台启动并记录 PID
    nohup $RUN_CMD > "$LOG_FILE" 2>&1 &
    PID=$!
    echo $PID > "$PID_FILE"
    
    # 稍等片刻，检查是否启动成功
    sleep 2
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "服务 $APP_NAME 启动成功！"
        echo "PID: $PID"
        echo "访问地址: http://localhost:$PORT"
        echo "日志输出: $LOG_FILE"
        add_cron
    else
        echo "服务 $APP_NAME 启动失败，请检查日志 $LOG_FILE"
        exit 1
    fi
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "正在停止服务 $APP_NAME (PID: $PID)..."
            kill "$PID"
            # 循环等待进程结束，最多等待10秒
            for i in {1..10}; do
                if ! ps -p "$PID" > /dev/null 2>&1; then
                    break
                fi
                sleep 1
            done
            
            # 如果还在运行，强制结束
            if ps -p "$PID" > /dev/null 2>&1; then
                echo "服务未响应，正在强制停止..."
                kill -9 "$PID"
            fi
            
            echo "服务已停止。"
        else
            echo "PID 文件存在，但未找到 PID $PID 对应的进程。服务可能已挂掉。"
        fi
        rm -f "$PID_FILE"
    else
        echo "未找到 PID 文件，服务可能未在运行。"
    fi
    remove_cron
}

status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "服务 $APP_NAME 正在运行，PID: $PID"
            echo "运行命令: $RUN_CMD"
            echo "访问地址: http://localhost:$PORT"
            # 打印最后几行日志
            echo "=== 最近的日志输出 ==="
            tail -n 10 "$LOG_FILE"
        else
            echo "服务 $APP_NAME 未运行 (PID 文件存在，但进程不存在)。"
        fi
    else
        echo "服务 $APP_NAME 未在运行。"
    fi
    echo ""
    check_cron_status
}

restart() {
    stop
    sleep 1
    start
}

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    status)
        status
        ;;
    restart)
        restart
        ;;
    cron-add)
        add_cron
        ;;
    cron-remove)
        remove_cron
        ;;
    cron-status)
        check_cron_status
        ;;
    *)
        # 默认一键启动
        start
        ;;
esac
