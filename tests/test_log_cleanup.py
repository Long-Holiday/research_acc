import json
import os
from pathlib import Path
import subprocess

import pytest

import maintenance
from server_modules.log_cleanup import ROOT_LOGS, cleanup_logs


def test_truncate_keeps_tail_inode_and_append_writer(tmp_path):
    path = tmp_path / "server.log"
    path.write_bytes(b"old logs\nrecent logs\n")
    inode = path.stat().st_ino
    with path.open("ab", buffering=0) as writer:
        results = cleanup_logs(tmp_path, max_bytes=12)
        assert path.read_bytes() == b"recent logs\n"
        assert path.stat().st_ino == inode
        writer.write(b"next\n")
    assert path.read_bytes() == b"recent logs\nnext\n"
    assert results[0]["action"] == "truncate"


def test_only_root_allowlist_is_touched(tmp_path):
    for name in ROOT_LOGS:
        (tmp_path / name).write_bytes(b"x" * 100)
    data = tmp_path / "data"
    data.mkdir()
    protected = [data / "advisor_2020-01-01.log", data / "enhance_2020-01-01.log",
                 data / "statistics.db", data / "papers.jsonl", tmp_path / "custom.log"]
    for path in protected:
        path.write_bytes(b"keep" * 100)
        os.utime(path, (1, 1))
    assert len(cleanup_logs(tmp_path, max_bytes=10)) == len(ROOT_LOGS)
    for name in ROOT_LOGS:
        assert (tmp_path / name).stat().st_size == 10
    for path in protected:
        assert path.read_bytes() == b"keep" * 100
        assert path.stat().st_mtime == 1


def test_dry_run_and_small_missing_files(tmp_path):
    large = tmp_path / "server.log"
    large.write_bytes(b"123456")
    small = tmp_path / "cron_crawl.log"
    small.write_bytes(b"12")
    result = cleanup_logs(tmp_path, max_bytes=3, dry_run=True)
    assert len(result) == 1
    assert large.read_bytes() == b"123456"
    assert small.read_bytes() == b"12"


def test_symlink_never_modifies_target_and_other_logs_still_cleaned(tmp_path):
    target = tmp_path / "important"
    target.write_bytes(b"keep data")
    (tmp_path / "server.log").symlink_to(target)
    (tmp_path / "cron_crawl.log").write_bytes(b"123456")
    results = cleanup_logs(tmp_path, max_bytes=3)
    assert target.read_bytes() == b"keep data"
    assert (tmp_path / "cron_crawl.log").read_bytes() == b"456"
    assert results[0]["action"] == "error"


def test_cli_preview_and_cleanup(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(maintenance, "PROJECT_DIR", tmp_path)
    path = tmp_path / "server.log"
    path.write_bytes(b"a" * (1024 * 1024 + 10))
    assert maintenance.main(["logs-cleanup", "--max-size-mb", "1", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert path.stat().st_size == 1024 * 1024 + 10
    assert maintenance.main(["logs-cleanup", "--max-size-mb", "1"]) == 0
    assert path.stat().st_size == 1024 * 1024


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_cli_invalid_size(value):
    with pytest.raises(SystemExit) as exc:
        maintenance.main(["logs-cleanup", "--max-size-mb", value])
    assert exc.value.code == 2


def test_cron_lifecycle_with_fake_crontab(tmp_path):
    # 使用临时 crontab 替身，不修改机器上的真实定时任务。
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "crontab"
    fake.write_text('#!/bin/bash\nif [ "$1" = "-l" ]; then\n  /bin/cat "$FAKE_CRONTAB"\nelse\n  /bin/cat > "$FAKE_CRONTAB.new"\n  /bin/mv "$FAKE_CRONTAB.new" "$FAKE_CRONTAB"\nfi\n')
    fake.chmod(0o755)
    table = tmp_path / "crontab.txt"
    table.write_text("0 0 * * * unrelated-command\n")
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_CRONTAB=str(table))
    script = Path(maintenance.__file__).parent / "startup.sh"

    def run(command):
        result = subprocess.run(["bash", str(script), command], cwd=tmp_path, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout

    run("cron-add")
    run("cron-add")
    content = table.read_text()
    assert content.count("PROJECT_IDENTIFIER=daily_paper_log_cleanup") == 1
    assert "17 * * * *" in content
    assert "logs-cleanup" in content
    assert "日志清理任务: [已启用]" in run("cron-status")
    run("cron-remove")
    assert table.read_text() == "0 0 * * * unrelated-command\n"
    assert "日志清理任务: [已禁用]" in run("cron-status")
