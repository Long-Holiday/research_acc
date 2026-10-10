from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def outside_maintenance_window(monkeypatch):
    # 测试默认在正常服务时段；维护边界测试显式覆盖时钟。
    import app.main
    monkeypatch.setattr(app.main, "local_now", lambda: datetime(2026, 10, 12, 12, 0))
