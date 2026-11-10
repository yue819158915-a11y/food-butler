# -*- coding: utf-8 -*-
"""用户画像与饮食记录存储（SQLite）。

设计取舍：
- 无登录体系，用前端生成的匿名 device_id 作为主键
- 发布环境是否保留 data/ 未定，故 /api/health 回显探针信息便于验证
- 单文件 SQLite + WAL，足够 1~100 人的小规模使用
"""
import json
import os
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("DB_PATH") or os.path.join(ROOT, "data", "app.db")
PROBE_PATH = os.path.join(ROOT, "data", ".probe.json")

SCHEMA = """
CREATE TABLE IF NOT EXISTS probe (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  note TEXT
);
CREATE TABLE IF NOT EXISTS users (
  uid TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL,
  consent INTEGER DEFAULT 0,
  allergens TEXT DEFAULT '',     -- 逗号分隔：花生,海鲜,蛋奶...
  dislikes TEXT DEFAULT '',      -- 忌口/不爱吃
  goals TEXT DEFAULT '',         -- 减脂,增肌,控糖,维持
  taste TEXT DEFAULT '{}',       -- JSON：{标签: 权重}
  budget_per_meal INTEGER,
  people INTEGER DEFAULT 2
);
CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT NOT NULL,
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL,            -- meal | cook
  recipe_id TEXT,
  name TEXT,
  kcal REAL,
  protein_g REAL,
  carb_g REAL,
  fat_g REAL,
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_uid_ts ON logs(uid, ts DESC);
"""


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with _conn() as conn:
        conn.executescript(SCHEMA)
    return DB_PATH


def write_probe(note="startup"):
    """启动时写一条探针；同时落一个 json 便于跨版本比对。"""
    init_db()
    ts = int(time.time())
    with _conn() as conn:
        conn.execute("INSERT INTO probe (ts, note) VALUES (?, ?)", (ts, note))
    if not os.path.exists(PROBE_PATH):
        with open(PROBE_PATH, "w", encoding="utf-8") as f:
            json.dump({"first_seen": ts, "note": note}, f, ensure_ascii=False)
    return ts


def probe_status():
    init_db()
    with _conn() as conn:
        rows = conn.execute("SELECT COUNT(*) c, MIN(ts) first, MAX(ts) last FROM probe").fetchone()
    first_seen = None
    if os.path.exists(PROBE_PATH):
        try:
            first_seen = json.load(open(PROBE_PATH, encoding="utf-8")).get("first_seen")
        except Exception:
            first_seen = None
    return {
        "db_path": DB_PATH,
        "db_exists": os.path.exists(DB_PATH),
        "probe_rows": rows["c"],
        "first_probe_at": rows["first"],
        "last_probe_at": rows["last"],
        "file_first_seen": first_seen,
        "persisted": bool(first_seen and rows["first"] and rows["first"] <= first_seen + 5),
    }


# ---------- 用户画像 ----------

def upsert_user(uid, **fields):
    init_db()
    allowed = {"consent", "allergens", "dislikes", "goals", "taste", "budget_per_meal", "people"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if "taste" in fields and not isinstance(fields["taste"], str):
        fields["taste"] = json.dumps(fields["taste"], ensure_ascii=False)
    with _conn() as conn:
        row = conn.execute("SELECT uid FROM users WHERE uid=?", (uid,)).fetchone()
        if not row:
            conn.execute("INSERT INTO users (uid, created_at) VALUES (?, ?)", (uid, int(time.time())))
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE users SET {sets} WHERE uid=?", (*fields.values(), uid))
    return get_user(uid)


def get_user(uid):
    init_db()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["taste"] = json.loads(d.get("taste") or "{}")
    except Exception:
        d["taste"] = {}
    for k in ("allergens", "dislikes", "goals"):
        d[k] = [x for x in (d.get(k) or "").split(",") if x]
    return d


# ---------- 饮食记录 ----------

def add_log(uid, kind, name, kcal=None, protein_g=None, carb_g=None, fat_g=None, recipe_id=None, note=""):
    init_db()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO logs (uid, ts, kind, recipe_id, name, kcal, protein_g, carb_g, fat_g, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uid, int(time.time()), kind, recipe_id, name, kcal, protein_g, carb_g, fat_g, note))
    return True


def list_logs(uid, limit=30):
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM logs WHERE uid=? ORDER BY ts DESC LIMIT ?", (uid, limit)).fetchall()
    return [dict(r) for r in rows]
