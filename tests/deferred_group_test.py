"""Cooldown expiry consumes genuine pending input through the ordinary decision path."""
import asyncio,contextvars,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
import autonomy,initiative


class DeferredGroup(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'behavior.sqlite';self.store=BehaviorStore(self.path);self.db=self.store.db;self.now=time.time()
        self.cfg={'proactive':False,'behavior':{'groupParticipationEnabled':True,'groupParticipationGroups':['12345','67890'],'groupParticipationIntervalMinutes':10,'hourlyInterjectionLimit':20,'quietStart':0,'quietEnd':0,'dailyPlanLimit':0,'dailyProactiveLimit':0}}
        self.var=contextvars.ContextVar('deferred',default=None);self.metrics={};self.calls=[]
        async def chat(**kwargs):self.calls.append(kwargs);return NS(completion_text=json.dumps({'reply':True,'target':'bot','participation':'direct','information':'substantive','interest':'strong','reason_code':'direct_request','confidence':'high'}))
        self.provider=NS(text_chat=chat)
        self.db.execute("INSERT INTO autonomy_groups(scope,checked,event) VALUES('group:12345',?,'previous')",(self.now,));self.db.commit()
    def tearDown(self):self.store.close();self.temp.cleanup()
    def defer(self,event='actual',at=None,scope='group:12345',text='真实消息',**changed):
        at=self.now+100 if at is None else at
        raw={'post_type':'message','message_type':'group','message_id':event,'group_id':scope[6:],'user_id':'11111111','self_id':'99999999','message':[{'type':'text','data':{'text':text}}]};raw.update(changed)
        return self.store.presence.defer(scope,self.cfg,event,raw,'11111111','99999999',text,at)
    def pending(self,now):return self.store.presence.pending(self.cfg,self.provider,lambda:True,now)
    async def test_expiry_rechecks_without_a_new_message_or_generic_budget(self):
        self.assertTrue(self.defer());self.assertIsNone(self.pending(self.now+599))
        item=self.pending(self.now+600);self.assertEqual(item['event'],'actual')
        self.assertTrue(self.store.presence.claim(item,self.cfg,self.provider,lambda:True,self.now+600))
        self.assertFalse(self.store.presence.claim(item,self.cfg,self.provider,lambda:True,self.now+600))
        self.assertIsNone(self.pending(self.now+605))
        with patch('autonomy.time.time',return_value=self.now+600):
            result=await self.store.presence.evaluate(item['scope'],self.cfg,item['event'],self.provider,[{'sender_id':'11111111','text':'真实消息'}],{},{},lambda:True,self.metrics,self.var,self.now+600)
        self.assertTrue(result[0].reply);self.assertEqual(len(self.calls),1)
        self.assertIsNone(self.pending(self.now+1200),'time alone must not recheck old input')
        self.assertEqual(self.db.execute('SELECT count(*) FROM autonomy_group_inbox').fetchone()[0],0)
    def test_latest_text_wins_but_nontext_does_not_erase_it(self):
        self.defer('first');self.defer('latest',self.now+200);self.assertFalse(self.defer('face',self.now+300,text=''))
        self.assertEqual(self.pending(self.now+600)['event'],'latest')
        self.assertFalse(self.defer('forged',self.now+400,user_id='22222222'))
        self.assertEqual(self.pending(self.now+600)['event'],'latest')
    def test_real_interaction_invalidates_pending_in_only_that_group(self):
        self.defer();self.defer('other',scope='group:67890')
        autonomy.note_interaction(self.db,self.cfg,'group:12345','11111111',self.now+200)
        self.assertEqual(self.pending(self.now+600)['scope'],'group:67890')
        self.assertIsNone(self.db.execute("SELECT 1 FROM autonomy_group_inbox WHERE scope='group:12345'").fetchone())
    def test_restart_preserves_pending_and_expired_queue_claim_is_recoverable(self):
        self.defer();item=self.pending(self.now+600);self.store.presence.claim(item,self.cfg,self.provider,lambda:True,self.now+600)
        self.store.close();self.store=BehaviorStore(self.path);self.db=self.store.db
        self.assertIsNone(self.pending(self.now+629));self.assertEqual(self.pending(self.now+630)['event'],'actual')
        self.assertEqual(self.db.execute("SELECT checked FROM autonomy_groups WHERE scope='group:12345'").fetchone()[0],self.now)
    def test_config_change_new_input_and_manual_pause_fence_late_claim(self):
        self.defer();item=self.pending(self.now+600)
        self.assertFalse(self.store.presence.claim(item,self.cfg,self.provider,lambda:False,self.now+600))
        self.cfg['behavior']['groupParticipationGroups']=[];self.assertFalse(self.store.presence.claim(item,self.cfg,self.provider,lambda:True,self.now+600))
        self.cfg['behavior']['groupParticipationGroups']=['12345'];self.defer('replacement',self.now+200)
        self.assertFalse(self.store.presence.claim(item,self.cfg,self.provider,lambda:True,self.now+600))
        self.store.presence.discard(item);self.assertEqual(self.pending(self.now+600)['event'],'replacement','late discard cannot erase newer real input')
        initiative.incoming(self.db,'group:12345','11111111','99999999','真实消息',self.now+200,directed=False)
        self.db.execute("UPDATE contacts SET opted_out=1 WHERE scope='group:12345'");self.db.commit();self.assertIsNone(self.pending(self.now+600))
    def test_input_is_bounded_and_does_not_cross_bot_or_protocol_identity(self):
        self.assertFalse(self.defer(text='x'*17000));self.assertFalse(self.defer(group_id='67890'))
        self.defer();other=BehaviorStore(Path(self.temp.name)/'other.sqlite')
        try:self.assertIsNone(other.presence.pending(self.cfg,self.provider,lambda:True,self.now+600))
        finally:other.close()


if __name__=='__main__':unittest.main(verbosity=2)
