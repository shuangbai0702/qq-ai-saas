"""Host-side PostgreSQL dump, optional isolated restore check, bounded retention.

Run as root on the Docker host. Does not stop API/Agent or touch the live DB.
The encryption key and QQ/AI runtime files require separate protected backups.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid


def command(args, **kwargs):
    return subprocess.run(args, check=True, timeout=300, **kwargs)


def restore_check(dump, container, user):
    database = 'fishbot_backup_check_' + uuid.uuid4().hex
    prefix = ['docker', 'exec', container]
    command(prefix + ['createdb', '-U', user, '-T', 'template0', database], capture_output=True)
    try:
        with dump.open('rb') as source:
            command(['docker', 'exec', '-i', container, 'pg_restore', '-U', user,
                     '--exit-on-error', '--no-owner', '--no-privileges', '-d', database],
                    stdin=source, capture_output=True)
        result = command(prefix + ['psql', '-X', '-U', user, '-d', database, '-Atc',
                         "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'"],
                         capture_output=True, text=True)
        count = int(result.stdout.strip())
        if count < 16:
            raise RuntimeError('Restored database is missing application tables')
        return {'databaseTables': count, 'ok': True}
    finally:
        command(prefix + ['dropdb', '-U', user, database], capture_output=True)


def prune(root, keep):
    # Only completed backup pairs created by this script are eligible.
    completed = sorted(root.glob('fishbot-pg-*.json'), reverse=True)
    for manifest in completed[keep:]:
        dump = manifest.with_suffix('.dump')
        if dump.is_file():
            dump.unlink()
            manifest.unlink()


def backup(root, container, database, user, keep, verify):
    if keep < 1:
        raise ValueError('Retention must keep at least one backup')
    os.umask(0o077)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Serialize both timer and manual runs before dumping or pruning.
    import fcntl
    with (root / '.backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        dump = root / ('fishbot-pg-' + stamp + '.dump')
        partial = dump.with_suffix('.partial')
        try:
            with partial.open('xb') as output:
                command(['docker', 'exec', container, 'pg_dump', '-U', user,
                         '-d', database, '--format=custom'], stdout=output, stderr=subprocess.PIPE)
                output.flush()
                os.fsync(output.fileno())
            # Reject incomplete/corrupt archive headers before publishing it.
            with partial.open('rb') as source:
                command(['docker', 'exec', '-i', container, 'pg_restore', '--list'],
                        stdin=source, capture_output=True)
            checked = restore_check(partial, container, user) if verify else None
            with partial.open('rb') as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            partial.replace(dump)
            manifest = {'createdAt': stamp, 'database': database, 'bytes': dump.stat().st_size,
                        'sha256': digest, 'restoreCheck': checked,
                        'scope': 'PostgreSQL only; master.key and runtime files are not included'}
            metadata = dump.with_suffix('.json')
            temp = metadata.with_suffix('.json.partial')
            with temp.open('x') as output:
                json.dump(manifest, output, indent=2)
                output.flush()
                os.fsync(output.fileno())
            temp.replace(metadata)
            prune(root, keep)
            print(json.dumps(manifest), flush=True)
            return manifest
        finally:
            partial.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--container', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--keep', type=int, default=7)
    parser.add_argument('--restore-check', action='store_true')
    args = parser.parse_args()
    backup(args.directory, args.container, args.database, args.user, args.keep, args.restore_check)
