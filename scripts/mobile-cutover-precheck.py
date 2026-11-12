"""Compare final, frozen migration inventories; NEVER perform a cutover.

Usage: python scripts/mobile-cutover-precheck.py SOURCE.json TARGET.json

Manifest contract (schemaVersion=1):
  baseline: {codeCommit: 40-char Git SHA, databaseSchema: SHA256,
             configSchema: SHA256}
  evidence: {method: "stopped-read-only", snapshotId: nonempty string,
             observedAt: timezone-qualified ISO8601 timestamp}
  state: {consumptionStopped: bool, executionAuthorityEnabled: bool,
          allQQAIProcessesExited: bool, routeGateEnabled: bool}
  bots: {opaqueBotId: {
    sqlite: {logicalDatabaseName: {sha256: SHA256, integrityCheck: "ok"}},
    journals: {originalLogicalScope: SHA256},
    ledgers: {unknown: {count: int, sha256: SHA256},
              partial: {count: int, sha256: SHA256}},
    references: [logicalFileId, ...]}}
  files: {logicalFileId: SHA256}
  deletionPolicy: "no-age-only-deletion"

Both inventories MUST be obtained from actual read-only inspection after the
source stops consuming and all source QQ/AI processes exit. observedAt must be
after that freeze. Before reading SQLite, capture committed data together with
any required WAL through a consistent SQLite snapshot/backup; integrityCheck
must be the complete PRAGMA integrity_check result, not a sample or old report.
Journal digests cover the original persisted bytes for EVERY logical scope.
Ledger count/digests cover complete unknown and partial rows (including ownership
and receipt identity); file inventories hash every referenced payload, including
media, with a complete reference traversal. Target inventories must be freshly
read from the final copied data, not copied from the source manifest. snapshotId
identifies that same final frozen source snapshot on both sides.

This program only compares supplied assertions. It cannot establish that a
manifest is truthful, complete, fresh, bound to a host, or that a process stayed
stopped. A ready result is NOT authority to execute, reopen consumption, start
QQ, send AI requests, write an authority file, change routing, or delete anything.
An operator must independently verify the freeze, evidence provenance, final
incremental transfer, live gates, and rollback before a separate cutover action.
No age/mtime decision grants deletion permission. No state is written here.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re


class PrecheckError(ValueError):
    """Only stable error codes; do not echo identities or manifest content."""


def require(condition, code):
    if not condition:
        raise PrecheckError(code)


def digest(value, length=64):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{'+str(length)+'}', value) is not None


def mapping(value, code, nonempty=False):
    require(isinstance(value, dict) and (not nonempty or bool(value)), code)
    require(all(isinstance(k, str) and k.strip() for k in value), code)
    return value


def validate(manifest):
    m = mapping(manifest, 'invalid_manifest')
    require(type(m.get('schemaVersion')) is int and m['schemaVersion'] == 1, 'unsupported_manifest_schema')
    b = mapping(m.get('baseline'), 'invalid_baseline')
    require(set(b) == {'codeCommit', 'databaseSchema', 'configSchema'}, 'invalid_baseline')
    require(digest(b['codeCommit'], 40) and digest(b['databaseSchema']) and digest(b['configSchema']), 'invalid_baseline')
    e = mapping(m.get('evidence'), 'missing_freeze_evidence')
    require(e.get('method') == 'stopped-read-only' and isinstance(e.get('snapshotId'), str) and bool(e['snapshotId'].strip()), 'missing_freeze_evidence')
    try:
        at = datetime.fromisoformat(e['observedAt'].replace('Z', '+00:00'))
        require(at.tzinfo is not None and at.utcoffset() is not None, 'invalid_observation_time')
    except (KeyError, AttributeError, TypeError, ValueError):
        raise PrecheckError('invalid_observation_time') from None
    s = mapping(m.get('state'), 'missing_process_gate_evidence')
    for key in ('consumptionStopped', 'executionAuthorityEnabled', 'allQQAIProcessesExited', 'routeGateEnabled'):
        require(type(s.get(key)) is bool, 'missing_process_gate_evidence')
    require(m.get('deletionPolicy') == 'no-age-only-deletion', 'unsafe_deletion_policy')
    files = mapping(m.get('files'), 'invalid_file_inventory')
    require(all(digest(v) for v in files.values()), 'invalid_file_inventory')
    bots = mapping(m.get('bots'), 'missing_bot_inventory', nonempty=True)
    for bot in bots.values():
        bot = mapping(bot, 'invalid_bot_inventory')
        dbs = mapping(bot.get('sqlite'), 'missing_sqlite_inventory', nonempty=True)
        for db in dbs.values():
            db = mapping(db, 'invalid_sqlite_inventory')
            require(db.get('integrityCheck') == 'ok' and digest(db.get('sha256')), 'sqlite_integrity_unproven')
        journals = mapping(bot.get('journals'), 'missing_journal_scopes', nonempty=True)
        require(all(digest(v) for v in journals.values()), 'invalid_journal_digest')
        ledgers = mapping(bot.get('ledgers'), 'missing_ledger_inventory')
        require(set(ledgers) == {'unknown', 'partial'}, 'missing_ledger_inventory')
        for ledger in ledgers.values():
            ledger = mapping(ledger, 'invalid_ledger_inventory')
            require(set(ledger) == {'count', 'sha256'} and type(ledger.get('count')) is int and ledger['count'] >= 0 and digest(ledger.get('sha256')), 'invalid_ledger_inventory')
        refs = bot.get('references')
        require(isinstance(refs, list) and all(isinstance(v, str) and v.strip() for v in refs), 'invalid_reference_inventory')
        require(len(refs) == len(set(refs)), 'duplicate_reference')
        require(set(refs) <= set(files), 'referenced_file_missing')
    return m


def precheck(source, target):
    source, target = validate(source), validate(target)
    require(source['baseline'] == target['baseline'], 'baseline_or_schema_mismatch')
    require(source['evidence']['snapshotId'] == target['evidence']['snapshotId'], 'frozen_snapshot_mismatch')
    for manifest in (source, target):
        state = manifest['state']
        require(state['consumptionStopped'] and state['allQQAIProcessesExited'], 'consumer_or_process_still_active')
        require(not state['executionAuthorityEnabled'] and not state['routeGateEnabled'], 'execution_or_route_gate_open')
    require(set(source['bots']) == set(target['bots']), 'bot_inventory_mismatch')
    for name, bot in source['bots'].items():
        other = target['bots'][name]
        require(bot['sqlite'] == other['sqlite'], 'sqlite_snapshot_mismatch')
        require(bot['journals'] == other['journals'], 'journal_scope_or_digest_mismatch')
        require(bot['ledgers'] == other['ledgers'], 'unknown_or_partial_ledger_mismatch')
        require(set(bot['references']) == set(other['references']), 'reference_inventory_mismatch')
        require(all(source['files'][f] == target['files'][f] for f in bot['references']), 'referenced_file_digest_mismatch')
    return {'ready': True, 'checksOnly': True, 'executionAuthorized': False,
            'scope': 'supplied_frozen_manifest_consistency_only',
            'requiresIndependentEvidence': True,
            'limitation': 'Manifest assertions do not prove live freeze, completeness, integrity, or permission to cut over.'}


def read_manifest(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate_manifest_key')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=unique_pairs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source_manifest')
    parser.add_argument('target_manifest')
    args = parser.parse_args(argv)
    try:
        result = precheck(read_manifest(args.source_manifest), read_manifest(args.target_manifest))
    except (OSError, UnicodeError, json.JSONDecodeError):
        result = {'ready': False, 'checksOnly': True, 'executionAuthorized': False, 'error': 'manifest_read_failed'}
    except PrecheckError as error:
        result = {'ready': False, 'checksOnly': True, 'executionAuthorized': False, 'error': str(error)}
    print(json.dumps(result, separators=(',', ':')))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
