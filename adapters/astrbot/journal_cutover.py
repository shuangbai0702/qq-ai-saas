"""Explicit, one-time legacy enrollment; invoked only by release tooling.

This does not recover old wire history or mint a summary/config permission.
The operator must hold the owning worker before calling it. Memory remains
untouched; the first successful journal commit consumes each enrollment.
"""
import json
import hashlib
import math
import re
import time

OWNER_EARLY_MIGRATION = 'owner_authorized_early_migration'


def source_timeline(db,expected):
    """Read the exact granted wire and verify every legacy QQ input's time.

    Distinct channels can share an event ID. A tied QQ clock across channels
    establishes simultaneity at the recorded precision, not a relative order.
    """
    from context_protocol import model_json,source
    from journal_storage import read_snapshot_payload
    from message_provenance import get

    def timestamp(value):
        return type(value) in (int,float) and math.isfinite(value) and value>0

    rows={json.loads(row[0]): row for row in db.execute(
        "SELECT scope,version,payload,sha FROM fishbot_readonly_journals WHERE state='ready'")}
    if {scope:row[3] for scope,row in rows.items() if scope!='bot_shared'}!=expected:
        raise ValueError('owner_migration_scope_inventory_changed')
    timeline=[];seen=set()
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for scope in sorted(expected):
        key,version,payload,sha=rows[scope]
        snapshot=read_snapshot_payload(db,key,version,payload,sha)
        previous=None
        for position,message in enumerate(snapshot['wire']):
            origin=source(message)
            if origin is None:continue
            event=str(origin['event_id']);identity=model_json([scope,event]);person=str(origin['sender_id'])
            if origin['scope']!=scope or identity in seen:
                raise ValueError('owner_migration_source_identity_conflict')
            observed={origin['at']} if timestamp(origin.get('at')) else set()
            if 'memory_message_sources' in tables:
                incoming=get(db,scope,event)
                if incoming.get('time_source')=='protocol' and timestamp(incoming.get('at')):
                    if str(incoming.get('sender_id'))!=person:raise ValueError('owner_migration_provenance_sender_conflict')
                    observed.add(incoming['at'])
            if 'pipeline_work_inputs' in tables:
                row=db.execute('SELECT payload FROM pipeline_work_inputs WHERE scope=? AND event=?',(scope,event)).fetchone()
                if row and row[0]:
                    raw=json.loads(row[0]);target=raw.get('group_id') if scope.startswith('group:') else raw.get('user_id')
                    if (str(raw.get('message_id'))!=event or str(raw.get('user_id'))!=person or str(target)!=scope.split(':')[1]):
                        raise ValueError('owner_migration_protocol_identity_conflict')
                    if timestamp(raw.get('time')):observed.add(raw['time'])
            if len(observed)!=1:raise ValueError('owner_migration_protocol_time_missing_or_conflicting')
            at=observed.pop()
            if previous is not None and previous>at:raise ValueError('owner_migration_wire_time_conflict')
            previous=at;seen.add(identity)
            timeline.append({'event':identity,'sender_id':person,'scope':scope,'at':at,'wire_position':position})
    return sorted(timeline,key=lambda row:(row['at'],row['scope'],row['wire_position']))


def _operator_archives(db):
    db.execute('''CREATE TABLE IF NOT EXISTS fishbot_operator_session_archives(
      authorization TEXT PRIMARY KEY,scope TEXT NOT NULL,release TEXT NOT NULL,archive TEXT NOT NULL,
      sha TEXT NOT NULL,created REAL NOT NULL,new_session_state TEXT NOT NULL CHECK(new_session_state IN ('available','consumed')))''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS operator_session_archive_immutable BEFORE UPDATE OF authorization,scope,release,archive,sha,created ON fishbot_operator_session_archives BEGIN SELECT RAISE(ABORT,'operator archive is immutable'); END''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS operator_session_archive_no_delete BEFORE DELETE ON fishbot_operator_session_archives BEGIN SELECT RAISE(ABORT,'operator archive is immutable'); END''')


