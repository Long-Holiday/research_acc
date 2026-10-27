"""Bound project logs without importing the web or AI application."""

import fcntl
import os
from pathlib import Path
import stat


ROOT_LOGS = ("server.log", "cron_crawl.log", "cron_fix_abstracts.log", "cron_log_cleanup.log")


def cleanup_logs(project_dir, max_bytes=10 * 1024 * 1024, dry_run=False):
    if max_bytes <= 0:
        raise ValueError("日志大小必须大于 0")
    root = Path(project_dir)
    results = []
    # 严格白名单：不扫描或修改 data/ 下的任何文件。
    for name in ROOT_LOGS:
        path = root / name
        try:
            # 不跟随符号链接，也不阻塞在误命名的 FIFO 等特殊文件上。
            flags = (os.O_RDONLY if dry_run else os.O_RDWR) | os.O_NOFOLLOW | os.O_NONBLOCK
            fd = os.open(path, flags)
            with os.fdopen(fd, "rb" if dry_run else "r+b") as log:
                info = os.fstat(log.fileno())
                if not stat.S_ISREG(info.st_mode):
                    continue
                # 避免多个清理进程同时改写同一个日志；不要求日志写入方持锁。
                try:
                    fcntl.flock(log, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                if info.st_size > max_bytes:
                    if not dry_run:
                        log.seek(-max_bytes, os.SEEK_END)
                        tail = log.read(max_bytes)
                        # copy-truncate 保留 inode，配合追加写入使运行中的进程继续正常输出。
                        log.seek(0)
                        log.write(tail)
                        log.truncate()
                    results.append({"path": str(path), "action": "truncate", "bytes_before": info.st_size})
        except FileNotFoundError:
            continue
        except OSError as exc:
            # 一个文件失败仍继续清理其他文件，并让调用方报告非零退出码。
            results.append({"path": str(path), "action": "error", "error": str(exc)})
    return results
