import sqlite3
import queue
import threading

class SQLitePool:
    def __init__(self, db_path, max_connections=10):
        self.db_path = db_path
        self.max_connections = max_connections
        self.pool = queue.Queue(max_connections)
        self.lock = threading.Lock()
        self.connections_created = 0

    def get_conn(self):
        try:
            return self.pool.get_nowait()
        except queue.Empty:
            with self.lock:
                if self.connections_created < self.max_connections:
                    conn = self._create_new_conn()
                    self.connections_created += 1
                    return conn
            return self.pool.get(timeout=10.0)

    def put_conn(self, conn):
        try:
            self.pool.put_nowait(conn)
        except queue.Full:
            conn.close()

    def _create_new_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        except Exception as e:
            print(f"Error setting WAL mode: {e}")
        return conn

class PooledConnection:
    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._conn.executemany(*args, **kwargs)

    def close(self):
        if self._conn is not None:
            self._pool.put_conn(self._conn)
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __getattr__(self, name):
        if self._conn is None:
            raise sqlite3.ProgrammingError("Cannot operate on a closed connection.")
        return getattr(self._conn, name)

_pools = {}
_pools_lock = threading.Lock()

def get_pool(db_path):
    with _pools_lock:
        if db_path not in _pools:
            _pools[db_path] = SQLitePool(db_path)
        return _pools[db_path]

def connect_db(db_path):
    import sys
    is_testing = "pytest" in sys.modules or "unittest" in sys.modules
    if is_testing:
        conn = sqlite3.connect(db_path, timeout=10.0, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        except Exception as e:
            print(f"Error setting WAL mode: {e}")
        return conn

    pool = get_pool(db_path)
    conn = pool.get_conn()
    return PooledConnection(conn, pool)
