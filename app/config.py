import sys
import os
from dotenv import load_dotenv

load_dotenv()

# 清理环境变量中的空白符和换行符，防止因 \r 导致解析报错
for k in os.environ:
    os.environ[k] = os.environ[k].strip()

class ConfigModule(object):
    def __init__(self):
        self._ACCESS_PASSWORD = os.getenv("ACCESS_PASSWORD", "")
        self._DB_PATH = "data/statistics.db"
        self._active_sessions = {}  # token -> expiry_timestamp

    @property
    def ACCESS_PASSWORD(self):
        server_mod = sys.modules.get('server')
        if server_mod:
            return server_mod.__dict__.get('ACCESS_PASSWORD', self._ACCESS_PASSWORD)
        return self._ACCESS_PASSWORD

    @ACCESS_PASSWORD.setter
    def ACCESS_PASSWORD(self, value):
        self._ACCESS_PASSWORD = value

    @property
    def DB_PATH(self):
        server_mod = sys.modules.get('server')
        if server_mod:
            return server_mod.__dict__.get('DB_PATH', self._DB_PATH)
        return self._DB_PATH

    @DB_PATH.setter
    def DB_PATH(self, value):
        self._DB_PATH = value

    @property
    def active_sessions(self):
        server_mod = sys.modules.get('server')
        if server_mod:
            return server_mod.__dict__.get('active_sessions', self._active_sessions)
        return self._active_sessions

    @active_sessions.setter
    def active_sessions(self, value):
        self._active_sessions = value

# 替换系统模块，使得 app.config 拥有 property 的动态特性
sys.modules[__name__] = ConfigModule()
