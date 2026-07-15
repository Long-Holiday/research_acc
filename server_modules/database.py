import sqlite3

def connect_db(db_path):
    conn = sqlite3.connect(db_path, timeout=10.0, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception as e:
        print(f"Error setting WAL mode: {e}")
    return conn
