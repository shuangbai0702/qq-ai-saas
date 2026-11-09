"""An accepted human request survives config saves through a current lease."""
import copy,json,sqlite3,sys,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from bot_pipeline import BotPipeline


class RequestRevision(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.pipeline=BotPipeline(self.db)
        self.data={'tenantId':'tenant','botId':'bot','generation':1,'revision':1,'at':int(time.time()*1000)-1000,'delaySeconds':0,'eventId':'event','scope':'private:12345678','senderId':'12345678'}
        self.original=json.dumps(self.data)
        self.db.execute("INSERT INTO pipeline_handoffs VALUES('goal',1,?,'accepted',?,?)",(self.original,json.dumps({'status':'accepted','goalId':'goal'}),time.time()))
        self.source={'handoffId':'goal','eventId':'event','scope':'private:12345678','senderId':'12345678'}
        self.current={k:self.data[k] for k in ('tenantId','botId','generation','revision')};self.current['revision']=2
    def tearDown(self):self.db.close()
    def reply(self,identity=None,source=None,lease=None,action='next',reserve=False):
        return self.pipeline.request_reply(identity or self.current,'goal',source or self.source,action,reserve,current_lease=lease)
    def test_new_revision_needs_exact_current_lease_and_preserves_original_request(self):
        self.assertIsNone(self.reply())
        self.assertIsNone(self.reply(lease={**self.current,'revision':1}))
        self.assertIsNone(self.reply(lease={**self.current,'botId':'other'}))
        self.assertEqual(self.reply(lease=self.current),self.data)
        self.assertEqual(self.db.execute("SELECT data FROM pipeline_handoffs WHERE id='goal'").fetchone()[0],self.original)
        self.assertNotIn('executionIdentity',json.loads(self.db.execute("SELECT receipt FROM pipeline_handoffs WHERE id='goal'").fetchone()[0]))
    def test_original_identity_and_source_cannot_be_reassigned(self):
        for key,value in [('tenantId','other'),('botId','other'),('generation',2)]:
            with self.subTest(key=key):
                changed={**self.current,key:value}
                self.assertIsNone(self.reply(changed,lease=changed))
        for key,value in [('scope','private:87654321'),('senderId','87654321'),('eventId','other'),('handoffId','other'),('revision',2),('generation',2)]:
            with self.subTest(source_key=key):self.assertIsNone(self.reply(source={**self.source,key:value},lease=self.current))
    def test_model_origin_never_acquires_solicited_reply_authority(self):
        self.assertIsNone(self.reply(source={**self.source,'origin':'model'},lease=self.current))
        self.db.execute("UPDATE pipeline_handoffs SET data=?",(json.dumps({**self.data,'origin':'model'}),))
        self.assertIsNone(self.reply(lease=self.current))
    def test_unknown_or_confirmed_action_never_replays_across_another_config_save(self):
        self.assertTrue(self.reply(lease=self.current,action='first',reserve=True))
        self.db.execute("INSERT INTO pipeline_actions VALUES('first','fingerprint',1,'private:12345678',?,'unknown',?,?)",(time.time()+100,json.dumps({'status':'unknown'}),time.time()))
        self.db.execute("INSERT INTO pipeline_action_effects VALUES('first',0,'send_private_msg','private:12345678','unknown',NULL,?)",(time.time(),))
        later={**self.current,'revision':3}
        self.assertIsNone(self.reply(later,lease=later,action='next'))
        self.db.execute("UPDATE pipeline_action_effects SET state='confirmed',receipt='qq-real'")
        self.assertIsNone(self.reply(later,lease=later,action='next'))
        self.assertTrue(self.reply(later,lease=later,action='first'))
    def test_cancellation_and_due_window_still_apply(self):
        self.db.execute("UPDATE pipeline_handoffs SET state='canceled'")
        self.assertIsNone(self.reply(lease=self.current))
        future={**self.data,'at':int(time.time()*1000)+100000}
        self.db.execute("UPDATE pipeline_handoffs SET state='accepted',data=?",(json.dumps(future),))
        self.assertIsNone(self.reply(lease=self.current))


if __name__=='__main__':unittest.main()
