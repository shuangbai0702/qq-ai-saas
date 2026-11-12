"""Offline manifests only; no credentials, hosts, QQ, or production files."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/mobile-cutover-precheck.py'
spec = importlib.util.spec_from_file_location('mobile_cutover_precheck', SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def fixture():
    return {'schemaVersion': 1,
            'baseline': {'codeCommit': 'a'*40, 'databaseSchema': 'b'*64, 'configSchema': 'c'*64},
            'evidence': {'method': 'stopped-read-only', 'snapshotId': 'frozen-final-1', 'observedAt': '2026-11-12T08:00:00Z'},
            'state': {'consumptionStopped': True, 'executionAuthorityEnabled': False,
                      'allQQAIProcessesExited': True, 'routeGateEnabled': False},
            'deletionPolicy': 'no-age-only-deletion',
            'bots': {'bot-fixture': {
                'sqlite': {'behavior': {'sha256': 'd'*64, 'integrityCheck': 'ok'}},
                'journals': {'bot_shared': 'e'*64, 'private:fixture': 'f'*64},
                'ledgers': {'unknown': {'count': 2, 'sha256': '1'*64}, 'partial': {'count': 1, 'sha256': '2'*64}},
                'references': ['media-fixture']}},
            'files': {'media-fixture': '3'*64}}


class CutoverPrecheck(unittest.TestCase):
    def setUp(self):
        self.source = fixture()
        self.target = copy.deepcopy(self.source)

    def rejects(self, code):
        with self.assertRaisesRegex(check.PrecheckError, '^'+code+'$'):
            check.precheck(self.source, self.target)

    def test_matching_manifest_is_only_a_check_and_does_not_change_inputs(self):
        before = copy.deepcopy((self.source, self.target))
        result = check.precheck(self.source, self.target)
        self.assertTrue(result['ready'])
        self.assertTrue(result['checksOnly'])
        self.assertFalse(result['executionAuthorized'])
        self.assertTrue(result['requiresIndependentEvidence'])
        self.assertEqual((self.source, self.target), before)

    def test_baseline_and_schema_must_match(self):
        for key in self.target['baseline']:
            with self.subTest(key=key):
                self.target = copy.deepcopy(self.source)
                self.target['baseline'][key] = '9'*len(self.target['baseline'][key])
                self.rejects('baseline_or_schema_mismatch')

    def test_unknown_or_partial_loss_refuses_even_with_equal_other_data(self):
        for ledger in ('unknown', 'partial'):
            for field, value in (('count', 0), ('sha256', '9'*64)):
                with self.subTest(ledger=ledger, field=field):
                    self.target = copy.deepcopy(self.source)
                    self.target['bots']['bot-fixture']['ledgers'][ledger][field] = value
                    self.rejects('unknown_or_partial_ledger_mismatch')

    def test_missing_scope_or_bot_refuses(self):
        del self.target['bots']['bot-fixture']['journals']['private:fixture']
        self.rejects('journal_scope_or_digest_mismatch')
        self.target = copy.deepcopy(self.source)
        self.target['bots']['other-fixture'] = self.target['bots'].pop('bot-fixture')
        self.rejects('bot_inventory_mismatch')

    def test_sqlite_integrity_and_snapshot_must_match(self):
        self.target['bots']['bot-fixture']['sqlite']['behavior']['integrityCheck'] = 'not ok'
        self.rejects('sqlite_integrity_unproven')
        self.target = copy.deepcopy(self.source)
        self.target['bots']['bot-fixture']['sqlite']['behavior']['sha256'] = '8'*64
        self.rejects('sqlite_snapshot_mismatch')

    def test_missing_or_changed_referenced_file_refuses(self):
        self.target['files'].clear()
        self.rejects('referenced_file_missing')
        self.target = copy.deepcopy(self.source)
        self.target['files']['media-fixture'] = '9'*64
        self.rejects('referenced_file_digest_mismatch')

    def test_live_gates_or_processes_refuse(self):
        for side in ('source', 'target'):
            for key, value in (('consumptionStopped', False), ('allQQAIProcessesExited', False),
                               ('executionAuthorityEnabled', True), ('routeGateEnabled', True)):
                with self.subTest(side=side, key=key):
                    self.source, self.target = fixture(), fixture()
                    getattr(self, side)['state'][key] = value
                    self.rejects('consumer_or_process_still_active' if key in ('consumptionStopped', 'allQQAIProcessesExited') else 'execution_or_route_gate_open')

    def test_evidence_must_exist_and_bind_same_frozen_snapshot(self):
        self.target['evidence']['snapshotId'] = 'other-snapshot'
        self.rejects('frozen_snapshot_mismatch')
        self.target = copy.deepcopy(self.source)
        self.target['evidence']['method'] = 'copied-source-manifest'
        self.rejects('missing_freeze_evidence')

    def test_no_age_only_deletion_and_no_truthy_string_flags(self):
        self.target['deletionPolicy'] = 'delete-older-than-30-days'
        self.rejects('unsafe_deletion_policy')
        self.target = copy.deepcopy(self.source)
        self.target['state']['executionAuthorityEnabled'] = 'false'
        self.rejects('missing_process_gate_evidence')

    def test_cli_reads_only_and_duplicate_keys_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/'a.json', Path(directory)/'b.json'
            a.write_text(json.dumps(self.source), encoding='utf-8')
            b.write_text(json.dumps(self.target), encoding='utf-8')
            before = {p.name: p.read_bytes() for p in Path(directory).iterdir()}
            run = subprocess.run([sys.executable, '-B', str(SCRIPT), str(a), str(b)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertFalse(json.loads(run.stdout)['executionAuthorized'])
            self.assertEqual(before, {p.name: p.read_bytes() for p in Path(directory).iterdir()})
            b.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding='utf-8')
            run = subprocess.run([sys.executable, '-B', str(SCRIPT), str(a), str(b)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(json.loads(run.stdout)['error'], 'duplicate_manifest_key')


if __name__ == '__main__':
    unittest.main()
