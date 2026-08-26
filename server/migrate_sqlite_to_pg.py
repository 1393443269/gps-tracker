"""
SQLite -> PostgreSQL 数据迁移脚本
在服务器上运行（容器内或挂载环境）。步骤：
  1) 用 PG 后端跑 init_db() 建好所有表结构
  2) 逐表把 SQLite 数据搬到 PG
  3) 修正 PG 序列（SERIAL 自增值对齐已有最大 id）

用法（在 backend 容器内）：
  DB_BACKEND=postgres DATABASE_URL=postgresql://gps:gps_pw_2024@postgres:5432/gps \
  SQLITE_PATH=/app/data/tracker.db python migrate_sqlite_to_pg.py

安全：只读 SQLite、只写 PG，不改动 SQLite 源库。可重复运行（先清空 PG 表再导）。
"""
import os, sys, sqlite3

try: sys.stdout.reconfigure(encoding='utf-8')
except Exception: pass

SQLITE_PATH = os.environ.get('SQLITE_PATH', '/app/data/tracker.db')

if os.environ.get('DB_BACKEND', '').lower() != 'postgres':
    print("！必须设 DB_BACKEND=postgres 运行本脚本"); sys.exit(1)
if not os.path.exists(SQLITE_PATH):
    print(f"！SQLite 源库不存在: {SQLITE_PATH}"); sys.exit(1)

import app  # 以 PG 后端加载

print("=== 步骤1：在 PG 建表结构 ===")
app.init_db()
print("  建表完成")

# 读 SQLite
sconn = sqlite3.connect(SQLITE_PATH)
sconn.row_factory = sqlite3.Row
scur = sconn.cursor()

# 待迁移的表（按依赖顺序，父表在前）
scur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
all_tables = [r[0] for r in scur.fetchall()]
print(f"=== 步骤2：迁移数据，共 {len(all_tables)} 张表 ===")

pgconn = app.get_db()   # PG 连接（_ConnWrapper）
total_rows = 0
for tbl in all_tables:
    try:
        scur.execute(f"SELECT * FROM {tbl}")
        rows = scur.fetchall()
        if not rows:
            print(f"  {tbl}: 0 行，跳过"); continue
        cols = rows[0].keys()
        collist = ','.join(cols)
        ph = ','.join(['?'] * len(cols))
        # 先清空 PG 目标表，保证可重复运行
        pgconn.execute(f"DELETE FROM {tbl}")
        n = 0
        for r in rows:
            vals = [r[c] for c in cols]
            try:
                pgconn.execute(f"INSERT INTO {tbl} ({collist}) VALUES ({ph}) ON CONFLICT DO NOTHING", vals)
                n += 1
            except Exception as e:
                print(f"    行插入失败 {tbl}: {e}")
        pgconn.commit()
        total_rows += n
        print(f"  {tbl}: {n} 行")
    except Exception as e:
        print(f"  {tbl}: 表迁移失败 {e}")

print(f"=== 步骤3：对齐自增序列 ===")
# 对每张有 id 列的表，把序列重置到 max(id)+1
for tbl in all_tables:
    try:
        r = pgconn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=? AND column_name='id'", (tbl,)).fetchone()
        if not r:
            continue
        pgconn.execute(
            f"SELECT setval(pg_get_serial_sequence('{tbl}','id'), "
            f"COALESCE((SELECT MAX(id) FROM {tbl}),1))")
        pgconn.commit()
    except Exception as e:
        print(f"  {tbl} 序列对齐跳过: {e}")

print(f"\n=== 迁移完成：共 {total_rows} 行 ===")
sconn.close()
pgconn.close()
