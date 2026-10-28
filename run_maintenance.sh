#!/usr/bin/env bash

# 页面维护操作的独立命令入口，不依赖 Web 服务。
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

case "${1:-}" in
    data-process|keywords-reextract|advisor-backfill|advisor-regenerate)
        source "$PROJECT_DIR/task_lock.sh"
        acquire_task_lock || exit $?
        ;;
esac

export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1

if command -v uv >/dev/null 2>&1; then
    PYTHON_EXEC=(uv run python)
elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    PYTHON_EXEC=("$PROJECT_DIR/.venv/bin/python")
else
    PYTHON_EXEC=(python3)
fi

exec "${PYTHON_EXEC[@]}" "$PROJECT_DIR/maintenance.py" "$@"
