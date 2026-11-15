"""Only new group background is windowed; adopted wire stays immutable."""
import copy,json,sqlite3,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import shared_context as shared
from context_protocol import event_message,source,model_json


class GroupQueueWindow(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');shared.initialize(self.db)
        self.cfg={'groupParticipationGroups':['12345','23456']}
        self.j=NS(last=None,_staged=None)
        self.messages={}
    def tearDown(self):self.db.close()
    def add(self,scope,event):
        message=event_message({'scope':scope,'event_id':str(event),'sender_id':'12345678','at':1},'body '+str(event))
        self.db.execute('INSERT INTO fishbot_shared_inputs(scope,event,message,created) VALUES(?,?,?,?)',(scope,str(event),model_json(message),1))
        self.db.commit();self.messages[(scope,str(event))]=message;return message
    def keys(self,messages):return [(source(m)['scope'],source(m)['event_id']) for m in messages]
    def count(self):return self.db.execute('SELECT count(*) FROM fishbot_shared_inputs').fetchone()[0]
    def test_outstanding_directed_input_survives_background_window_and_restart(self):
        from bot_pipeline import BotPipeline
        pipeline=BotPipeline(self.db)
        for n in range(20):self.add('group:12345',n)
        self.db.execute("INSERT INTO pipeline_work_inputs(scope,event,status,addressed_self,seen) VALUES('group:12345','1','owned',1,0)")
        first=shared.queued(self.db,self.cfg,self.j)
        self.assertIn(('group:12345','1'),self.keys(first))
        self.assertEqual(len(first),13,'12 background + required directed input')
        # Even an adopted later window cannot make a missing directed input
        # silently disappear during recovery before the next request.
        self.j.last=NS(base=[self.messages[('group:12345','19')]])
        self.assertIn(('group:12345','1'),self.keys(shared.queued(self.db,self.cfg,self.j)))
    def test_each_group_has_twelve_private_unbounded_and_late_current_inside_quota(self):
        for scope in ('group:12345','group:23456','private:12345678'):
            for n in range(20):self.add(scope,n)
        current=self.messages[('group:12345','0')]
        selected=shared.queued(self.db,self.cfg,self.j,current=('group:12345','0',current))
        keys=self.keys(selected)
        self.assertEqual([e for s,e in keys if s=='group:12345'],['0',*[str(n) for n in range(9,20)]])
        self.assertEqual([e for s,e in keys if s=='group:23456'],[str(n) for n in range(8,20)])
        self.assertEqual(sum(s.startswith('private:') for s,e in keys),20)
        self.assertEqual(self.count(),60,'selection does not delete before prepare/commit')
    def test_failed_or_staged_candidate_cannot_reclaim_omitted_rows(self):
        for n in range(20):self.add('group:12345',n)
        selected=shared.queued(self.db,self.cfg,self.j)
        shared.settle_inputs(self.db,self.j)
        self.assertEqual(self.count(),20)
        self.j.last=NS(base=copy.deepcopy(selected));self.j._staged=object()
        shared.settle_inputs(self.db,self.j)
        self.assertEqual(self.count(),20,'staged view cannot prove the omitted rows handed over')
        self.j._staged=None;shared.settle_inputs(self.db,self.j)
        self.assertEqual(self.count(),0)
    def test_adoption_reclaims_old_queue_not_new_arrivals_or_business_evidence(self):
        self.db.execute('CREATE TABLE memory_pending(scope,event,question)')
        self.db.execute("INSERT INTO memory_pending VALUES('group:12345','0','retained business input')")
        for n in range(20):self.add('group:12345',n)
        selected=shared.queued(self.db,self.cfg,self.j)
        self.add('group:12345',20)
        self.j.last=NS(base=copy.deepcopy(selected));before=copy.deepcopy(self.j.last.base)
        shared.settle_inputs(self.db,self.j)
        self.assertEqual(self.keys(shared.queued(self.db,self.cfg,self.j)),[('group:12345','20')])
        self.assertEqual(self.db.execute('SELECT question FROM memory_pending').fetchone()[0],'retained business input')
        self.assertEqual(self.j.last.base,before)
    def test_committed_current_tool_loop_is_not_counted_or_repeated(self):
        committed=self.add('group:12345','old');self.j.last=NS(base=[committed])
        for n in range(20):self.add('group:12345',n)
        selected=shared.queued(self.db,self.cfg,self.j,current=('group:12345','old',committed))
        self.assertEqual(len(selected),12);self.assertNotIn(('group:12345','old'),self.keys(selected))
        self.j.last.base.extend(selected);shared.settle_inputs(self.db,self.j)
        self.assertEqual(shared.queued(self.db,self.cfg,self.j),[])
        self.assertEqual(len(self.j.last.base),13)

    def test_restart_after_real_journal_adoption_does_not_replay_omitted_background(self):
        from readonly_journal import ReadonlyJournal
        from context_protocol import READONLY_CURRENT
        from context_transport import EVENT_CONTEXT_CURRENT
        journal=ReadonlyJournal(self.db,'bot_shared')
        for n in range(20):self.add('group:12345',n)
        selected=shared.queued(self.db,self.cfg,journal)
        messages=[{'role':'system','content':'stable'},
                  {'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({'authority':{},'policy':{},'currentGuidance':'','meaning':'platform'})},*selected,
                  {'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{}})}]
        prepared=journal.prepare(messages,'same',strict=True)
        self.assertTrue(journal.stage_candidate(prepared))
        # Staging writes crash recovery, but only adoption is handed over here.
        shared.settle_inputs(self.db,journal);self.assertEqual(self.count(),20)
        self.assertTrue(journal.commit(prepared));prefix=model_json(journal.last.wire)
        restarted=ReadonlyJournal(self.db,'bot_shared')
        self.assertEqual(shared.queued(self.db,self.cfg,restarted),[])
        shared.settle_inputs(self.db,restarted)
        self.assertEqual(self.count(),0)
        self.assertEqual(model_json(restarted.last.wire),prefix)

    def test_window_commit_settles_excluded_custody_without_model_or_send_claim(self):
        from bot_pipeline import BotPipeline
        import message_provenance
        BotPipeline(self.db);message_provenance.initialize(self.db)
        self.db.execute("INSERT INTO pipeline_scope_work VALUES('group:12345','19','person','runtime',1)")
        for n in range(20):
            self.add('group:12345',n)
            self.db.execute("INSERT INTO pipeline_work_inputs(scope,event,owner,status,seen,addressed_self,created,payload) VALUES('group:12345',?,'19','owned',0,0,?,'original')",(str(n),n))
        # A prior window lost its display row but left this same custody.
        for event,created,addressed in [('orphan',-1,0),('late',21,0),('directed',0,1),('unknown',0,None)]:
            self.db.execute("INSERT INTO pipeline_work_inputs(scope,event,owner,status,seen,addressed_self,created,payload) VALUES('group:12345',?,'19','owned',0,?,?,'original')",(event,addressed,created))
        self.db.execute("INSERT INTO pipeline_action_effects VALUES('2',1,'send','target','unknown',NULL,1)")
        message_provenance.bind_model_input(self.db,'bot_shared','group:12345','3','12345678',self.messages[('group:12345','3')])
        value={'source_scope':'group:12345','event_id':'3','sender_id':'12345678','outputs':[{'digest':'actual draft'}]}
        self.db.execute("UPDATE memory_message_sources SET data=? WHERE direction='model'",(model_json(value),));self.db.commit()
        selected=shared.queued(self.db,self.cfg,self.j)
        # Failed/staged candidate cannot release any responsibility.
        shared.settle_inputs(self.db,self.j)
        self.assertEqual(self.db.execute("SELECT count(*) FROM pipeline_work_inputs WHERE status='settled_window'").fetchone()[0],0)
        self.j.last=NS(base=copy.deepcopy(selected));before=copy.deepcopy(self.j.last.base)
        shared.settle_inputs(self.db,self.j)
        settled={e for e, in self.db.execute("SELECT event FROM pipeline_work_inputs WHERE status='settled_window'")}
        self.assertEqual(settled,{'0','1','4','5','6','7','orphan'})
        for event in ('2','3','late','directed','unknown','19'):
            self.assertEqual(self.db.execute('SELECT status FROM pipeline_work_inputs WHERE event=?',(event,)).fetchone()[0],'owned')
        self.assertEqual(self.j.last.base,before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM pipeline_events').fetchone()[0],0)
        shared.settle_inputs(self.db,self.j)
        self.assertEqual(self.db.execute("SELECT count(*) FROM pipeline_work_inputs WHERE status='settled_window'").fetchone()[0],7)


if __name__=='__main__':unittest.main()
