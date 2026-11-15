"""Real SDK components; authenticated redelivery reuses existing input only."""
import copy,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot'),str(ROOT/'tests')]
from astrbot.core.message.components import Image,Plain
from behavior import BehaviorStore
from readonly_journal import ReadonlyJournal
from context_protocol import model_json
from unified_context_20261108_test import request
import shared_context as shared

class Redelivery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'bot.sqlite'
        self.store=BehaviorStore(self.path);self.db=self.store.db
        shared.initialize(self.db)
        self.cfg={'behavior':{'ownerUin':'12345678','groupParticipationGroups':['12345']}}
        self.lease={'tenantId':'tenant','botId':'bot','generation':1,'revision':1}
        self.raw={'post_type':'message','message_id':'101','user_id':'12345678','self_id':'99999','group_id':'12345','message_type':'group','time':123,'message':[{'type':'image','data':{'url':'https://original/image'}}]}
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def event(self,url='https://first/image',at=123,raw=None,sender='12345678'):
        extra={};parts=[Plain('image'),Image(file=url,url=url)]
        return NS(get_group_id=lambda:'12345',get_sender_id=lambda:sender,
          get_messages=lambda:parts,get_self_id=lambda:'99999',get_extra=lambda k,d=None:extra.get(k,d),
          set_extra=lambda k,v:extra.update({k:v}),message_obj=NS(message_id='101',timestamp=at,raw_message=copy.deepcopy(raw or self.raw)))
    def first(self):
        event=self.event();self.assertTrue(self.store.pipeline.persist_input(event,self.lease))
        self.assertTrue(shared.observe(self.db,event,self.cfg))
        return self.db.execute('SELECT message FROM fishbot_shared_inputs').fetchone()[0],event.get_extra('_hosted_shared_input_digest')
    def test_redelivery_transient_media_and_sdk_time_do_not_rebind_or_append(self):
        original,digest=self.first();again=self.event('https://second/image',999)
        self.assertTrue(shared.observe(self.db,again,self.cfg))
        self.assertEqual(again.get_extra('_hosted_shared_input_digest'),digest)
        self.assertEqual(self.db.execute('SELECT message FROM fishbot_shared_inputs').fetchall(),[(original,)])
    def test_restart_reuses_committed_journal_without_recreating_queue(self):
        original,digest=self.first();journal=ReadonlyJournal(self.db,'bot_shared')
        m=json.loads(original);p=journal.prepare(request(m),'fixed',strict=True)
        self.assertTrue(journal.stage_candidate(p));self.assertTrue(journal.commit(p))
        shared.settle_inputs(self.db,journal)
        before=self.db.execute('SELECT * FROM fishbot_readonly_journals').fetchall()
        self.store.close();self.store=BehaviorStore(self.path);self.db=self.store.db
        again=self.event('https://second/image',999)
        self.assertTrue(shared.observe(self.db,again,self.cfg))
        self.assertEqual(again.get_extra('_hosted_shared_input_digest'),digest)
        self.assertEqual(self.db.execute('SELECT * FROM fishbot_shared_inputs').fetchall(),[])
        self.assertEqual(self.db.execute('SELECT * FROM fishbot_readonly_journals').fetchall(),before)
    def test_changed_raw_or_sender_does_not_gain_original_input_permission(self):
        self.first();raw=copy.deepcopy(self.raw);raw['message'][0]['data']['url']='https://different/image'
        for event in (self.event(raw=raw),self.event(sender='87654321')):
            with self.assertRaisesRegex(ValueError,'protocol_input_changed'):
                shared.observe(self.db,event,self.cfg)
    def test_missing_original_is_not_recreated_from_a_changed_sdk_view(self):
        self.first();self.db.execute('DELETE FROM fishbot_shared_inputs');self.db.commit()
        with self.assertRaisesRegex(ValueError,'protocol_input_changed'):
            shared.observe(self.db,self.event('https://second/image',999),self.cfg)

if __name__=='__main__':unittest.main()
