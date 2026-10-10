import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("enhance_exit,process_exit,partial", [
    (0, 0, False), (1, 0, False), (0, 1, False), (0, 0, True),
])
def test_daily_processing_runs_once_after_enhancement(tmp_path, enhance_exit, process_exit, partial):
    root = Path(__file__).resolve().parents[1]
    shutil.copyfile(root / "run.sh", tmp_path / "run.sh")
    for folder in ("bin", "data", "daily_paper", "ai"):
        (tmp_path / folder).mkdir()
    stub_python = tmp_path / "bin/python"
    stub_python.write_text("""#!/bin/bash
printf '%s\\n' "$*" >> "$TRACE_FILE"
case "$1" in
    enhance.py) exit "$ENHANCE_EXIT" ;;
    maintenance.py) exit "$PROCESS_EXIT" ;;
    *) exit 0 ;;
esac
""")
    stub_python.chmod(0o755)
    stub_scrapy = tmp_path / "bin/scrapy"
    stub_scrapy.write_text("""#!/bin/bash
printf '%s\\n' "$*" >> "$TRACE_FILE"
printf '{"id":"paper"}\\n' > "$4"
""")
    stub_scrapy.chmod(0o755)
    trace = tmp_path / "trace.log"
    env = {
        **os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        "OPENAI_API_KEY": "" if partial else "test-key",
        "GOOGLE_API_KEY": "", "TRACE_FILE": str(trace),
        "ENHANCE_EXIT": str(enhance_exit), "PROCESS_EXIT": str(process_exit),
    }
    result = subprocess.run(["bash", "run.sh", "--date", "2026-10-12"],
                            cwd=tmp_path, env=env, input="y\n", capture_output=True, text=True)
    commands = trace.read_text().splitlines()
    processing = [command for command in commands if command.startswith("maintenance.py")]
    if partial or enhance_exit:
        assert processing == []
        assert not any(command.startswith("ai/advisor.py") for command in commands)
    else:
        assert processing == ["maintenance.py data-process"]
        enhance_index = next(i for i, command in enumerate(commands) if command.startswith("enhance.py"))
        process_index = commands.index("maintenance.py data-process")
        assert enhance_index < process_index
        if not process_exit:
            advisor_index = next(i for i, command in enumerate(commands) if command.startswith("ai/advisor.py"))
            assert process_index < advisor_index
        else:
            assert not any(command.startswith("ai/advisor.py") for command in commands)
    assert result.returncode == (1 if enhance_exit or process_exit else 0), result.stdout + result.stderr


def test_weekend_pipeline_defers_database_sync(tmp_path):
    root = Path(__file__).resolve().parents[1]
    shutil.copyfile(root / "run_fix_abstracts.sh", tmp_path / "run_fix_abstracts.sh")
    executable = tmp_path / ".venv/bin/python"
    executable.parent.mkdir(parents=True)
    executable.write_text('#!/bin/bash\nprintf \'%s\\n\' "$*" >> "$TRACE_FILE"\n')
    executable.chmod(0o755)
    trace = tmp_path / "trace.log"
    result = subprocess.run(["bash", "run_fix_abstracts.sh"], cwd=tmp_path,
                            env={**os.environ, "TRACE_FILE": str(trace)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = trace.read_text().splitlines()
    assert len(commands) == 2
    assert commands[0].startswith("backfill_ieee_papers.py")
    assert commands[1].startswith("fix_missing_abstracts.py")
    assert all("--skip-db-sync" in command for command in commands)
    assert not any("maintenance.py" in command for command in commands)
