"""Exact shared wire, queued input ownership and durable completion metadata."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from behavior import BehaviorStore
from readonly_journal import ReadonlyJournal
from context_protocol import source,event_message,model_json
from unified_context_20261108_test import request,message
import shared_context as shared
import summary_material as material
import message_provenance as provenance

class Ownership(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'b.sqlite'
        self.store=BehaviorStore(self.path);self.db=self.store.db;self.scope='private:12345678'
        self.j=ReadonlyJournal(self.db,'bot_shared')
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def stage(self,m,inputs):
        origin=source(m)
        seq,_=shared.compose(self.j,request(m),origin['scope'],origin['event_id'],inputs)
        prepared=self.j.prepare(seq,'same-system',strict=True)
        self.assertTrue(self.j.stage_candidate(prepared));self.assertTrue(self.j.commit(prepared))
    def settle(self,event,status='silent',attempted=0,confirmed=0):
        provenance.settle_model_output(self.db,NS(scope=self.scope,event_id=event,subject='12345678',status=status,attempted=attempted,confirmed=confirmed,planned_messages=confirmed,confirmed_messages=confirmed))
        self.db.commit()
    def test_neighbour_submitted_string_is_reused_despite_sdk_list_view(self):
        a=message(self.scope,'a');b=message(self.scope,'b',text='next真实正文')
        digest=provenance.bind_model_input(self.db,'bot_shared',self.scope,'b','12345678',b);self.db.commit()
        self.stage(a,[a,b]);self.j.append_completion({'role':'assistant','content':'answer A'},model_origin=(self.scope,'a','12345678'))
        old=model_json(self.j.last.wire)
        live=copy.deepcopy(b);live['content']=[{'type':'text','text':b['content']}]
        seq,_=shared.compose(self.j,request(live),self.scope,'b',input_digest=digest)
        candidate=self.j.prepare(seq,'same-system',strict=True)
        self.assertEqual(model_json(candidate.wire[:len(self.j.last.wire)]),old)
        self.assertEqual(sum(source(m)==source(b) for m in candidate.base),1)
        self.assertIsInstance(next(m['content'] for m in candidate.base if source(m)==source(b)),str)
        with self.assertRaisesRegex(ValueError,'protocol_input_changed'):
            shared.compose(self.j,request(live),self.scope,'b',input_digest='wrong')
        changed=copy.deepcopy(b);changed['content']=b['content']+'different'
        with self.assertRaisesRegex(ValueError,'protocol_input_changed'):
            provenance.bind_model_input(self.db,'bot_shared',self.scope,'b','12345678',changed)
        self.assertEqual(model_json(self.j.last.wire),old)
    def test_physical_neighbour_draft_has_real_work_owner_not_last_user(self):
        a=message(self.scope,'a');b=message(self.scope,'b')
        self.stage(a,[a,b]);prefix=model_json(self.j.last.wire)
        self.j.append_completion({'role':'assistant','content':'reply belonging to A'},model_origin=(self.scope,'a','12345678'))
        self.settle('a','sent',1,1);self.settle('b')
        items=material.snapshots(self.j.last.base,'bot_shared',db=self.db)
        block=items[-1]
        self.assertEqual(block['reply'],'')
        self.assertEqual(block['native_outputs'][0]['owner']['event_id'],'a')
        self.assertTrue(material.coverage_eligible(block,'bot_shared'))
        self.assertEqual(model_json(self.j.last.wire[:-1]),prefix)
        rows=[{'event':block['event'],'sender_id':'12345678','user':'hello','bot':'','at':1}]
        claims=material.derive_claims('bot_shared',rows,{block['event']:block},[],{'evidence':[{'event':block['event'],'who':'bot','quote':'reply belonging to A'}]})
        self.assertEqual(claims,[],'foreign draft cannot become this event bot statement')
    def test_identical_outputs_in_distinct_work_keep_distinct_owners(self):
        for event in ('a','b'):
            m=message(self.scope,event);self.stage(m,[m])
            self.j.append_completion({'role':'assistant','content':'same'},model_origin=(self.scope,event,'12345678'))
            self.settle(event,'sent',1,1)
        items=material.snapshots(self.j.last.base,'bot_shared',db=self.db)
        self.assertEqual([i['native_outputs'][0]['owner']['event_id'] for i in items],['a','b'])
        self.assertTrue(all(material.coverage_eligible(i,'bot_shared') for i in items))
    def test_completion_metadata_failure_rolls_back_wire_and_restart(self):
        a=message(self.scope,'a');self.stage(a,[a]);old=model_json(self.j.last.wire)
        self.db.execute("CREATE TRIGGER fail_metadata BEFORE INSERT ON memory_message_sources WHEN NEW.direction='model' BEGIN SELECT RAISE(ABORT,'metadata fail'); END");self.db.commit()
        with self.assertRaises(Exception):self.j.append_completion({'role':'assistant','content':'new'},model_origin=(self.scope,'a','12345678'))
        self.assertEqual(model_json(ReadonlyJournal(self.db,'bot_shared').last.wire),old)
        self.assertEqual(model_json(self.j.last.wire),old)
    def test_future_event_shape_and_media_preserve_parts_and_source(self):
        origin={'sender_id':'12345678','scope':self.scope,'event_id':'new','at':123,'attention':'[important]'}
        parts=[{'type':'text','text':'hello'},{'type':'image_url','image_url':{'url':'data:image/png;base64,YQ==','detail':'high'}}]
        before=copy.deepcopy(parts);a=event_message(origin,parts);b=event_message(dict(reversed(list(origin.items()))),parts)
        self.assertEqual(model_json(a),model_json(b));self.assertEqual(parts,before)
        self.assertEqual(a['content'][1],parts[1]);self.assertEqual(source(a)['attention'],'[important]')
    def test_unsettled_unknown_or_missing_binding_cannot_cover(self):
        a=message(self.scope,'a');self.stage(a,[a])
        self.j.append_completion({'role':'assistant','content':'draft'},model_origin=(self.scope,'a','12345678'))
        self.assertFalse(material.coverage_eligible(material.snapshots(self.j.last.base,'bot_shared',db=self.db)[0],'bot_shared'))
        self.settle('a','unknown',1,0)
        self.assertFalse(material.coverage_eligible(material.snapshots(self.j.last.base,'bot_shared',db=self.db)[0],'bot_shared'))

    def test_material_metadata_refresh_keeps_storage_redaction(self):
        a=message(self.scope,'a',text='test sk-12345678901234567890')
        self.stage(a,[a])
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',('bot_shared','12345678',model_json([self.scope,'a']),'input','',1))
        self.db.commit()
        self.j.append_completion({'role':'assistant','content':'Bearer fixture-secret'},model_origin=(self.scope,'a','12345678'))
        self.settle('a','sent',1,1)
        for _ in range(2):material.remember(self.db,'bot_shared',material.snapshots(self.j.last.base,'bot_shared',db=self.db))
        raw=self.db.execute('SELECT material FROM memory_round_material').fetchone()[0]
        self.assertNotIn('sk-12345678901234567890',raw)
        self.assertNotIn('fixture-secret',raw)
        self.assertIn('凭据已移除',raw)

    def test_covered_prefix_and_already_submitted_event_defer_retirement(self):
        a=message(self.scope,'a');b=message(self.scope,'b');c=message(self.scope,'c')
        self.stage(a,[a,b,c])
        self.j.append_completion({'role':'assistant','content':'answer C'},model_origin=(self.scope,'c','12345678'))
        self.settle('a');self.settle('b');self.settle('c','sent',1,1)
        old=copy.deepcopy(self.j.last.wire)
        item=material.snapshots(self.j.last.base,'bot_shared',db=self.db)[1]
        self.assertTrue(material.coverage_eligible(item,'bot_shared'))
        proof={item['event']:{'subject':item['subject'],'digest':item['digest'],'reply_digest':material.coverage_digest(item).split(':')[2]}}
        full,_=shared.compose(self.j,request(c),self.scope,'c')
        projected=material.project_history(full,proof,'bot_shared',model_json([self.scope,'c']))
        self.assertLess(len(projected),len(full))
        from readonly_journal import ContextReconstructionDenied
        with self.assertRaises(ContextReconstructionDenied):self.j.prepare(projected,'same-system',strict=True,guarded=True)
        candidate=self.j.prepare(projected,'same-system',strict=True,guarded=True,unprojected=full)
        self.assertEqual(candidate.wire[:len(old)],old)
        self.assertTrue(self.j.commit(candidate))
        self.assertEqual(ReadonlyJournal(self.db,'bot_shared').last.wire[:len(old)],old)

if __name__=='__main__':unittest.main()
