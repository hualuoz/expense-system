import sqlite3, os
# 找到正确的 db 路径
db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")
print(f"DB path: {db_path}")
print(f"Exists: {os.path.exists(db_path)}")
conn = sqlite3.connect(db_path)
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print(f"Tables: {[t[0] for t in tables]}")
for t in tables:
    cnt = conn.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
    print(f"  {t[0]}: {cnt} rows")
conn.close()
