import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters/astrbot'))

import activation_rounds
import autonomy
import context_transport
import data_admin
from behavior import BehaviorStore
from context_protocol import is_persona_reference, persona_reference


class P0CorrectnessRegressionTests(unittest.TestCase):
    def test_p0_1_owner_and_scheduled_contact_annotate_service_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = BehaviorStore(Path(tmp) / 'behavior.sqlite')
            try:
                now = time.time()
                for src, evt in (('owner_contact', 'oc-1'), ('scheduled_contact', 'sc-1'), ('autonomous', 'au-1')):
                    st.db.execute(
                        "INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,updated_at) VALUES(?,?,?,?,?,'commit','sent','',?)",
                        ('private:10001', '10001', evt, now, src, now),
                    )
                    activation_rounds.annotate_service(
                        st.db, 'bot_shared', f'private:10001#{evt}', evt, source_scope='private:10001'
                    )
                    row = ('bot_shared', '10001', f'private:10001#{evt}', 'hi', 'hello', now)
                    self.assertTrue(activation_rounds.valid(st.db, 'bot_shared', row))
                    self.assertTrue(activation_rounds.settled(st.db, 'bot_shared', [row]))
                # Plain user message source or mismatched scope must still be rejected.
                st.db.execute(
                    "INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,updated_at) VALUES(?,?,?,?,'message','commit','sent','',?)",
                    ('private:10001', '10001', 'msg-1', now, now),
                )
                with self.assertRaisesRegex(ValueError, 'service_dialogue_owner_missing'):
                    activation_rounds.annotate_service(
                        st.db, 'bot_shared', 'private:10001#msg-1', 'msg-1', source_scope='private:10001'
                    )
                with self.assertRaisesRegex(ValueError, 'service_dialogue_owner_missing'):
                    activation_rounds.annotate_service(
                        st.db, 'bot_shared', 'private:99999#oc-1', 'oc-1', source_scope='private:99999'
                    )
            finally:
                st.close()

    def test_p0_2_no_phantom_or_duplicate_persona_reference_on_wire(self):
        persona = '你是一只傲娇的蓝色肥鱼。'
        # Case A: SDK injects standard Persona Instructions with no extra preamble -> single persona_reference
        stable, history = context_transport.request_context_view(
            persona,
            [
                {'role': 'system', 'content': f'\n# Persona Instructions\n\n{persona}\n'},
                {'role': 'user', 'content': '你好'},
            ],
            None,
            'p',
            '',
            json.dumps({'scope': 'private:10001'}),
            platform_system=context_transport.foreground_system({}),
        )
        persona_msgs = [m for m in history if is_persona_reference(m)]
        self.assertEqual(len(persona_msgs), 1)
        self.assertEqual(persona_msgs[0], persona_reference(persona))

        # Case B: Bare persona + RECEIPT_GUIDANCE (as in autonomous main activation) -> no duplicate persona
        _, history_b = context_transport.request_context_view(
            persona,
            [
                {'role': 'system', 'content': f'{persona}\n真实回执规则。'},
                {'role': 'user', 'content': '激活'},
            ],
            None,
            'p',
            '',
            json.dumps({'scope': 'private:10001'}),
            platform_system=context_transport.foreground_system({}),
        )
        persona_msgs_b = [m for m in history_b if is_persona_reference(m)]
        self.assertEqual(len(persona_msgs_b), 2)
        self.assertEqual(persona_msgs_b[0], persona_reference(persona))
        self.assertEqual(persona_msgs_b[1], persona_reference('真实回执规则。'))
        self.assertNotIn(persona, persona_msgs_b[1]['content'])

    def test_p0_3_no_fact_vector_triggers_and_no_expression_hints_hot_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = BehaviorStore(Path(tmp) / 'behavior.sqlite')
            try:
                triggers = {
                    r[0]
                    for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
                }
                self.assertNotIn('fact_vectors_insert', triggers)
                self.assertNotIn('fact_vectors_update', triggers)
                self.assertNotIn('fact_vectors_delete', triggers)
                ctx = st.social.context('group:12345', '你好', {'styleLearning': True, 'memeEnabled': False})
                self.assertNotIn('expression_hints', ctx)
            finally:
                st.close()

    def test_p0_4_readonly_sticker_and_autonomy_queries_never_write_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / 'behavior.sqlite'
            st = BehaviorStore(db_path)
            st.db.execute(
                "INSERT INTO contacts(scope,subject,self_id,last_user,last_bot,last_attempt,unanswered,opted_out,last_text,last_output) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ('group:10001', '20001', '90001', 100.0, 90.0, 0.0, 0, 0, 'hi', 'yo'),
            )
            st.db.execute(
                "INSERT INTO contacts(scope,subject,self_id,last_user,last_bot,last_attempt,unanswered,opted_out,last_text,last_output) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ('private:20001', '20001', '90001', 100.0, 90.0, 0.0, 0, 0, 'hi', 'yo'),
            )
            st.db.commit()
            st.close()

            # Read-only data_admin sticker queries must succeed even if sticker tables don't exist
            res = data_admin.run(db_path, {'action': 'list_stickers'})
            self.assertEqual(res['items'], [])
            missing = data_admin.run(db_path, {'action': 'get_sticker', 'id': 'none'})
            self.assertEqual(missing['status'], 404)

            # Strictly read-only SQLite connection proves zero writes in snapshot() and owner_delivery_candidate()
            ro = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)
            try:
                gp = autonomy.GroupPresence.__new__(autonomy.GroupPresence)
                gp.db = ro
                snap = gp.snapshot('group:10001')
                self.assertEqual(snap, (0, 90.0, 0, ''))
                cfg = {'behavior': {'ownerUin': '20001', 'ownerContactEnabled': True, 'enabled': True}}
                self.assertIsNone(autonomy.owner_delivery_candidate(ro, cfg, 5000.0))
            finally:
                ro.close()


if __name__ == '__main__':
    unittest.main()
