import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
spec = importlib.util.spec_from_file_location('host_monitor', Path(__file__).resolve().parents[1]/'scripts/windows-host-monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class HostMonitorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'services').mkdir()
        self.config = dict(stateRoot=str(self.root), dataRoot=str(self.root),
                           sourceRoot=str(self.root), postgresData=str(self.root))
        self.write('supervisor.json', dict(pid=1, created=1, preflight=True))
        for name, pid in [('control-api', 2), ('postgres', 3)]:
            self.write('services/'+name+'.process.json', dict(pid=pid, created=pid))

    def write(self, path, value):
        (self.root/path).write_text(json.dumps(value))

    def owned(self, row):
        p = Mock(pid=row['pid'])
        p.children.return_value = []
        return p

    def run_collect(self, owner=None):
        with patch.object(monitor, 'owned', side_effect=owner or self.owned), \
             patch.object(monitor, 'lifetime'), \
             patch.object(monitor, 'metrics', side_effect=lambda p,c,o,k: dict(pid=p.pid, created=c, pssBytes=None)):
            return monitor.publish(self.config)

    def test_preflight_packet_atomic_and_private(self):
        result = self.run_collect()
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['expectedAbsent'], ['agent:preflight'])
        self.assertEqual([p['container'] for p in result['processes']], ['api', 'postgres'])
        text = (self.root/'monitor-host.json').read_text()
        self.assertNotIn(str(self.root), text)
        self.assertNotIn('root', text)
        self.assertIsNone(result['processes'][0]['pssBytes'])
        self.assertEqual(json.loads(text), result)

    def test_missing_agent_not_expected_in_live_mode(self):
        self.write('supervisor.json', dict(pid=1, created=1, preflight=False))
        result = self.run_collect()
        self.assertEqual(result['expectedAbsent'], [])
        self.assertEqual(result['errors'][0]['kind'], 'agent')

    def test_pid_reuse_or_wrong_owner_cannot_publish(self):
        for reason in ['lifetime-mismatch', 'owner-mismatch']:
            with self.subTest(reason=reason):
                def owner(row):
                    if row['pid'] == 3:
                        raise ValueError(reason)
                    return self.owned(row)
                result = self.run_collect(owner)
                self.assertEqual([p['container'] for p in result['processes']], ['api'])
                self.assertEqual(result['errors'][0]['kind'], 'postgres')

    def test_dead_supervisor_does_not_mark_agent_expected(self):
        result = self.run_collect(lambda row: (_ for _ in ()).throw(ValueError('lifetime-mismatch')))
        self.assertEqual(result['processes'], [])
        self.assertEqual(result['expectedAbsent'], [])
        self.assertTrue(result['errors'])

    def test_record_replacement_during_collection_is_not_complete(self):
        def owner(row):
            if row['pid'] == 3:
                self.write('services/postgres.process.json', dict(pid=9, created=9))
            return self.owned(row)
        result = self.run_collect(owner)
        self.assertEqual([p['container'] for p in result['processes']], ['api'])
        self.assertTrue(result['errors'])


if __name__ == '__main__':
    unittest.main()
