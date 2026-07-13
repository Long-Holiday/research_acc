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

# Cron 定时任务配置 (每天晚上 20:11 自动执行)
CRON_SCHEDULE="45 1 * * *"
CRON_IDENTIFIER="PROJECT_IDENTIFIER=daily_arxiv_crawl"
CRON_LINE="$CRON_SCHEDULE export PATH=\$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin && cd $PROJECT_DIR && $CRON_IDENTIFIER ./run.sh >> $PROJECT_DIR/cron_crawl.log 2>&1"

# 定时任务辅助函数
add_cron() {
    # 检查是否已存在
    if crontab -l 2>/dev/null | grep -F "$CRON_IDENTIFIER" >/dev/null; then
        echo "定时任务 (cron) 已配置，无需重复添加。"
    else
        # 确保 run.sh 具有执行权限
        chmod +x ./run.sh
        # 添加至 crontab
        (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
        echo "定时任务 (cron) 已成功添加到系统 crontab 中 (每天 20:11 自动执行)。"
    fi
}

remove_cron() {
    if crontab -l 2>/dev/null | grep -F "$CRON_IDENTIFIER" >/dev/null; then
        crontab -l 2>/dev/null | grep -v -F "$CRON_IDENTIFIER" | crontab -
        echo "定时任务 (cron) 已从系统 crontab 中移除。"
    else
        echo "未发现相关的定时任务 (cron)，无需移除。"
    fi
}

check_cron_status() {
    if crontab -l 2>/dev/null | grep -F "$CRON_IDENTIFIER" >/dev/null; then
        echo "定时任务 (cron) 状态: [已启用]"
        local cron_expr
        cron_expr=$(crontab -l 2>/dev/null | grep -F "$CRON_IDENTIFIER")
        echo "  表达式: $cron_expr"
    else
        echo "定时任务 (cron) 状态: [已禁用]"
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

start() {
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
