"""A legal 35-round batch can explain an unavailable old Hold to the model."""
import json,sys,unittest
from pathlib import Path
from unittest.mock import patch
import sqlite3
sys.path.insert(0,str(Path(__file__).resolve().parent))
import semantic_discard_test as fixtures
import semantic_batch_summary_test as local_fixtures
from context_protocol import event_message,model_json
import summary_material,semantic_collection

class LegacyHold(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.DiscardTests.asyncSetUp
    asyncTearDown=fixtures.DiscardTests.asyncTearDown
    prepare=fixtures.DiscardTests.prepare

    def held(self,incomplete=False):
        r,db=self.prepare(35)
        key=model_json(['group:55555','lost'])
        db.execute('INSERT INTO memory_hold_rounds VALUES(?,?,NULL,?,?)',('bot_shared',key,'uncovered',50))
        if incomplete:
            msg=event_message({'scope':'group:55555','sender_id':'87654321','event_id':'lost','at':50},'ACTUAL_REMAINING_FRAGMENT')
            m=summary_material.snapshots([msg],'bot_shared')[0];m['complete']=False
            self.assertTrue(summary_material.valid_material(m,'bot_shared'))
            db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',('bot_shared',key,'87654321',model_json(m)))
        db.commit();return r,db,key

    async def check_success(self,incomplete=False):
        r,db,key=self.held(incomplete)
        self.assertTrue(r.retry());await r.task
        self.assertEqual(len(self.requests),1)
        wire=model_json(self.requests[0]['messages'])
        self.assertIn('ACTUAL_REMAINING_FRAGMENT',wire)
        self.assertIn('incomplete_original' if incomplete else 'full_original',wire)
        self.assertIn('missing部分未提供',wire)
        self.assertEqual(db.execute('SELECT round_count FROM memory_reconstruction_checkpoints').fetchone(),(35,))
        self.assertIsNotNone(db.execute('SELECT 1 FROM memory_hold_rounds WHERE event=?',(key,)).fetchone(),'Hold retires only after actual Journal replacement')
        self.assertEqual(db.execute('SELECT count(*) FROM memory_summary_coverage').fetchone()[0],0)

    async def test_missing_hold_reaches_model_in_legal_batch(self):await self.check_success()
    async def test_incomplete_hold_reaches_model_in_legal_batch(self):await self.check_success(True)

    async def test_database_failure_keeps_everything_and_does_not_mint_checkpoint(self):
        r,db,key=self.held()
        with patch.object(semantic_collection,'collect',side_effect=sqlite3.OperationalError('fixture database unavailable')):
            self.assertTrue(r.retry());await r.task
        self.assertEqual(self.requests,[])
        self.assertEqual(db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)
        self.assertIsNotNone(db.execute('SELECT 1 FROM memory_hold_rounds WHERE event=?',(key,)).fetchone())
        self.assertIsNone(db.execute('SELECT 1 FROM memory_reconstruction_checkpoints').fetchone())

    async def test_conflicting_sender_still_fails_without_checkpoint(self):
        r,db,key=self.held()
        msg=event_message({'scope':'group:55555','sender_id':'11111111','event_id':'lost','at':50},'CONFLICTING_AUTHOR')
        m=summary_material.snapshots([msg,{'role':'assistant','content':'reply'}],'bot_shared')[0]
        db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',('bot_shared',key,'11111111',model_json(m)));db.commit()
        self.assertTrue(r.retry());await r.task
        self.assertEqual(self.requests,[])
        self.assertEqual(db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)
        self.assertIsNone(db.execute('SELECT 1 FROM memory_reconstruction_checkpoints').fetchone())
        self.assertIsNotNone(db.execute('SELECT 1 FROM memory_hold_rounds WHERE event=?',(key,)).fetchone())

    async def test_material_change_during_model_request_rolls_back_checkpoint(self):
        r,db,key=self.held()
        original=self.provider.text_chat_stream
        async def changed(**kwargs):
            async for response in original(**kwargs):yield response
            row=db.execute('SELECT event,material FROM memory_round_material LIMIT 1').fetchone()
            material=json.loads(row[1]);material['reply']='changed after snapshot'
            db.execute('UPDATE memory_round_material SET material=? WHERE event=?',(model_json(material),row[0]));db.commit()
        self.provider.text_chat_stream=changed
        self.assertTrue(r.retry());await r.task
        self.assertEqual(len(self.requests),1)
        self.assertEqual(db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)
        self.assertIsNone(db.execute('SELECT 1 FROM memory_reconstruction_checkpoints').fetchone())
        self.assertIsNotNone(db.execute('SELECT 1 FROM memory_hold_rounds WHERE event=?',(key,)).fetchone())

class LocalLegacyHold(unittest.IsolatedAsyncioTestCase):
    setUp=local_fixtures.SemanticSummary.setUp
    tearDown=local_fixtures.SemanticSummary.tearDown
    record=local_fixtures.SemanticSummary.record
    chat=local_fixtures.SemanticSummary.chat
    run_summary=local_fixtures.SemanticSummary.run_summary
    checkpoint=local_fixtures.SemanticSummary.checkpoint

    async def test_private_35_with_orphan_hold_reports_unknown_author_without_fabrication(self):
        self.record()
        scope=local_fixtures.SCOPE
        self.db.execute('INSERT INTO memory_hold_rounds VALUES(?,?,NULL,?,?)',(scope,'orphan-missing','uncovered',50));self.db.commit()
        self.assertTrue(await self.run_summary())
        self.assertEqual(len(self.calls),1)
        prompt=json.loads(self.calls[0]['prompt'])
        orphan=next(item for item in prompt['incomplete_material'] if item['event']=='orphan-missing')
        self.assertEqual(orphan['sender_ids'],[]);self.assertEqual(orphan['missing'],'full_original')
        self.assertEqual(orphan['available_text'],[])
        self.assertEqual(self.checkpoint()[1],35)
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM memory_hold_rounds WHERE event=?',('orphan-missing',)).fetchone())
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_summary_coverage').fetchone()[0],0)

    async def test_private_material_only_incomplete_hold_preserves_fragment_and_author(self):
        self.record();scope=local_fixtures.SCOPE
        msg=event_message({'scope':scope,'sender_id':'12345678','event_id':'old-fragment','at':50},'LOCAL_HELD_REAL_FRAGMENT')
        m=summary_material.snapshots([msg],scope)[0];m['complete']=False
        self.db.execute('INSERT INTO memory_hold_rounds VALUES(?,?,NULL,?,?)',(scope,'old-fragment','uncovered',50))
        self.db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',(scope,'old-fragment','12345678',model_json(m)));self.db.commit()
        self.assertTrue(await self.run_summary());self.assertEqual(len(self.calls),1)
        prompt=json.loads(self.calls[0]['prompt']);item=next(x for x in prompt['incomplete_material'] if x['event']=='old-fragment')
        self.assertEqual(item['sender_ids'],['12345678']);self.assertIn('LOCAL_HELD_REAL_FRAGMENT',item['available_text'][0])
        self.assertEqual(self.checkpoint()[1],35)

    async def test_private_held_material_identity_conflict_does_not_become_missing(self):
        self.record();scope=local_fixtures.SCOPE
        msg=event_message({'scope':scope,'sender_id':'12345678','event_id':'actual-other-event','at':50},'WRONG_IDENTITY')
        m=summary_material.snapshots([msg],scope)[0]
        self.db.execute('INSERT INTO memory_hold_rounds VALUES(?,?,NULL,?,?)',(scope,'held-event','uncovered',50))
        self.db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',(scope,'held-event','12345678',model_json(m)));self.db.commit()
        self.assertTrue(await self.run_summary());self.assertEqual(self.calls,[]);self.assertIsNone(self.checkpoint())
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],35)

if __name__=='__main__':unittest.main()
