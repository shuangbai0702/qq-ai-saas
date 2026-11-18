"""Apply one validated owner migration while the owning worker is held."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from uuid import UUID

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--grant',type=Path,required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--product',type=Path,required=True)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--normalize-legacy-claims',action='store_true')
    args=parser.parse_args()
    grant=json.loads(args.grant.read_text(encoding='utf-8-sig'))
    bot=str(UUID(grant['bot']))
    parent=(args.root/'bots').resolve(strict=True)
    database=(parent/bot/'behavior.sqlite').resolve(strict=True)
    archive=args.archive.resolve(strict=True)
    if database!=parent/bot/'behavior.sqlite' or grant['bot']!=bot:
        raise ValueError('owner_migration_database_identity_changed')
    if archive.name!=database.name or archive.parent.name!=bot or archive.is_relative_to(parent):
        raise ValueError('owner_migration_archive_identity_changed')
    if archive.with_name(archive.name+'-wal').exists():
        raise ValueError('owner_migration_archive_requires_standalone_snapshot')
    backup_sha=hashlib.sha256(archive.read_bytes()).hexdigest()
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
    from journal_cutover import retire_owner_journals
    from journal_storage import read_snapshot_payload
    with closing(sqlite3.connect(archive.as_uri()+'?immutable=1',uri=True)) as saved:
        if saved.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
            raise ValueError('owner_migration_backup_integrity')
        inventory={}
        for scope,version,payload,sha in saved.execute("SELECT scope,version,payload,sha FROM fishbot_readonly_journals WHERE state='ready'"):
            read_snapshot_payload(saved,scope,version,payload,sha,scope_error='owner_migration_backup_scope')
            inventory[json.loads(scope)]=sha
        if inventory!=grant['sources']:raise ValueError('owner_migration_backup_inventory_changed')
    product=json.loads(args.product.read_text(encoding='utf-8-sig'))
    config=json.loads(args.config.read_text(encoding='utf-8-sig'))
    if config['bot']!=bot or product['bot']!=bot or product['backupSha']!=backup_sha:
        raise ValueError('owner_migration_product_bot_changed')
    with closing(sqlite3.connect(database.as_uri()+'?mode=rw',uri=True,timeout=10)) as live:
        receipt=retire_owner_journals(live,bot,grant,backup_sha,config['config'],product['product'],
                                      normalize_legacy_claims=args.normalize_legacy_claims)
        if live.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('owner_migration_post_commit_integrity')
        print(json.dumps(receipt,ensure_ascii=True))

if __name__=='__main__':main()
