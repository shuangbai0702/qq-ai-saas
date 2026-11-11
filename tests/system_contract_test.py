import sqlite3
import sys
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters/astrbot'))
from system_contract import SystemContract, SystemContractViolation, system_vector_sha


class SystemContractTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.tmp = tempfile.TemporaryDirectory()
        self.guard = SystemContract(self.db)
        self.stable = [
            {'role': 'system', 'content': 'fixed persona'},
            {'role': 'user', 'content': 'event data'},
            {'role': 'system', 'content': 'fixed protocol'},
        ]

    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def test_first_bootstrap_is_persisted_and_scope_or_phase_cannot_rekey_it(self):
        first = self.guard.reserve(self.stable, None, 'request-1', 1)
        self.assertTrue(first['bootstrap'])
        self.assertTrue(self.guard.settle(first, True, 2))
        after_restart = SystemContract(self.db)
        self.assertFalse(after_restart.reserve(self.stable, None, 'other-scope-planning', 3)['bootstrap'])
        changed = [self.stable[0], {'role': 'user', 'content': 'event data'},
                   {'role': 'system', 'content': 'different protocol'}]
        with self.assertRaises(SystemContractViolation):
            after_restart.reserve(changed, None, 'new-scope-or-phase', 4)

    def test_hash_covers_system_count_order_position_and_exact_content(self):
        original = system_vector_sha(self.stable)
        variants = [
            [{'role': 'user', 'content': 'event data'}, *self.stable],
            [{'role': 'system', 'content': 'fixed protocol'}, {'role': 'user', 'content': 'event data'},
             {'role': 'system', 'content': 'fixed persona'}],
            [*self.stable, {'role': 'system', 'content': 'third'}],
            [{'role': 'system', 'content': 'fixed persona\n'}, *self.stable[1:]],
        ]
        for value in variants:
            with self.subTest(value=system_vector_sha(value)):
                self.assertNotEqual(system_vector_sha(value), original)

    def test_only_successful_authenticated_save_token_changes_contract_once(self):
        self.guard.reserve(self.stable, None, 'bootstrap', 1)
        changed = [{'role': 'system', 'content': 'user saved persona'}]
        with self.assertRaises(SystemContractViolation):
            self.guard.reserve(changed, None, 'untrusted-change', 2)
        reserved = self.guard.reserve(changed, 'audit-42', 'failed-request', 3)
        self.assertTrue(reserved['changed'])
        self.assertTrue(self.guard.settle(reserved, False, 4))
        retry = self.guard.reserve(changed, 'audit-42', 'successful-request', 5)
        self.assertTrue(self.guard.settle(retry, True, 6))
        self.assertEqual(self.guard.reserve(changed, None, 'stable-after-save', 7)['systemSha'],
                         system_vector_sha(changed))
        later = [{'role': 'system', 'content': 'unrelated code change'}]
        with self.assertRaises(SystemContractViolation):
            self.guard.reserve(later, 'audit-42', 'replay-consumed-token', 8)

    def test_same_system_consumes_save_token_only_after_success(self):
        self.guard.reserve(self.stable, None, 'bootstrap', 1)
        reservation = self.guard.reserve(self.stable, 'audit-77', 'successful-noop-save', 2)
        self.assertFalse(reservation['changed'])
        self.assertTrue(self.guard.settle(reservation, True, 3))
        with self.assertRaises(SystemContractViolation):
            self.guard.reserve([{'role': 'system', 'content': 'late unrelated change'}], 'audit-77', 'late', 4)

    def test_actual_wire_observer_enforces_before_request_and_settles_on_result(self):
        import httpx
        from model_cache import begin_attempt,finish_attempt,observe_wire_request,reset_attempt
        lease={'root':self.tmp.name,'tenantId':'isolated-tenant','botId':'isolated-bot','revision':3}
        event=NS(get_extra=lambda key,default=None:lease if key=='_hosted_lease' else default)
        provider=object()
        def send(messages,permit=None,status='completed'):
            token=begin_attempt(event,provider,system_contract=self.guard,system_permit_id=permit)
            try:
                request=httpx.Request('POST','https://fixture.invalid/v1/chat/completions',content=json.dumps({'messages':messages}).encode())
                observe_wire_request(request)
                finish_attempt(status)
            finally:reset_attempt(token)
        send(self.stable)
        send(self.stable,'audit-deferred')
        self.assertEqual(self.db.execute("SELECT count(*) FROM fishbot_system_permits WHERE permit_id='audit-deferred'").fetchone()[0],0)
        send([{'role':'system','content':'adopted at legal checkpoint'}],'audit-deferred')
        self.assertEqual(self.db.execute("SELECT state FROM fishbot_system_permits WHERE permit_id='audit-deferred'").fetchone()[0],'consumed')
        with self.assertRaises(SystemContractViolation):send([{'role':'system','content':'unauthorized'}])
        send([{'role':'system','content':'saved'}],'audit-88')
        with self.assertRaises(SystemContractViolation):send([{'role':'system','content':'late change'}],'audit-88')


if __name__ == '__main__':
    unittest.main()