def retire_empty_legacy_journals(db, bot_id, grant, backup_sha):
    """Retire System-only extra Journals under an exact offline operator grant.

    The owning worker must be fenced. The offline, independently verified
    SQLite archive owns the original bytes. The runtime holds only its receipt,
    so regular compression cannot discover the archived input as new history.
    """
    from journal_storage import read_snapshot_payload
    from readonly_journal import ReadonlyJournal, system_head
    from shared_context import JOURNAL_SCOPE, active

    if set(grant) != {'type','id','bot','destination','sources','reason','release'}:
        raise ValueError('owner_migration_grant_shape')
    if (grant['type'] != OWNER_EARLY_MIGRATION or grant['bot'] != bot_id
        or grant['destination'] != JOURNAL_SCOPE or not re.fullmatch(r'[0-9a-f-]{36}', bot_id)
        or not re.fullmatch(r'[0-9a-f-]{36}', str(grant['id']))
        or not re.fullmatch(r'[0-9a-f]{40}', str(grant['release']))
        or not re.fullmatch(r'[0-9a-f]{64}', str(backup_sha))
        or not isinstance(grant['reason'], str) or not grant['reason'].strip()):
        raise PermissionError('owner_migration_grant_invalid')
    expected = grant['sources']
    if (not isinstance(expected, dict) or not expected or any(
            not re.fullmatch(r'(?:private|group):\d{4,20}', scope)
            or not re.fullmatch(r'[0-9a-f]{64}', str(sha)) for scope, sha in expected.items())):
        raise PermissionError('owner_migration_scope_inventory_invalid')
    if db.in_transaction:
        raise ValueError('operator_requires_owned_transaction')
    from uuid import UUID
    if str(UUID(bot_id)) != bot_id or str(UUID(grant['id'])) != grant['id']:
        raise PermissionError('owner_migration_grant_invalid')
    destination = ReadonlyJournal(db, JOURNAL_SCOPE)
    if not active(db) or destination.recovery_error or destination.retired_reason or destination.last is None:
        raise ValueError('owner_migration_requires_verified_shared_destination')
    destination_key = destination.scope_key
    db.execute('BEGIN IMMEDIATE')
    try:
        rows = {json.loads(row[0]): row for row in db.execute(
            'SELECT scope,version,state,payload,sha,updated FROM fishbot_readonly_journals')}
        if not active(db) or JOURNAL_SCOPE not in rows or rows[JOURNAL_SCOPE][2] != 'ready':
            raise ValueError('owner_migration_destination_changed')
        _, version, _, payload, destination_sha, _ = rows[JOURNAL_SCOPE]
        read_snapshot_payload(db, destination_key, version, payload, destination_sha)
        db.execute('''CREATE TABLE IF NOT EXISTS fishbot_journal_owner_migrations(
          authorization TEXT PRIMARY KEY,receipt TEXT NOT NULL,sha TEXT NOT NULL,completed REAL NOT NULL)''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS journal_owner_migration_no_update BEFORE UPDATE ON fishbot_journal_owner_migrations
                      BEGIN SELECT RAISE(ABORT,'migration receipt is immutable'); END''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS journal_owner_migration_no_delete BEFORE DELETE ON fishbot_journal_owner_migrations
                      BEGIN SELECT RAISE(ABORT,'migration receipt is immutable'); END''')
        prior = db.execute('SELECT receipt,sha FROM fishbot_journal_owner_migrations WHERE authorization=?', (grant['id'],)).fetchone()
        if prior:
            saved = json.loads(prior[0])
            if (hashlib.sha256(prior[0].encode()).hexdigest() != prior[1]
                or saved.get('grant') != grant or saved.get('backupSha') != backup_sha or set(rows) != {JOURNAL_SCOPE}):
                raise ValueError('owner_migration_receipt_conflict')
            db.commit()
            return saved
        if {scope: row[4] for scope, row in rows.items() if scope != JOURNAL_SCOPE} != expected:
            raise ValueError('owner_migration_scope_inventory_changed')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'pipeline_scope_work' in tables and db.execute('SELECT 1 FROM pipeline_scope_work LIMIT 1').fetchone():
            raise ValueError('owner_migration_work_still_active')
        sources = []
        for scope, sha in sorted(expected.items()):
            key, version, state, payload, _, updated = rows[scope]
            if state != 'ready':
                raise ValueError('owner_migration_source_not_ready')
            snapshot = read_snapshot_payload(db, key, version, payload, sha)
            for field in ('base','wire'):
                messages = snapshot[field]
                if not isinstance(messages, list) or system_head(messages) != len(messages):
                    raise ValueError('owner_migration_source_requires_history_coverage')
            # A System-only visible snapshot cannot prove that old round,
            # pending or checkpoint material was already summarized elsewhere.
            for table in ('memory_rounds','memory_pending','memory_hold_rounds','memory_round_material',
                          'memory_summaries','memory_reconstruction_checkpoints','memory_message_sources'):
                if table in tables and db.execute('SELECT 1 FROM '+table+' WHERE scope=? LIMIT 1',(scope,)).fetchone():
                    raise ValueError('owner_migration_source_requires_history_coverage:'+table)
            if 'pipeline_work_inputs' in tables and db.execute(
                    "SELECT 1 FROM pipeline_work_inputs WHERE scope=? AND status='owned' LIMIT 1", (scope,)).fetchone():
                raise ValueError('owner_migration_source_input_still_owned')
            sources.append({'scope':scope,'sha':sha,'version':version,'updated':updated,
                            'baseMessages':len(snapshot['base']),'wireMessages':len(snapshot['wire']),
                            'coverage':'system_only','state':'retired'})
            if db.execute('DELETE FROM fishbot_readonly_journals WHERE scope=? AND sha=?', (key, sha)).rowcount != 1:
                raise ValueError('owner_migration_source_changed')
            if 'fishbot_readonly_cutover' in tables:
                db.execute("UPDATE fishbot_readonly_cutover SET state='consumed' WHERE scope=? AND state='available'", (key,))
        receipt = {'grant':grant,'backupSha':backup_sha,'destinationSha':destination_sha,'sources':sources}
        encoded = json.dumps(receipt,ensure_ascii=False,separators=(',',':'))
        db.execute('INSERT INTO fishbot_journal_owner_migrations VALUES(?,?,?,?)',
                   (grant['id'],encoded,hashlib.sha256(encoded.encode()).hexdigest(),time.time()))
        db.commit()
        return receipt
    except BaseException:
        db.rollback()
        raise


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
    no summary coverage, 30-round permit or external receipt is manufactured.
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
        _operator_archives(db)
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
