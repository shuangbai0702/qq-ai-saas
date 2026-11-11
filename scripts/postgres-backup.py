"""PostgreSQL dump, optional isolated restore check, bounded owned retention.

Use Docker on Linux or protected native configuration on Windows. Does not stop
API/Agent or restore over the live DB.
The encryption key and QQ/AI runtime files require separate protected backups.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

FORMAT = 'fishbot.postgres-backup.v1'
NAME = re.compile(r'^fishbot-pg-(\d{8}T\d{12}Z)\.json$')
SCOPE = 'PostgreSQL only; master.key and runtime files are not included'


def private_json(file):
    file = Path(file)
    if not file.is_absolute() or file.is_symlink() or file.stat().st_size > 65536:
        raise ValueError('Invalid private configuration file')
    return json.loads(file.read_text(encoding='utf-8-sig'))


class NativePostgres:
    """Read existing private deployment config; passwords enter only child env."""
    def __init__(self, config):
        value = private_json(config)
        environment = value['environment']
        self.bin = Path(value['postgresBin'])
        password_file = Path(environment['PGPASSWORD_FILE'])
        if not self.bin.is_absolute() or not password_file.is_absolute() or password_file.is_symlink() or password_file.stat().st_size > 16384:
            raise ValueError('Invalid native PostgreSQL paths')
        self.database, self.user = environment['PGDATABASE'], environment['PGUSER']
        host, port = environment.get('PGHOST', '127.0.0.1'), str(environment.get('PGPORT', '5432'))
        if host != '127.0.0.1' or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError('Native backup requires private loopback PostgreSQL')
        if any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$-]{0,62}', v) for v in (self.database, self.user)):
            raise ValueError('Invalid native database identity')
        password = password_file.read_text(encoding='utf-8').rstrip('\r\n')
        if not password or '\0' in password:
            raise ValueError('Invalid private PostgreSQL password file')
        # Do not inherit PGSERVICE/PGOPTIONS or credentials from another target.
        self.env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PG')}
        self.env.update(PGHOST=host, PGPORT=port, PGUSER=self.user, PGPASSWORD=password, PGCONNECT_TIMEOUT='5')
        self.executables = {}
        for name in ('pg_dump', 'pg_restore', 'createdb', 'dropdb', 'psql'):
            executable = self.bin / (name + ('.exe' if os.name == 'nt' else ''))
            if not executable.is_file():
                raise ValueError('Native PostgreSQL utility is missing')
            self.executables[name] = str(executable)

    def run(self, executable, args, **kwargs):
        return command([self.executables[executable], *args], env=self.env,
                       creationflags=0x08000000 if os.name == 'nt' else 0, **kwargs)

    def restore_check(self, dump):
        database = 'fishbot_backup_check_' + uuid.uuid4().hex
        # Only a successfully created, invocation-owned database is dropped.
        self.run('createdb', ['-T', 'template0', '--', database], capture_output=True)
        try:
            with dump.open('rb') as source:
                self.run('pg_restore', ['--exit-on-error', '--no-owner', '--no-privileges', '-d', database], stdin=source, capture_output=True)
            result = self.run('psql', ['-X', '-d', database, '-Atc', "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'"], capture_output=True, text=True)
            count = int(result.stdout.strip())
            if count < 16:
                raise RuntimeError('Restored database is missing application tables')
            return {'databaseTables': count, 'ok': True}
        finally:
            self.run('dropdb', ['--', database], capture_output=True)


def bounded_child(root, name):
    file = root / name
    if file.is_symlink() or file.resolve().parent != root:
        raise ValueError('Backup path escaped its owned directory')
    return file


@contextmanager
def backup_lock(root):
    with bounded_child(root, '.backup.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            if lock.seek(0, 2) == 0:
                lock.write(b'0'); lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


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
    if keep < 1:
        raise ValueError('Retention must keep at least one backup')
    root = root.resolve()
    completed = []
    for manifest in sorted(root.glob('fishbot-pg-*.json'), reverse=True):
        match = NAME.fullmatch(manifest.name)
        if not match:
            continue
        try:
            bounded_child(root, manifest.name)
            dump = bounded_child(root, manifest.with_suffix('.dump').name)
            if manifest.stat().st_size > 16384 or not dump.is_file():
                continue
            value = json.loads(manifest.read_text(encoding='utf-8'))
            # The previous Docker script had no format marker. Recognize only
            # its exact metadata shape, scope and matching archive digest.
            legacy = set(value) == {'createdAt','database','bytes','sha256','restoreCheck','scope'} and value.get('scope') == SCOPE and isinstance(value.get('database'), str)
            if (value.get('format') != FORMAT and not legacy) or value.get('createdAt') != match[1] or value.get('bytes') != dump.stat().st_size or not re.fullmatch(r'[a-f0-9]{64}', value.get('sha256', '')):
                continue
            completed.append((manifest, dump, value['sha256']))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    for manifest, dump, digest in completed[keep:]:
        # A modified or manually substituted archive is no longer ours to prune.
        with dump.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                continue
        dump.unlink()
        manifest.unlink()


def backup(root, container, database, user, keep, verify, native=None):
    if keep < 1:
        raise ValueError('Retention must keep at least one backup')
    os.umask(0o077)
    if root.is_symlink():
        raise ValueError('Backup directory must not be a symlink')
    root = root.resolve()
    if root.parent == root:
        raise ValueError('Backup directory cannot be a filesystem root')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Serialize both timer and manual runs before dumping or pruning.
    with backup_lock(root):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        dump = bounded_child(root, 'fishbot-pg-' + stamp + '.dump')
        partial = bounded_child(root, dump.with_suffix('.partial').name)
        metadata = bounded_child(root, dump.with_suffix('.json').name)
        temp = bounded_child(root, metadata.with_suffix('.json.partial').name)
        if any(file.exists() for file in (dump, partial, metadata, temp)):
            raise FileExistsError('Backup destination already exists')
        partial_owned = temp_owned = published = completed = False
        try:
            with partial.open('xb') as output:
                partial_owned = True
                if native:
                    native.run('pg_dump', ['-d', database, '--format=custom'], stdout=output, stderr=subprocess.PIPE)
                else:
                    command(['docker', 'exec', container, 'pg_dump', '-U', user,
                             '-d', database, '--format=custom'], stdout=output, stderr=subprocess.PIPE)
                output.flush()
                os.fsync(output.fileno())
            # Reject incomplete/corrupt archive headers before publishing it.
            with partial.open('rb') as source:
                if native:
                    native.run('pg_restore', ['--list'], stdin=source, capture_output=True)
                else:
                    command(['docker', 'exec', '-i', container, 'pg_restore', '--list'],
                            stdin=source, capture_output=True)
            checked = (native.restore_check(partial) if native else restore_check(partial, container, user)) if verify else None
            with partial.open('rb') as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            partial.replace(dump)
            published = True
            manifest = {'format': FORMAT, 'createdAt': stamp, 'database': database, 'bytes': dump.stat().st_size,
                        'sha256': digest, 'restoreCheck': checked,
                        'scope': SCOPE}
            with temp.open('x') as output:
                temp_owned = True
                json.dump(manifest, output, indent=2)
                output.flush()
                os.fsync(output.fileno())
            temp.replace(metadata)
            completed = True
            prune(root, keep)
            print(json.dumps(manifest), flush=True)
            return manifest
        finally:
            if partial_owned:
                partial.unlink(missing_ok=True)
            if temp_owned:
                temp.unlink(missing_ok=True)
            if published and not completed:
                dump.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--config', type=Path, help='Protected native deployment JSON with postgresBin and environment.PGPASSWORD_FILE')
    parser.add_argument('--container')
    parser.add_argument('--database')
    parser.add_argument('--user')
    parser.add_argument('--keep', type=int, default=7)
    parser.add_argument('--restore-check', action='store_true')
    args = parser.parse_args()
    if args.config and any((args.container, args.database, args.user)):
        parser.error('--config cannot be combined with Docker connection arguments')
    if not args.config and not all((args.container, args.database, args.user)):
        parser.error('Docker backups require --container, --database and --user')
    try:
        native = NativePostgres(args.config) if args.config else None
        backup(args.directory, args.container, native.database if native else args.database,
               native.user if native else args.user, args.keep, args.restore_check, native)
    except Exception as error:
        # Never echo subprocess commands, raw stderr, private config or env.
        print('PostgreSQL backup failed: ' + type(error).__name__, flush=True)
        raise SystemExit(1)
