"""Synthetic offline files only. No production data, SSH, or service changes."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/mobile-file-delta.py'
spec = importlib.util.spec_from_file_location('mobile_file_delta', SCRIPT)
delta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delta)


class FileDeltaTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'frozen'
        self.source.mkdir()
        (self.source/'bots').mkdir()
        (self.source/'bots/state.sqlite').write_bytes(b'opaque synthetic unknown/partial ledger bytes\x00')
        (self.source/'media.bin').write_bytes(b'\x00\x01media')
        self.target = self.root/'new-generation'
        self.manifest = {'schemaVersion': 1, 'snapshot': {'id': 'synthetic-final', 'frozen': True, 'method': 'stopped-read-only', 'observedAt': '2026-11-12T08:00:00Z'}, 'deletionPolicy': 'no-age-only-deletion', 'files': []}
        for path in ('bots/state.sqlite', 'media.bin'):
            raw = (self.source/path).read_bytes()
            self.manifest['files'].append({'path': path, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'necessary': True, 'reason': 'Preserve complete frozen ledger and referenced payload bytes'})

    def tearDown(self):
        self.temp.cleanup()

    def run_transfer(self, apply=False):
        return delta.transfer(self.source, self.target, self.manifest, apply)

    def test_default_plan_creates_nothing_and_apply_reuses_exact_bytes(self):
        before = {p: (self.source/p).read_bytes() for p in ('bots/state.sqlite', 'media.bin')}
        plan = self.run_transfer()
        self.assertEqual(plan['wouldCopy'], 2)
        self.assertFalse(plan['allTargetFilesVerified'])
        self.assertFalse(self.target.exists())
        applied = self.run_transfer(True)
        self.assertEqual(applied['copied'], 2)
        self.assertTrue(applied['allTargetFilesVerified'])
        self.assertFalse(applied['executionAuthorized'])
        self.assertFalse(applied['freezeProven'])
        self.assertFalse(applied['businessClosureProven'])
        self.assertEqual(before, {p: (self.target/p).read_bytes() for p in before})
        self.assertEqual(before, {p: (self.source/p).read_bytes() for p in before})
        with patch.object(delta, 'copy_cas', side_effect=AssertionError('matching files must be reused')):
            self.assertEqual(self.run_transfer(True)['reused'], 2)

    def test_hash_mismatch_preflight_does_not_create_generation(self):
        (self.source/'media.bin').write_bytes(b'corrupt')
        with self.assertRaisesRegex(delta.DeltaError, 'source_hash_mismatch'):
            self.run_transfer(True)
        self.assertFalse(self.target.exists())

    def test_unknown_directory_and_existing_bad_file_refused(self):
        self.target.mkdir()
        with self.assertRaisesRegex(delta.DeltaError, 'existing_unowned_generation'):
            self.run_transfer(True)
        self.target.rmdir()
        self.run_transfer(True)
        (self.target/'media.bin').write_bytes(b'wrong target bytes')
        with self.assertRaisesRegex(delta.DeltaError, 'existing_target_hash_mismatch'):
            self.run_transfer(True)
        self.assertEqual((self.target/'media.bin').read_bytes(), b'wrong target bytes')

    def test_unknown_entries_and_other_manifest_refused(self):
        self.run_transfer(True)
        (self.target/'unknown.bin').write_bytes(b'unknown')
        with self.assertRaisesRegex(delta.DeltaError, 'unexpected_generation_entry'):
            self.run_transfer(True)
        (self.target/'unknown.bin').unlink()
        self.manifest['snapshot']['id'] = 'different freeze'
        with self.assertRaisesRegex(delta.DeltaError, 'generation_manifest_mismatch'):
            self.run_transfer(True)

    def test_path_traversal_wildcards_drive_aliases_and_missing_reason_refused(self):
        original = copy.deepcopy(self.manifest)
        for path in ('../escape', '/absolute', 'bots/../escape', 'C:/x', 'bots\\x', 'bots/*.sqlite', 'bots/[ab]', 'NUL', 'x/./b', 'x//b', 'a:b', 'trail.', '.mobile-file-delta-other'):
            with self.subTest(path=path):
                self.manifest = copy.deepcopy(original)
                self.manifest['files'][0]['path'] = path
                with self.assertRaisesRegex(delta.DeltaError, 'unsafe_path'):
                    self.run_transfer(True)
        self.manifest = copy.deepcopy(original)
        self.manifest['files'][0]['reason'] = ''
        with self.assertRaisesRegex(delta.DeltaError, 'necessity_reason_required'):
            self.run_transfer(True)
        self.assertFalse(self.target.exists())

    def test_age_only_or_unfrozen_or_transform_fields_refused(self):
        for change in ('age', 'frozen', 'rewrite'):
            self.manifest = copy.deepcopy(self.manifest)
            if change == 'age':
                self.manifest['deletionPolicy'] = 'delete originals older than 30 days'
                code = 'unsafe_deletion_policy'
            elif change == 'frozen':
                self.manifest['deletionPolicy'] = 'no-age-only-deletion'
                self.manifest['snapshot']['frozen'] = False
                code = 'missing_frozen_evidence'
            else:
                self.manifest['snapshot']['frozen'] = True
                self.manifest['files'][0]['rewriteUnknownToSettled'] = True
                code = 'invalid_file_entry'
            with self.subTest(change=change), self.assertRaisesRegex(delta.DeltaError, code):
                self.run_transfer(True)
        self.assertFalse(self.target.exists())

    def test_resume_partial_owned_generation(self):
        actual = delta.copy_cas
        def fail_second(source, target, item):
            if item['path'] == 'media.bin':
                raise OSError('synthetic interruption')
            return actual(source, target, item)
        with patch.object(delta, 'copy_cas', side_effect=fail_second), self.assertRaises(OSError):
            self.run_transfer(True)
        self.assertTrue((self.target/'bots/state.sqlite').exists())
        self.assertFalse((self.target/'media.bin').exists())
        result = self.run_transfer(True)
        self.assertEqual(result['copied'], 1)
        self.assertEqual(result['reused'], 1)
        self.assertTrue(result['allTargetFilesVerified'])

    def test_source_changed_after_plan_does_not_publish_partial_file(self):
        actual = delta.copy_cas
        def mutate_then_copy(source, target, item):
            source.write_bytes(b'changed during transfer')
            return actual(source, target, item)
        with patch.object(delta, 'copy_cas', side_effect=mutate_then_copy), self.assertRaisesRegex(delta.DeltaError, 'source_changed_or_copy_hash_mismatch'):
            self.run_transfer(True)
        self.assertFalse((self.target/'bots/state.sqlite').exists())
        self.assertEqual([p.name for p in (self.target/'bots').iterdir()], [])

    def test_independent_final_verification_catches_post_publication_corruption(self):
        actual = delta.copy_cas
        def corrupt_after_copy(source, target, item):
            actual(source, target, item)
            target.write_bytes(b'corruption')
        with patch.object(delta, 'copy_cas', side_effect=corrupt_after_copy), self.assertRaisesRegex(delta.DeltaError, 'existing_target_hash_mismatch'):
            self.run_transfer(True)

    def test_source_and_target_symlinks_refused(self):
        link = self.source/'linked.bin'
        try:
            link.symlink_to(self.source/'media.bin')
        except OSError:
            self.skipTest('OS account cannot create symlinks')
        self.manifest['files'][1]['path'] = 'linked.bin'
        with self.assertRaisesRegex(delta.DeltaError, 'link_or_reparse_point_refused'):
            self.run_transfer(True)
        self.assertFalse(self.target.exists())

    def test_windows_reparse_and_target_links_rejected_without_os_privilege(self):
        actual = Path.lstat
        for blocked in (self.source/'media.bin', self.target):
            def linked(path, *args, **kwargs):
                if path == blocked:
                    return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
                return actual(path, *args, **kwargs)
            with self.subTest(blocked=blocked.name), patch.object(Path, 'lstat', linked):
                with self.assertRaisesRegex(delta.DeltaError, 'link_or_reparse_point_refused'):
                    self.run_transfer(True)
        self.assertFalse(self.target.exists())

    def test_overlap_case_collisions_and_necessity_flags_refused(self):
        with self.assertRaisesRegex(delta.DeltaError, 'overlapping_roots_refused'):
            delta.transfer(self.source, self.source/'nested-generation', self.manifest, True)
        extra = copy.deepcopy(self.manifest['files'][1])
        extra['path'] = 'MEDIA.bin'
        self.manifest['files'].append(extra)
        with self.assertRaisesRegex(delta.DeltaError, 'duplicate_or_case_colliding_path'):
            self.run_transfer(True)
        self.manifest['files'].pop()
        self.manifest['files'][0]['necessary'] = False
        with self.assertRaisesRegex(delta.DeltaError, 'necessity_reason_required'):
            self.run_transfer(True)
        self.assertFalse(self.target.exists())

    def test_cas_never_overwrites_concurrent_destination(self):
        item = self.manifest['files'][1]
        dest = self.root/'cas.bin'
        dest.write_bytes(b'other owner')
        with self.assertRaisesRegex(delta.DeltaError, 'target_changed_during_copy'):
            delta.copy_cas(self.source/'media.bin', dest, item)
        self.assertEqual(dest.read_bytes(), b'other owner')
        self.assertEqual(list(self.root.glob(delta.STAGE+'*')), [])

    def test_cli_default_plan_and_invalid_json_have_no_side_effect(self):
        manifest = self.root/'manifest.json'
        manifest.write_text(json.dumps(self.manifest), encoding='utf-8')
        args = [sys.executable, '-B', str(SCRIPT), str(self.source), str(self.target), str(manifest)]
        run = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['mode'], 'plan')
        self.assertFalse(self.target.exists())
        manifest.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding='utf-8')
        run = subprocess.run(args+['--apply'], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)['error'], 'duplicate_manifest_key')
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
