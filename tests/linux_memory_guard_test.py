"""Deterministic pressure-window checks; no host cgroup modifications."""
import importlib.util
from pathlib import Path
import json
import os
import tempfile
from unittest.mock import patch
import sys
import unittest

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('memory_guard', Path(__file__).resolve().parents[1] / 'scripts/linux-memory-guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class PressureTest(unittest.TestCase):
    def test_sustained_full_stall_triggers_only_after_observation_window(self):
        samples = []
        for second in range(0, 18, 2):
            self.assertFalse(guard.pressure_exceeded(samples, second, second * 400_000))
        self.assertTrue(guard.pressure_exceeded(samples, 18, 7_200_000))

    def test_old_stall_expires_and_low_pressure_does_not_trigger(self):
        samples = []
        guard.pressure_exceeded(samples, 0, 0)
        guard.pressure_exceeded(samples, 2, 6_000_000)
        self.assertTrue(guard.pressure_exceeded(samples, 18, 6_000_000))
        self.assertFalse(guard.pressure_exceeded(samples, 22, 6_000_000))
        for second in range(24, 90, 2):
            self.assertFalse(guard.pressure_exceeded(samples, second, 6_000_000 + second * 1000))
        self.assertLessEqual(len(samples), 12)

    def test_counter_and_clock_reset_clear_previous_window(self):
        for now, total in [(3, 1), (1, 100_000)]:
            samples = [(2, 90_000)]
            self.assertFalse(guard.pressure_exceeded(samples, now, total))
            self.assertEqual(samples, [(now, total)])


class RollbackTest(unittest.TestCase):
    def test_repeated_rollback_cooldown_survives_recreation_and_resets_after_stability(self):
        with tempfile.TemporaryDirectory() as root:
            base=Path(root);(base/'limits.json').write_text('[]')
            with patch.object(guard,'BASE',base):
                for now,expected in [(1000,30),(1035,60),(1100,120),(1300,240),(1600,480),(2100,600),(2801,30)]:
                    (base/'disabled').unlink(missing_ok=True)
                    with patch.object(guard.time,'time',return_value=now):guard.rollback('pressure')
                    self.assertEqual(json.loads((base/'recovery.json').read_text())['cooldown'],expected)
    def test_relaxes_every_registered_limit_and_disables_controller(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as root:
            base = Path(root)
            (base / 'disabled').unlink(missing_ok=True)
            entries = [{'path': '/sys/fs/cgroup/system.slice/docker-abc.scope/fishbot-napcat/memory.high'},
                       {'path': '/sys/fs/cgroup/system.slice/docker-def.scope/memory.high'}]
            (base / 'limits.json').write_text(json.dumps(entries))
            original = Path.write_text
            original_read = Path.read_text
            def read(path, *args, **kwargs):
                if str(path).startswith(str(Path('/sys/fs/cgroup'))):
                    return '33554432'
                return original_read(path, *args, **kwargs)
            writes = []

            def write(path, value, *args, **kwargs):
                if str(path).startswith(str(Path('/sys/fs/cgroup'))):
                    writes.append((str(path), value))
                    return len(value)
                return original(path, value, *args, **kwargs)

            with patch.object(guard, 'BASE', base), patch.object(Path, 'read_text', autospec=True, side_effect=read), patch.object(Path, 'write_text', autospec=True, side_effect=write):
                guard.rollback('test-pressure')
            self.assertEqual(writes, [(str(Path(entry['path'])), 'max') for entry in entries])
            self.assertEqual((base / 'disabled').read_text(), 'test-pressure')
            self.assertEqual(json.loads((base / 'guard.jsonl').read_text())['failedPaths'], [])

    def test_already_relaxed_limit_is_not_written_again(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as root:
            base = Path(root)
            limit = Path('/sys/fs/cgroup/system.slice/docker-abc.scope/memory.high')
            (base / 'limits.json').write_text(json.dumps([{'path': str(limit)}]))
            original_read, original_write = Path.read_text, Path.write_text
            writes = []
            def read(path, *args, **kwargs):
                return 'max' if path == limit else original_read(path, *args, **kwargs)
            def write(path, value, *args, **kwargs):
                if path == limit:
                    writes.append(value)
                    return len(value)
                return original_write(path, value, *args, **kwargs)
            with patch.object(guard, 'BASE', base), patch.object(Path, 'read_text', autospec=True, side_effect=read), patch.object(Path, 'write_text', autospec=True, side_effect=write):
                guard.rollback('test-idempotent')
                guard.rollback('test-idempotent')
            self.assertEqual(writes, [])
            self.assertEqual(len((base / 'guard.jsonl').read_text().splitlines()), 1)


if __name__ == '__main__':
    unittest.main()
