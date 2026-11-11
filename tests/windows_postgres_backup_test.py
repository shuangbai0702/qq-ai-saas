"""Native backup adapter boundaries; no live database or task registration."""
import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_pg_backup', ROOT/'scripts/postgres-backup.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def native_fixture(root):
    binaries = root/'postgres'/'bin'; binaries.mkdir(parents=True)
    for name in ('pg_dump','pg_restore','createdb','dropdb','psql'):
        (binaries/(name+('.exe' if os.name == 'nt' else ''))).touch()
    password = root/'password'; password.write_text('private-fixture-password\n')
    config = root/'private.json'; config.write_text(json.dumps(dict(postgresBin=str(binaries), environment=dict(PGHOST='127.0.0.1',PGPORT='5432',PGUSER='fishbot',PGDATABASE='fishbot_live',PGPASSWORD_FILE=str(password)))))
    return backup.NativePostgres(config), config


class NativeBackup(unittest.TestCase):
    def test_native_dump_header_restore_isolation_and_no_credentials_in_args_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {'PGOPTIONS':'untrusted-inherited-setting','PGPASSWORD':'wrong'}):
                native, _ = native_fixture(root)
            calls = []
            def command(args, **kwargs):
                calls.append((args, kwargs))
                self.assertNotIn('private-fixture-password', repr(args))
                self.assertNotIn('PGOPTIONS', kwargs['env'])
                self.assertEqual(kwargs['env']['PGPASSWORD'], 'private-fixture-password')
                name = Path(args[0]).stem
                if name == 'pg_dump': kwargs['stdout'].write(b'PGDMP-fixture')
                return SimpleNamespace(stdout='19\n' if name == 'psql' else '')
            output = io.StringIO()
            with patch.object(backup, 'command', side_effect=command), contextlib.redirect_stdout(output):
                result = backup.backup(root/'backups', None, native.database, native.user, 7, True, native)
            self.assertTrue(result['restoreCheck']['ok']); self.assertEqual(result['restoreCheck']['databaseTables'],19)
            names = [Path(args[0]).stem for args, _ in calls]
            self.assertEqual(names, ['pg_dump','pg_restore','createdb','pg_restore','psql','dropdb'])
            created = calls[2][0][-1]; self.assertTrue(created.startswith('fishbot_backup_check_'))
            self.assertNotEqual(created, native.database); self.assertEqual(calls[-1][0][-1], created)
            self.assertNotIn('private-fixture-password', output.getvalue())
            self.assertEqual(len(list((root/'backups').glob('*.dump'))),1)
            self.assertEqual(len(list((root/'backups').glob('*.json'))),1)
            self.assertEqual(list((root/'backups').glob('*.partial')),[])

    def test_restore_failure_drops_only_its_successfully_created_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); native, _ = native_fixture(root); dump=root/'snapshot';dump.write_bytes(b'archive');calls=[]
            def command(args, **kwargs):
                calls.append(args)
                if Path(args[0]).stem == 'pg_restore':raise subprocess.CalledProcessError(1,args)
                return SimpleNamespace(stdout='19')
            with patch.object(backup,'command',side_effect=command):
                with self.assertRaises(subprocess.CalledProcessError):native.restore_check(dump)
            self.assertEqual(Path(calls[-1][0]).stem,'dropdb');self.assertEqual(calls[-1][-1],calls[0][-1]);self.assertNotEqual(calls[-1][-1],native.database)
            calls.clear()
            with patch.object(backup,'command',side_effect=subprocess.CalledProcessError(1,['createdb'])) as run:
                with self.assertRaises(subprocess.CalledProcessError):native.restore_check(dump)
                self.assertEqual(run.call_count,1,'failed createdb never authorizes dropping a database')

    def test_cli_failure_never_echoes_password_config_or_raw_subprocess_details(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);_,config=native_fixture(root)
            result=subprocess.run([__import__('sys').executable,str(ROOT/'scripts/postgres-backup.py'),'--config',str(config),'--directory',str(root/'backups'),'--restore-check'],capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0);self.assertNotIn('private-fixture-password',result.stdout+result.stderr);self.assertNotIn('PGPASSWORD',result.stdout+result.stderr);self.assertIn('PostgreSQL backup failed:',result.stdout);self.assertEqual(list((root/'backups').glob('*.partial')),[]);self.assertEqual(list((root/'backups').glob('*.dump')),[])

    def test_real_platform_lock_rejects_a_concurrent_manual_or_timer_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with backup.backup_lock(root):
                with self.assertRaises(OSError):
                    with backup.backup_lock(root):pass

    def test_existing_partial_and_modified_or_foreign_pairs_are_never_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);native,_=native_fixture(root);target=root/'backups';target.mkdir()
            fixed=datetime(2026,11,10,4,15,tzinfo=timezone.utc);stamp=fixed.strftime('%Y%m%dT%H%M%S%fZ');partial=target/('fishbot-pg-'+stamp+'.partial');partial.write_bytes(b'user-existing')
            with patch.object(backup,'datetime') as clock:
                clock.now.return_value=fixed
                with self.assertRaises(FileExistsError):backup.backup(target,None,native.database,native.user,7,True,native)
            self.assertEqual(partial.read_bytes(),b'user-existing')
            for i in range(9):
                name='20261110T041500'+str(i).zfill(6)+'Z';dump=target/('fishbot-pg-'+name+'.dump');dump.write_bytes(b'owned');digest=__import__('hashlib').sha256(b'owned').hexdigest()
                (target/('fishbot-pg-'+name+'.json')).write_text(json.dumps(dict(format=backup.FORMAT,createdAt=name,bytes=5,sha256=digest)))
            (target/'fishbot-pg-20261110T041500000000Z.dump').write_bytes(b'other')
            backup.prune(target,7)
            self.assertEqual((target/'fishbot-pg-20261110T041500000000Z.dump').read_bytes(),b'other')
            self.assertFalse((target/'fishbot-pg-20261110T041500000001Z.dump').exists())
            with self.assertRaises(ValueError):backup.bounded_child(target.resolve(),'../outside.dump')

    @unittest.skipUnless(os.name=='nt','Windows task planning only')
    def test_installer_plan_has_required_schedule_limited_identity_and_no_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);_,config=native_fixture(root);value=json.loads(config.read_text());value.update(sourceRoot=str(ROOT),python=__import__('sys').executable);config.write_text(json.dumps(value));target=root/'backups'
            result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/install-windows-postgres-backup-task.ps1'),'-Config',str(config),'-Directory',str(target),'-PlanOnly'],capture_output=True,text=True,encoding='utf-8',timeout=20,check=True)
            plan=json.loads(result.stdout);self.assertEqual((plan['sid'],plan['runLevel'],plan['dailyAt'],plan['randomDelaySeconds']),('S-1-5-20','Limited','04:15',900));self.assertTrue(plan['startWhenAvailable']);self.assertTrue(plan['restoreCheck']);self.assertEqual(plan['retention'],7);self.assertFalse(target.exists());self.assertNotIn('private-fixture-password',result.stdout)


if __name__=='__main__':unittest.main()
