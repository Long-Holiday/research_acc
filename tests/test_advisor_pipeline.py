import subprocess
import os
import pytest

def test_advisor_cli_help():
    result = subprocess.run(["python", "ai/advisor.py", "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--date" in result.stdout
    assert "--backfill" in result.stdout
