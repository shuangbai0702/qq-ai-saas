"""Apply one archived, System-only owner migration under an existing worker fence."""

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from uuid import UUID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='Production Bot runtime data root')
    parser.add_argument('--grant', type=Path, required=True, help='Protected operator authorization manifest for one Bot')
    parser.add_argument('--archive', type=Path, required=True, help='Verified, read-only SQLite backup of that Bot')
    args = parser.parse_args()
    grant = json.loads(args.grant.read_text(encoding='utf-8-sig'))
    bot = str(UUID(grant['bot']))
    parent = (args.root / 'bots').resolve(strict=True)
    database = (parent / bot / 'behavior.sqlite').resolve(strict=True)
    if database != parent / bot / 'behavior.sqlite' or grant['bot'] != bot:
        raise ValueError('owner_migration_database_identity_changed')
    archive = args.archive.resolve(strict=True)
    if archive.name != database.name or archive.parent.name != bot or archive.is_relative_to(parent):
        raise ValueError('owner_migration_archive_identity_changed')
    if not archive.is_file() or archive.with_name(archive.name+'-wal').exists():
        raise ValueError('owner_migration_archive_requires_standalone_snapshot')
    with archive.open('rb') as stream:
        backup_sha = hashlib.file_digest(stream, 'sha256').hexdigest()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters' / 'astrbot'))
    from journal_cutover import retire_empty_legacy_journals
    from journal_storage import read_snapshot_payload

    with closing(sqlite3.connect(archive.as_uri()+'?immutable=1', uri=True)) as saved:
        if saved.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('owner_migration_backup_integrity')
        inventory = {}
        for key, version, payload, sha in saved.execute('SELECT scope,version,payload,sha FROM fishbot_readonly_journals'):
            read_snapshot_payload(saved, key, version, payload, sha)
            inventory[json.loads(key)] = sha
        if inventory.pop('bot_shared', None) is None or inventory != grant['sources']:
            raise ValueError('owner_migration_backup_scope_inventory_changed')
    with closing(sqlite3.connect(database.as_uri()+'?mode=rw', uri=True, timeout=10)) as live:
        print(json.dumps(retire_empty_legacy_journals(live, bot, grant, backup_sha), ensure_ascii=True))


if __name__ == '__main__':
    main()
