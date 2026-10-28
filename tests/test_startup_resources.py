import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_start_checks_existing_process_without_installing_dependencies(tmp_path):
    shutil.copyfile(ROOT / "startup.sh", tmp_path / "startup.sh")
    (tmp_path / ".server.pid").write_text(str(os.getpid()))
    executable = tmp_path / "bin/uv"
    executable.parent.mkdir()
    trace = tmp_path / "uv.log"
    executable.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$UV_TRACE"\nexit 1\n')
    executable.chmod(0o755)
    result = subprocess.run(
        ["bash", str(tmp_path / "startup.sh"), "start"], cwd=tmp_path.parent,
        env={**os.environ, "PATH": f"{executable.parent}:{os.environ['PATH']}", "UV_TRACE": str(trace)},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "已经在运行" in result.stdout
    assert not trace.exists()


def test_dependency_install_is_explicit(tmp_path):
    shutil.copyfile(ROOT / "startup.sh", tmp_path / "startup.sh")
    executable = tmp_path / "bin/uv"
    executable.parent.mkdir()
    trace = tmp_path / "uv.log"
    executable.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$UV_TRACE"\n')
    executable.chmod(0o755)
    result = subprocess.run(
        ["bash", "startup.sh", "install"], cwd=tmp_path,
        env={**os.environ, "PATH": f"{executable.parent}:{os.environ['PATH']}", "UV_TRACE": str(trace)},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    commands = trace.read_text().splitlines()
    assert commands[0] == "pip install -r pyproject.toml"
    assert any("spacy.load" in command for command in commands)
    assert not any("uvicorn" in command for command in commands)
