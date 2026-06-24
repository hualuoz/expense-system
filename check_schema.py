import sqlite3
conn = sqlite3.connect(r"C:\Users\lijunhua\.catpaw\skills\expense-reimbursement\web\data.db")
for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
    print(r[0])
    cols = conn.execute(f"PRAGMA table_info({r[0]})").fetchall()
    for c in cols:
        print(f"  {c[1]:20s} {c[2]:10s} {'PK' if c[5] else ''}")
conn.close()
