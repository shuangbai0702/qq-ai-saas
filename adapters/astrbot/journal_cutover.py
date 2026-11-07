"""Explicit, one-time legacy enrollment; invoked only by release tooling.

This does not recover old wire history or mint a summary/config permission.
The operator must hold the owning worker before calling it. Memory remains
untouched; the first successful journal commit consumes each enrollment.
"""
import json
import hashlib
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


def archive_authorized_scope(db,scope,expected_sha,authorization,release):
    """Offline operator action, NEVER a runtime recovery fallback.

    Explicitly end one logical conversation. Its exact wire and unsummarized
    evidence remain in an immutable archive. New rounds start independently;
    no summary coverage, 35-round permit or external receipt is manufactured.
    The owning worker MUST be stopped, with a separate SQLite backup.
    """
    if not re.fullmatch(r'(?:private|group):\d{4,20}',str(scope)) or not re.fullmatch(r'[a-f0-9]{64}',str(expected_sha)):
        raise ValueError('invalid_operator_scope')
    if not isinstance(authorization,str) or not 8<=len(authorization)<=128 or not re.fullmatch(r'[a-f0-9]{40}',str(release)):
        raise ValueError('explicit_authorization_required')
    if db.in_transaction:raise ValueError('operator_requires_owned_transaction')
    key=json.dumps(scope,ensure_ascii=False,separators=(',',':'),sort_keys=True)
    db.execute('BEGIN IMMEDIATE')
    try:
        row=db.execute('SELECT scope,version,state,payload,sha,updated FROM fishbot_readonly_journals WHERE scope=?',(key,)).fetchone()
        if not row or row[4]!=expected_sha or hashlib.sha256(row[3].encode()).hexdigest()!=expected_sha:
            raise ValueError('operator_journal_changed')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        archive={'journal':dict(zip(('scope','version','state','payload','sha','updated'),row)),'evidence':{}}
        for name in ('memory_rounds','memory_round_material','memory_pending','memory_pending_rounds','memory_hold_rounds','memory_reconstruction_checkpoints','fishbot_system_scope_contract'):
            if name not in tables:continue
            cursor=db.execute('SELECT * FROM '+name+' WHERE scope=?',(scope,))
            archive['evidence'][name]={'columns':[c[0] for c in cursor.description],'rows':cursor.fetchall()}
        encoded=json.dumps(archive,ensure_ascii=False,separators=(',',':'))
        db.execute('''CREATE TABLE IF NOT EXISTS fishbot_operator_session_archives(
          authorization TEXT PRIMARY KEY,scope TEXT NOT NULL,release TEXT NOT NULL,archive TEXT NOT NULL,
          sha TEXT NOT NULL,created REAL NOT NULL,new_session_state TEXT NOT NULL CHECK(new_session_state IN ('available','consumed')))''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS operator_session_archive_immutable BEFORE UPDATE OF authorization,scope,release,archive,sha,created ON fishbot_operator_session_archives BEGIN SELECT RAISE(ABORT,'operator archive is immutable'); END''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS operator_session_archive_no_delete BEFORE DELETE ON fishbot_operator_session_archives BEGIN SELECT RAISE(ABORT,'operator archive is immutable'); END''')
        db.execute("INSERT INTO fishbot_operator_session_archives VALUES(?,?,?,?,?,?,'available')",(authorization,key,release,encoded,hashlib.sha256(encoded.encode()).hexdigest(),time.time()))
        # These belong to the explicitly closed conversation, not the new
        # Journal. Archive first; preserve all task/dispatch/unknown evidence.
        for name in ('memory_hold_rounds','memory_pending_rounds','memory_pending','memory_rounds','memory_round_material','fishbot_system_scope_contract'):
            if name in tables:db.execute('DELETE FROM '+name+' WHERE scope=?',(scope,))
        if 'memory_reconstruction_checkpoints' in tables:
            db.execute("UPDATE memory_reconstruction_checkpoints SET state='invalidated' WHERE scope=? AND state IN ('available','pending_coverage')",(scope,))
        db.execute('DELETE FROM fishbot_readonly_journals WHERE scope=? AND sha=?',(key,expected_sha))
        db.commit()
        return {'authorization':authorization,'scope':scope,'archivedJournalSha':expected_sha,'archiveSha':hashlib.sha256(encoded.encode()).hexdigest()}
    except BaseException:
        db.rollback();raise
