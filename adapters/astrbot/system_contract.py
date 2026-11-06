"""Persistent, content-free guard for the complete model-bound System vector.

The contract is bot-private and stored in the existing behavior SQLite file.
It has no scope/phase/work key, so changing a journal key cannot bootstrap a
different System. A trusted config-save audit token may authorize one
successful transition; failed requests release that token reservation.
"""
import hashlib
import json


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
            CREATE TABLE IF NOT EXISTS fishbot_system_permits (
                permit_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                previous_sha TEXT NOT NULL,
                target_sha TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('reserved','consumed')),
                updated_at REAL NOT NULL
            );
        ''')

    def reserve(self, messages, permit_id, request_id, now):
        """Check the exact final wire vector and reserve a trusted save token."""
        target = system_vector_sha(messages)
        permit = str(permit_id) if permit_id not in (None, '') else None
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT system_sha FROM fishbot_system_contract WHERE id=1').fetchone()
            if row is None:
                self.db.execute('INSERT INTO fishbot_system_contract VALUES(1,?,?)', (target, now))
                self.db.commit()
                return {'systemSha': target, 'permitId': None, 'requestId': request_id, 'changed': False, 'bootstrap': True}
            previous = row[0]
            token = self.db.execute('SELECT request_id,previous_sha,target_sha,state FROM fishbot_system_permits WHERE permit_id=?', (permit,)).fetchone() if permit else None
            if previous == target and permit is None:
                self.db.commit()
                return {'systemSha': target, 'permitId': None, 'requestId': request_id, 'changed': False, 'bootstrap': False}
            if token and token[3] == 'consumed':
                if previous != target:
                    raise SystemContractViolation('system_change_permit_consumed')
                self.db.commit()
                return {'systemSha': target, 'permitId': None, 'requestId': request_id, 'changed': False, 'bootstrap': False}
            if previous != target and permit is None:
                raise SystemContractViolation('system_change_without_trusted_permit')
            if token:
                if token[0] != request_id:
                    if previous != target:
                        raise SystemContractViolation('system_change_permit_reserved')
                    self.db.commit()
                    return {'systemSha': target, 'permitId': None, 'requestId': request_id, 'changed': False, 'bootstrap': False}
                if token[1] != previous or token[2] != target:
                    raise SystemContractViolation('system_change_candidate_mismatch')
            else:
                self.db.execute('INSERT INTO fishbot_system_permits VALUES(?,?,?,?,?,?)',
                                (permit, request_id, previous, target, 'reserved', now))
            self.db.commit()
            return {'systemSha': target, 'permitId': permit, 'requestId': request_id,
                    'previousSha': previous, 'changed': previous != target, 'bootstrap': False}
        except BaseException:
            self.db.rollback()
            raise

    def settle(self, reservation, success, now):
        if not reservation or not reservation.get('permitId'):
            return True
        permit = reservation['permitId'];request_id = reservation['requestId']
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT request_id,previous_sha,target_sha,state FROM fishbot_system_permits WHERE permit_id=?', (permit,)).fetchone()
            if row is None or row[0] != request_id or row[3] != 'reserved':
                self.db.rollback()
                return False
            if success:
                current = self.db.execute('SELECT system_sha FROM fishbot_system_contract WHERE id=1').fetchone()
                if current is None or current[0] != row[1]:
                    if current is None or current[0] != row[2]:
                        raise SystemContractViolation('system_contract_changed_during_request')
                else:
                    self.db.execute('UPDATE fishbot_system_contract SET system_sha=?,updated_at=? WHERE id=1 AND system_sha=?',
                                    (row[2], now, row[1]))
                self.db.execute("UPDATE fishbot_system_permits SET state='consumed',updated_at=? WHERE permit_id=? AND request_id=?", (now, permit, request_id))
            else:
                # Failure/cancellation aborts only this reservation. The
                # authenticated audit token remains available for a deliberate retry.
                self.db.execute('DELETE FROM fishbot_system_permits WHERE permit_id=? AND request_id=? AND state=\'reserved\'', (permit, request_id))
            self.db.commit()
            return True
        except BaseException:
            self.db.rollback()
            raise
