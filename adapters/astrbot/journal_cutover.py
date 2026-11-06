"""Explicit, one-time legacy enrollment; invoked only by release tooling.

This does not recover old wire history or mint a summary/config permission.
The operator must hold the owning worker before calling it. Memory remains
untouched; the first successful journal commit consumes each enrollment.
"""
import json
import re
import time


def enroll_legacy_scopes(db, release):
    if not isinstance(release, str) or not re.fullmatch(r'[a-f0-9]{40}', release):
        raise ValueError('cutover_requires_source_commit')
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'fishbot_readonly_cutover' in tables:
        rows = db.execute('SELECT DISTINCT release FROM fishbot_readonly_cutover').fetchall()
        if rows and rows != [(release,)]:
            raise ValueError('legacy_cutover_already_enrolled')
        return 0
    if 'fishbot_readonly_journals' in tables:
        raise ValueError('cannot_enroll_existing_guarded_runtime')
    scopes = set()
    for table in ('memory_rounds', 'memory_summaries', 'memory_summary_origins'):
        if table in tables:
            scopes.update(r[0] for r in db.execute('SELECT DISTINCT scope FROM '+table)
                          if isinstance(r[0], str) and re.fullmatch(r'(private|group):\d{4,20}', r[0]))
    if len(scopes) > 4096:
        raise ValueError('legacy_scope_inventory_invalid')
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute("CREATE TABLE fishbot_readonly_cutover(scope TEXT PRIMARY KEY,release TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('available','consumed')),enrolled REAL NOT NULL)")
        db.executemany('INSERT INTO fishbot_readonly_cutover VALUES(?,?,\'available\',?)',
                       [(json.dumps(s, ensure_ascii=False, separators=(',', ':'), sort_keys=True), release, time.time()) for s in sorted(scopes)])
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return len(scopes)
