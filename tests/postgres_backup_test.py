"""Backup failure boundaries and retention; real restore is checked on Linux."""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('pg_backup', Path(__file__).resolve().parents[1] / 'scripts/postgres-backup.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupTest(unittest.TestCase):
    def test_retention_preserves_unpaired_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for stamp in ['001', '002', '003']:
                stamp = '20261110T041500' + stamp.zfill(6) + 'Z'
                (root / ('fishbot-pg-' + stamp + '.dump')).write_bytes(b'backup')
                (root / ('fishbot-pg-' + stamp + '.json')).write_text(json.dumps(dict(format=backup.FORMAT, createdAt=stamp, bytes=6, sha256=hashlib.sha256(b'backup').hexdigest())))
            (root / 'fishbot-pg-000.dump').write_text('unpaired')
            (root / 'manual.dump').write_text('manual')
            (root / 'fishbot-pg-20261109T041500000000Z.dump').write_text('foreign')
            (root / 'fishbot-pg-20261109T041500000000Z.json').write_text('{}')
            legacy='20261108T041500000000Z'
            (root / ('fishbot-pg-'+legacy+'.dump')).write_bytes(b'backup')
            (root / ('fishbot-pg-'+legacy+'.json')).write_text(json.dumps(dict(createdAt=legacy,database='live',bytes=6,sha256=hashlib.sha256(b'backup').hexdigest(),restoreCheck=None,scope=backup.SCOPE)))
            backup.prune(root, 2)
            self.assertFalse((root / 'fishbot-pg-20261110T041500000001Z.dump').exists())
            self.assertTrue((root / 'fishbot-pg-20261110T041500000002Z.dump').exists())
            self.assertTrue((root / 'fishbot-pg-000.dump').exists())
            self.assertTrue((root / 'manual.dump').exists())
            self.assertTrue((root / 'fishbot-pg-20261109T041500000000Z.dump').exists())
            self.assertFalse((root / ('fishbot-pg-'+legacy+'.dump')).exists(),'valid previous Docker-script metadata remains covered by retention')

    def test_failed_restore_drops_only_new_verification_database(self):
        calls = []
        def command(args, **kwargs):
            calls.append(args)
            if 'pg_restore' in args:
                raise subprocess.CalledProcessError(1, args)
        with tempfile.TemporaryDirectory() as directory:
            dump = Path(directory) / 'snapshot.dump'
            dump.write_bytes(b'archive')
            with patch.object(backup, 'command', side_effect=command):
                with self.assertRaises(subprocess.CalledProcessError):
                    backup.restore_check(dump, 'pg', 'owner')
        created = calls[0][-1]
        self.assertTrue(created.startswith('fishbot_backup_check_'))
        self.assertEqual(calls[-1], ['docker', 'exec', 'pg', 'dropdb', '-U', 'owner', created])

    def test_failed_dump_does_not_publish_or_prune(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for suffix in ['.dump', '.json']:
                (root / ('fishbot-pg-old' + suffix)).write_text('keep')
            with patch.object(backup, 'command', side_effect=subprocess.CalledProcessError(1, ['pg_dump'])):
                with self.assertRaises(subprocess.CalledProcessError):
                    backup.backup(root, 'pg', 'live', 'owner', 1, True)
            self.assertEqual(sorted(p.name for p in root.iterdir()),
                             ['.backup.lock', 'fishbot-pg-old.dump', 'fishbot-pg-old.json'])


if __name__ == '__main__':
    unittest.main()
