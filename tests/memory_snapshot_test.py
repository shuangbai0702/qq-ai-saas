"""No QQ traffic: fake process tests and a read-only current-interpreter probe."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import memory_snapshot as snapshot


class FakeProcess:
    def __init__(self, pid, root='', created=100):
        self.pid = pid
        self.root = root
        self.created = created
        self.descendants = []
        self.denied = False
        self.exited = False
        self.reuse_on_read = False

    def create_time(self):
        if self.exited:
            raise snapshot.psutil.NoSuchProcess(self.pid)
        return self.created

    def is_running(self):
        return not self.exited

    def cmdline(self):
        return ['python', self.root, 'SECRET-CMDLINE']

    def children(self):
        return self.descendants

    def ppid(self):
        return 1

    def name(self):
        return 'python.exe'

    def memory_info(self):
        if self.exited:
            raise snapshot.psutil.NoSuchProcess(self.pid)
        if self.reuse_on_read:
            self.created += 1
        return NS(rss=4096, vms=8192)

    def memory_full_info(self):
        if self.denied:
            raise snapshot.psutil.AccessDenied(self.pid)
        return NS(pss=2048, uss=1024, swap=512)

    def cpu_times(self):
        return NS(user=1.25, system=.75)

    def num_threads(self):
        return 2

    def io_counters(self):
        return NS(read_bytes=10, write_bytes=20)


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        # All fixtures are outside the project; no runtime files are touched.
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.api = FakeProcess(1)
        self.processes = {1: self.api}
        self.patcher = patch.object(snapshot.psutil, 'Process', side_effect=self.lookup)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def lookup(self, pid):
        if pid not in self.processes:
            raise snapshot.psutil.NoSuchProcess(pid)
        return self.processes[pid]

    def record(self, owner='bot-a', kind='ai', pid=2, created=100, direct=False, **extra):
        directory = self.root / owner if direct else self.root / 'bots' / owner
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (kind + '.process.json')).write_text(
            json.dumps(dict(pid=pid, created=created, **extra)), encoding='utf-8')
        return directory

    def collect(self):
        return snapshot.collect(dict(root=str(self.root), apiPid=1))

    def test_ownership_rejects_sibling_prefix_and_forged_root(self):
        directory = self.record(root='SECRET-FAKE-ROOT')
        self.processes[2] = FakeProcess(2, str(directory) + '-other')
        result = self.collect()
        self.assertEqual([p['pid'] for p in result['processes']], [1])
        self.assertEqual(result['errors'][0]['reason'], 'owner-mismatch')
        output = json.dumps(result)
        for secret in [str(self.root), 'SECRET-FAKE-ROOT', 'SECRET-CMDLINE', 'cmdline', 'environ']:
            self.assertNotIn(secret, output)

    def test_reused_pid_and_missing_created_rejected(self):
        directory = self.record(created=99)
        self.processes[2] = FakeProcess(2, str(directory))
        self.record(owner='bot-b', pid=3, created=None)
        self.assertEqual([p['pid'] for p in self.collect()['processes']], [1])
        self.assertEqual(self.collect()['errors'][0]['reason'], 'lifetime-mismatch')

    def test_tree_global_dedup_and_api_priority(self):
        directory = self.record()
        self.record(kind='protocol')
        parent = FakeProcess(2, str(directory / 'ai' / 'main.py'))
        child = FakeProcess(3)
        parent.descendants = [child, self.api]
        self.processes.update({2: parent, 3: child})
        result = self.collect()
        self.assertEqual(sorted(p['pid'] for p in result['processes']), [1, 2, 3])
        self.assertEqual(result['processes'][0]['kind'], 'api')
        self.assertEqual(next(p for p in result['processes'] if p['pid'] == 3)['ownerId'], 'bot-a')
        self.assertEqual(result['errors'], [])

    def test_missing_full_info_keeps_rss_and_cpu(self):
        self.api.denied = True
        row = self.collect()['processes'][0]
        self.assertEqual(row['rssBytes'], 4096)
        self.assertEqual(row['cpuSeconds'], 2)
        for field in ['pssBytes', 'ussBytes', 'swapBytes']:
            self.assertIsNone(row[field])
        self.api.memory_full_info = lambda: NS(uss=12)
        row = self.collect()['processes'][0]
        self.assertIsNone(row['pssBytes'])
        self.assertIsNone(row['swapBytes'])
        self.assertEqual(row['ussBytes'], 12)

    def test_pid_reuse_during_metrics_is_not_published(self):
        self.api.reuse_on_read = True
        result = self.collect()
        self.assertEqual(result['processes'], [])
        self.assertEqual(result['errors'][0]['reason'], 'lifetime-mismatch')

    def test_process_exit_and_access_denied(self):
        self.api.exited = True
        self.assertEqual(self.collect()['errors'][0]['reason'], 'process-exited')
        self.api.exited = False
        self.api.create_time = lambda: (_ for _ in ()).throw(snapshot.psutil.AccessDenied(1))
        self.assertEqual(self.collect()['errors'][0]['reason'], 'access-denied')

    def test_groups_in_bots_and_direct_root_keep_owner(self):
        for pid, owner, direct in [(2, 'group-in-bots', False), (3, 'group-direct', True)]:
            directory = self.record(owner=owner, pid=pid, direct=direct)
            self.processes[pid] = FakeProcess(pid, str(directory))
        owners = {p['ownerId'] for p in self.collect()['processes']}
        self.assertEqual(owners, {None, 'group-in-bots', 'group-direct'})

    def test_limits(self):
        directory = self.record()
        parent = FakeProcess(2, str(directory))
        parent.descendants = [FakeProcess(pid) for pid in range(3, 520)]
        self.processes.update({p.pid: p for p in [parent] + parent.descendants})
        result = self.collect()
        self.assertEqual(len(result['processes']), 512)
        self.assertTrue(result['truncated'])
        with patch.object(snapshot, 'MAX_DIRECTORIES', 0):
            result = self.collect()
            self.assertEqual(len(result['processes']), 1)
            self.assertTrue(result['truncated'])
        for index in range(35):
            self.record(owner='bad-' + str(index), pid=10000 + index)
        result = self.collect()
        self.assertEqual(len(result['errors']), 32)
        self.assertTrue(result['truncated'])

    def test_invalid_input_is_bounded(self):
        for request in [None, [], {}, {'root': str(self.root), 'apiPid': True}]:
            result = snapshot.collect(request)
            self.assertEqual(result['errors'][0]['reason'], 'invalid-input')
            self.assertLess(len(json.dumps(result)), 1024)


class RealProcessTest(unittest.TestCase):
    def test_cli_current_python_read_only(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as root:
            request = json.dumps(dict(root=root, apiPid=os.getpid()))
            completed = subprocess.run(
                [sys.executable, '-B', str(Path(snapshot.__file__))], input=request,
                text=True, capture_output=True, timeout=15, check=True)
            self.assertEqual(completed.stderr, '')
            result = json.loads(completed.stdout)
            self.assertEqual(result['errors'], [])
            self.assertEqual(len(result['processes']), 1)
            row = result['processes'][0]
            self.assertEqual(row['pid'], os.getpid())
            self.assertEqual(row['kind'], 'api')
            self.assertGreater(row['rssBytes'], 0)
            self.assertGreaterEqual(row['cpuSeconds'], 0)
            self.assertGreaterEqual(result['collectDurationMs'], 0)
            self.assertFalse(result['truncated'])
            self.assertEqual(list(Path(root).iterdir()), [])
            self.assertLess(len(completed.stdout), 8192)
            for forbidden in [root, 'cmdline', 'environ', 'SECRET']:
                self.assertNotIn(forbidden, completed.stdout)


if __name__ == '__main__':
    unittest.main()
