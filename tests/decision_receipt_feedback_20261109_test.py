"""Stateless judgments retain actual settlements and sourced human corrections."""
import hashlib,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
import shared_context as shared
from context_protocol import SOURCE,model_json
from companion_insights import snapshot,apply
from decision_history import changes,apply_changes
import bot_pipeline,message_provenance


class Receipts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'bot.sqlite');self.db=self.store.db
        self.now=time.time();self.scope='group:55555';self.person='12345678'
        self.message={'role':'user','content':SOURCE+model_json({'scope':self.scope,'event_id':'answer','sender_id':self.person,'at':self.now})+'\nrequest'}
        self.journal=NS(last=NS(base=[self.message]))
        self.db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,attempted,confirmed) VALUES(?,?,?,?,'message','commit','unknown','',1,0)",(self.scope,self.person,'answer',self.now))
        self.db.execute("INSERT INTO dialogue_feedback VALUES(?,?,?,?,?,'observing')",(self.scope,'answer',self.person,self.now,'actual answer'))
        self.payload=[{'event':'answer','sender_id':self.person,'user':'request','bot':'actual answer','at':self.now},
                      {'event':'reaction','sender_id':self.person,'user':'这个说法不对，请更正','bot':'','at':self.now+1}]
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def feedback(self,state='corrected',before=None,payload=None):
        apply(self.db,self.store.memory,self.scope,{},payload or self.payload,{'effects':[{'event':'answer','reaction_event':'reaction','result':state,'quote':'这个说法不对'}]},before or snapshot(self.db,self.scope,{self.person}),self.now+2)
    def test_feedback_does_not_upgrade_unknown_and_late_confirmation_keeps_correction(self):
        self.feedback();old=shared.delivery_view(self.db,self.journal)
        self.assertEqual(old[0]['delivery'],'unknown');self.assertEqual(old[0]['feedback']['state'],'corrected')
        self.assertEqual(old[0]['feedback']['reaction_event'],'reaction')
        self.assertEqual(old[0]['feedback']['sender_id'],self.person)
        self.db.execute("UPDATE pipeline_events SET status='sent',confirmed=1")
        message_provenance.put(self.db,self.scope,'answer','outgoing',{'receipt':'confirmed','message_ids':['qq-real'],'complete':True})
        new=shared.delivery_view(self.db,self.journal)
        self.assertEqual(new[0]['delivery'],'confirmed');self.assertEqual(new[0]['feedback'],old[0]['feedback'])
        self.assertEqual(apply_changes(old,changes(old,new)),new)
    def test_late_reflection_and_older_reaction_cannot_replace_new_feedback(self):
        before=snapshot(self.db,self.scope,{self.person});self.feedback('rejected',before)
        self.feedback('corrected',before)
        self.assertEqual(shared.delivery_view(self.db,self.journal)[0]['feedback']['state'],'rejected')
        older=[dict(x) for x in self.payload];older[-1]['at']=self.now-1
        self.feedback('corrected',payload=older)
        self.assertEqual(shared.delivery_view(self.db,self.journal)[0]['feedback']['state'],'rejected')
    def test_untrusted_quote_is_not_saved(self):
        self.payload[-1]['source']={'sender_id':'different-author'};self.feedback()
        self.assertNotIn('feedback',shared.delivery_view(self.db,self.journal)[0])
    def test_newest_valid_reaction_in_batch_wins(self):
        self.payload.append({'event':'later','sender_id':self.person,'user':'我不同意这个解释','bot':'','at':self.now+2})
        data={'effects':[{'event':'answer','reaction_event':'reaction','result':'corrected','quote':'这个说法不对'},
                         {'event':'answer','reaction_event':'later','result':'rejected','quote':'我不同意这个解释'}]}
        apply(self.db,self.store.memory,self.scope,{},self.payload,data,snapshot(self.db,self.scope,{self.person}),self.now+3)
        feedback=shared.delivery_view(self.db,self.journal)[0]['feedback']
        self.assertEqual(feedback['reaction_event'],'later');self.assertEqual(feedback['state'],'rejected')
    def test_auxiliary_uses_complete_fixed_foundation_without_summary_search(self):
        scope='private:'+self.person
        for level,text in [('big','完整大摘要'*90),('small','较早小摘要'),('small','较新小摘要')]:
            row=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(scope,text,'[]',self.now))
            self.db.execute('INSERT INTO memory_summary_levels VALUES(?,?)',(row.lastrowid,level))
        result=bot_pipeline.decision_memory(self.db,{'memory':True},scope,self.person,self.now)
        basis=result['memory_foundation']['conversation_summaries']
        self.assertEqual([x['level'] for x in basis],['big','small','small'])
        self.assertEqual(basis[0]['summary'],'完整大摘要'*90)
        self.assertNotIn('summaries',result)
    def test_stateless_helpers_repeat_required_status_and_include_current_feedback(self):
        cfg={'memory':True,'memorySummary':False,'ownerUin':self.person,'groupParticipationGroups':['55555']}
        self.db.commit()
        shared.activate_authorized(self.db,cfg,{},'test fixture','f'*40)
        self.db.execute('CREATE TABLE IF NOT EXISTS fishbot_readonly_journals(scope TEXT PRIMARY KEY,version INTEGER,payload TEXT,sha TEXT)')
        payload=model_json({'scope':model_json(shared.JOURNAL_SCOPE),'base':[self.message]})
        self.db.execute('INSERT INTO fishbot_readonly_journals(scope,version,payload,sha) VALUES(?,1,?,?)',(model_json(shared.JOURNAL_SCOPE),payload,hashlib.sha256(payload.encode()).hexdigest()))
        self.feedback('rejected')
        one=bot_pipeline.decision_memory(self.db,cfg,'private:'+self.person,self.person,self.now)
        two=bot_pipeline.decision_memory(self.db,cfg,'private:'+self.person,self.person,self.now)
        self.assertEqual(one['cross_scope_deliveries'],two['cross_scope_deliveries'])
        self.assertEqual(two['cross_scope_deliveries'][0]['feedback']['state'],'rejected')
        same=bot_pipeline.decision_memory(self.db,cfg,self.scope,self.person,self.now)
        self.assertEqual(same['current_scope_feedback'][0]['feedback']['state'],'rejected')
        self.assertNotIn('confirmed_text',same['current_scope_feedback'][0])
        self.db.execute("UPDATE pipeline_events SET status='sent',confirmed=1")
        message_provenance.put(self.db,self.scope,'answer','outgoing',{'receipt':'confirmed','message_ids':['qq-real'],'complete':True})
        payload=model_json({'scope':model_json(shared.JOURNAL_SCOPE),'base':[self.message,{'role':'assistant','content':'actual answer'}]})
        self.db.execute('UPDATE fishbot_readonly_journals SET payload=?,sha=?',(payload,hashlib.sha256(payload.encode()).hexdigest()))
        compact=bot_pipeline.decision_memory(self.db,cfg,'private:'+self.person,self.person,self.now)
        receipt=compact['cross_scope_deliveries'][0]
        self.assertNotIn('confirmed_text',receipt)
        self.assertEqual(receipt['delivery'],'confirmed')
        self.assertEqual(compact['recent_cross_session_messages'][receipt['confirmed_text_ref'][1]]['text'],'actual answer')


if __name__=='__main__':unittest.main()
