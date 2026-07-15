# server.py
# 入口点及测试兼容代理层

import app.config as config
from app.main import app
from server_modules.database import connect_db

# 仅实现 __getattr__。当外部（如单元测试）访问 server.ACCESS_PASSWORD 等属性时，
# 如果它们在 server 模块 __dict__ 中尚未被修改定义，
# 就会触发 __getattr__ 并从 config 中获取。
def __getattr__(name):
    if name in ("ACCESS_PASSWORD", "DB_PATH", "active_sessions"):
        return getattr(config, name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
