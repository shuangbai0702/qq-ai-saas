"""Persistent, content-free guard for the complete model-bound System vector.

The contract is bot-private and stored in the existing behavior SQLite file.
Baselines are per conversation scope: each scope's committed Journal wire
carries its own System vector, and one scope adopting a new System at its own
licensed 35-round reconstruction must never block another scope that still
runs the previously committed vector. A trusted config-save audit token may
authorize one successful transition per scope; failed requests release that
token reservation for that scope only.
"""
import hashlib
import json
import os
import uuid

def request_identity():
    import psutil
    return f'{os.getpid()}@{psutil.Process().create_time()}@{uuid.uuid4()}'

def abandoned_request(value):
    import psutil
    try:
        pid,born,_=value.split('@',2)
        return abs(psutil.Process(int(pid)).create_time()-float(born))>.01
    except psutil.NoSuchProcess:return True
    except (ValueError,TypeError,AttributeError,psutil.AccessDenied):return False


class SystemContractViolation(RuntimeError):
    """The final request contains an unauthorized System change."""


def system_vector_sha(messages):
    if not isinstance(messages, list):
        raise SystemContractViolation('system_vector_missing')
    vector = []
    for index, message in enumerate(messages):
        if isinstance(message, dict) and message.get('role') == 'system':
            vector.append([index, message])
    encoded = json.dumps(vector, ensure_ascii=False, separators=(',', ':'), sort_keys=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


class SystemContract:
    def __init__(self, db):
        self.db = db
        db.executescript('''
            CREATE TABLE IF NOT EXISTS fishbot_system_contract (
                id INTEGER PRIMARY KEY CHECK(id=1),
                system_sha TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fishbot_system_scope_contract (
                scope TEXT PRIMARY KEY,
                system_sha TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
        ''')
        # Migration: permits were once bot-global. They are now per scope so
        # each conversation consumes its own trusted transition.
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in db.execute('PRAGMA table_info(fishbot_system_permits)')} if 'fishbot_system_permits' in tables else set()
        if not columns:
            db.executescript('''
                CREATE TABLE fishbot_system_permits (
                    permit_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    previous_sha TEXT NOT NULL,
                    target_sha TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('reserved','consumed')),
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(permit_id,scope)
                );
            ''')
        elif 'scope' in columns:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS fishbot_system_permits (
                    permit_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    previous_sha TEXT NOT NULL,
                    target_sha TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('reserved','consumed')),
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(permit_id,scope)
                );
            ''')
        else:
            db.executescript('''
                ALTER TABLE fishbot_system_permits RENAME TO fishbot_system_permits_legacy;
                CREATE TABLE fishbot_system_permits (
                    permit_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    previous_sha TEXT NOT NULL,
                    target_sha TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('reserved','consumed')),
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(permit_id,scope)
                );
                INSERT INTO fishbot_system_permits
                    SELECT permit_id,'',request_id,previous_sha,target_sha,state,updated_at
                    FROM fishbot_system_permits_legacy;
                DROP TABLE fishbot_system_permits_legacy;
            ''')
        db.commit()

    def reserve(self, messages, permit_id, request_id, now, scope=''):
        """Check the exact final wire vector and reserve a trusted save token.

        ``scope`` is the conversation scope of this request (empty for legacy
        callers). A scope with no recorded baseline bootstraps from its own
        wire; that is the same trust level as the former bot-global bootstrap
        because only platform-configured system prompts reach this boundary.
        """
        target = system_vector_sha(messages)
        permit = str(permit_id) if permit_id not in (None, '') else None
        scope = str(scope or '')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for old_permit,owner in self.db.execute("SELECT permit_id,request_id FROM fishbot_system_permits WHERE state='reserved' AND scope=?",(scope,)).fetchall():
                if abandoned_request(owner):self.db.execute("DELETE FROM fishbot_system_permits WHERE permit_id=? AND scope=? AND state='reserved'",(old_permit,scope))
            row = self.db.execute('SELECT system_sha FROM fishbot_system_scope_contract WHERE scope=?',(scope,)).fetchone()
            if row is None:
                self.db.execute('INSERT INTO fishbot_system_scope_contract VALUES(?,?,?)', (scope, target, now))
                self.db.commit()
                return {'systemSha': target, 'permitId': None, 'requestId': request_id, 'changed': False, 'bootstrap': True, 'scope': scope}
            previous = row[0]
            # A second token cannot authorize a conflicting in-flight vector in
            # the same scope. Other scopes transition independently.
            transition=self.db.execute("SELECT target_sha FROM fishbot_system_permits WHERE state='reserved' AND scope=? AND previous_sha<>target_sha AND request_id<>? LIMIT 1",(scope,request_id)).fetchone()
            if transition and transition[0]!=target:
                raise SystemContractViolation('system_change_in_flight')
            token = self.db.execute('SELECT request_id,previous_sha,target_sha,state FROM fishbot_system_permits WHERE permit_id=? AND scope=?', (permit,scope)).fetchone() if permit else None
            if previous == target and permit is None:
                self.db.commit()
                return {'systemSha': target, 'permitId': None, 'requestId': request_id, 'changed': False, 'bootstrap': False, 'scope': scope}
            if token and token[3] == 'consumed':
                if previous != target:
                    raise SystemContractViolation('system_change_permit_consumed')
                self.db.commit()
                return {'systemSha': target, 'permitId': None, 'requestId': request_id, 'changed': False, 'bootstrap': False, 'scope': scope}
            if previous != target and permit is None:
                raise SystemContractViolation('system_change_without_trusted_permit')
            if token:
                if token[0] != request_id:
                    if previous != target:
                        raise SystemContractViolation('system_change_permit_reserved')
                    self.db.commit()
                    return {'systemSha': target, 'permitId': None, 'requestId': request_id, 'changed': False, 'bootstrap': False, 'scope': scope}
                if token[1] != previous or token[2] != target:
                    raise SystemContractViolation('system_change_candidate_mismatch')
            else:
                self.db.execute('INSERT INTO fishbot_system_permits VALUES(?,?,?,?,?,?,?)',
                                (permit, scope, request_id, previous, target, 'reserved', now))
            self.db.commit()
            return {'systemSha': target, 'permitId': permit, 'requestId': request_id,
                    'previousSha': previous, 'changed': previous != target, 'bootstrap': False, 'scope': scope}
        except BaseException:
            self.db.rollback()
            raise

    def settle(self, reservation, success, now):
        if not reservation or not reservation.get('permitId'):
            return True
        permit = reservation['permitId'];request_id = reservation['requestId']
        scope = str(reservation.get('scope') or '')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT request_id,previous_sha,target_sha,state FROM fishbot_system_permits WHERE permit_id=? AND scope=?', (permit,scope)).fetchone()
            if row is None or row[0] != request_id or row[3] != 'reserved':
                self.db.rollback()
                return False
            if success:
                current = self.db.execute('SELECT system_sha FROM fishbot_system_scope_contract WHERE scope=?',(scope,)).fetchone()
                if current is None or current[0] != row[1]:
                    if current is None or current[0] != row[2]:
                        raise SystemContractViolation('system_contract_changed_during_request')
                else:
                    self.db.execute('UPDATE fishbot_system_scope_contract SET system_sha=?,updated_at=? WHERE scope=? AND system_sha=?',
                                    (row[2], now, scope, row[1]))
                self.db.execute("UPDATE fishbot_system_permits SET state='consumed',updated_at=? WHERE permit_id=? AND scope=? AND request_id=?", (now, permit, scope, request_id))
            else:
                # Failure/cancellation aborts only this scope's reservation. The
                # authenticated audit token remains available for that scope's
                # deliberate retry.
                self.db.execute("DELETE FROM fishbot_system_permits WHERE permit_id=? AND scope=? AND request_id=? AND state='reserved'", (permit, scope, request_id))
            self.db.commit()
            return True
        except BaseException:
            self.db.rollback()
            raise
