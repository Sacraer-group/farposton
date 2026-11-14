"""Anonymous public-demo state; legacy local single-user data stays separate."""
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app, g, has_request_context, session


def owner():
    return getattr(g, 'visitor_id', 'local') if has_request_context() else 'local'


def init_visitors(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS visitors (id TEXT PRIMARY KEY, seen_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS visitor_profiles (visitor_id TEXT PRIMARY KEY REFERENCES visitors(id) ON DELETE CASCADE, profile_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS visitor_decisions (id INTEGER PRIMARY KEY, visitor_id TEXT NOT NULL REFERENCES visitors(id) ON DELETE CASCADE, source TEXT NOT NULL, source_id TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('save','skip')), created_at TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS visitor_decisions_owner ON visitor_decisions(visitor_id,id);
    ''')


def begin(conn):
    if not current_app.config['PUBLIC_DEMO']:
        g.visitor_id = 'local'
        return
    session.permanent = True
    if 'visitor_id' not in session:
        session['visitor_id'] = secrets.token_urlsafe(24)
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    g.visitor_id = session['visitor_id']
    now = datetime.now(timezone.utc)
    conn.execute('INSERT INTO visitors(id,seen_at) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET seen_at=excluded.seen_at', (g.visitor_id,now.isoformat()))
    cutoff=(now-timedelta(days=30)).isoformat()
    expired=[r[0] for r in conn.execute('SELECT id FROM visitors WHERE seen_at < ?', (cutoff,))]
    # Trip tables are additive and may not exist during a migration.
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for identifier in expired:
        if 'trip_schedules' in tables:
            columns={r[1] for r in conn.execute('PRAGMA table_info(trip_schedules)')}
            if 'visitor_id' in columns:
                conn.execute('DELETE FROM trip_schedules WHERE visitor_id=?',(identifier,))
        conn.execute('DELETE FROM visitors WHERE id=?',(identifier,))
    conn.commit()
