"""桌面扩展单独迁移；先备份，再在事务内建表。"""
import sqlite3
import uuid
from contextlib import closing

STATEMENTS = (
    'CREATE TABLE IF NOT EXISTS app_schema (version INTEGER PRIMARY KEY)',
    'CREATE TABLE IF NOT EXISTS app_previews (ticket TEXT PRIMARY KEY, config TEXT NOT NULL, expires REAL NOT NULL)',
    "CREATE TABLE IF NOT EXISTS app_sessions (run_id TEXT PRIMARY KEY REFERENCES runs(id), thread_id TEXT, state TEXT NOT NULL DEFAULT 'idle', error TEXT NOT NULL DEFAULT '')",
)
V2_STATEMENTS = (
    'CREATE TABLE IF NOT EXISTS app_control (run_id TEXT PRIMARY KEY REFERENCES runs(id), generation INTEGER NOT NULL DEFAULT 0)',
)
V3_STATEMENTS = (
    'CREATE TABLE IF NOT EXISTS app_ai_selection (run_id TEXT PRIMARY KEY REFERENCES runs(id), state TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS app_ai_cache (cache_key TEXT PRIMARY KEY, value TEXT NOT NULL, updated TEXT NOT NULL)',
)


def migrate_app(store):
    exists = store.conn.execute("SELECT name FROM sqlite_master WHERE name='app_schema'").fetchone()
    if exists and store.conn.execute('SELECT version FROM app_schema WHERE version=3').fetchone():
        return
    folder = store.root / 'backups'
    folder.mkdir(exist_ok=True)
    version = (3 if store.conn.execute('SELECT version FROM app_schema WHERE version=2').fetchone() else 2) if exists else 1
    with closing(sqlite3.connect(folder / f'before-app-v{version}-{uuid.uuid4().hex}.sqlite3')) as backup:
        store.conn.backup(backup)
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        for statement in STATEMENTS:
            store.conn.execute(statement)
        store.conn.execute('INSERT OR IGNORE INTO app_schema VALUES (1)')
        for statement in V2_STATEMENTS:
            store.conn.execute(statement)
        store.conn.execute('INSERT OR IGNORE INTO app_schema VALUES (2)')
        for statement in V3_STATEMENTS:
            store.conn.execute(statement)
        store.conn.execute('INSERT OR IGNORE INTO app_schema VALUES (3)')
