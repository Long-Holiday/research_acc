#!/usr/bin/env bash

# 所有离线写入入口共用一把锁；锁文件保留，进程退出时自动释放锁。
acquire_task_lock() {
    if ! command -v flock >/dev/null 2>&1; then
        printf '%s\n' '后台任务需要 flock，请安装 util-linux。' >&2
        return 1
    fi
    exec 9>"$PROJECT_DIR/.maintenance.lock" || return 1
    local lock_status
    if flock -n -E 75 9; then
        return 0
    else
        lock_status=$?
    fi
    if [[ "$lock_status" -eq 75 ]]; then
        printf '%s\n' '已有后台任务正在运行，本次未执行；请稍后重试。' >&2
    else
        printf '%s\n' '无法获取后台任务锁。' >&2
    fi
    return "$lock_status"
}
