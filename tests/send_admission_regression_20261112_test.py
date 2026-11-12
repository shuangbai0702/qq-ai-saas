"""Backlog is not a veto on a suitable draft or a reason for identical calls."""
import json,sqlite3,sys,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from bot_pipeline import BotPipeline
from context_protocol import event_message


class SendAdmission(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.p=BotPipeline(self.db)
        extra={}
        e=NS(get_group_id=lambda:'55555',get_sender_id=lambda:'12345',get_self_id=lambda:'99999',
             get_platform_id=lambda:'fixture',get_extra=lambda k,d=None:extra.get(k,d),
             set_extra=lambda k,v:extra.update({k:v}),message_obj=NS(message_id='root',timestamp=time.time()))
        self.work=self.p.bind(e,{'botId':'fixture','generation':1})
    def tearDown(self):self.db.close()
    def queued(self,event,raw=None,scope='group:55555',status='owned'):
        raw=raw or {'post_type':'message','message_id':event,'user_id':'12345','message_type':'group','group_id':'55555','message':[]}
        self.db.execute('INSERT INTO pipeline_work_inputs(scope,event,subject,owner,payload,status,created) VALUES(?,?,?,?,?,?,?)',
                        (scope,event,'12345','root',json.dumps(raw),status,time.time()));self.db.commit()
    def message(self,event):
        return event_message({'scope':'group:55555','event_id':event,'sender_id':'12345','at':1},'input')
    def test_23_old_messages_and_3_reactions_do_not_cause_repeated_model_calls(self):
        for i in range(23):self.queued('old'+str(i))
        for i in range(3):self.queued('reaction'+str(i),{'post_type':'notice','notice_type':'group_msg_emoji_like'})
        self.assertEqual(self.p.unseen_inputs(self.work),26,'reactions remain observable inputs, not filtered notices')
        self.p.request_inputs(self.work,[self.message('root')])
        self.assertEqual(self.p.input_continuation(self.work),'inputs_not_adopted')
        self.assertEqual(self.db.execute("SELECT count(*) FROM pipeline_work_inputs WHERE event LIKE 'old%' AND seen=0 AND status='owned'").fetchone()[0],23)
        # A retry or another provider tool step with no new real input cannot
        # turn a clock-only delta into fresh processing permission.
        self.p.request_inputs(self.work,[self.message('root')])
        self.assertEqual(self.p.input_continuation(self.work),'inputs_not_adopted')
    def test_new_real_input_after_request_gets_a_pass_without_replaying_old_backlog(self):
        self.queued('old');self.p.request_inputs(self.work,[self.message('root')])
        self.queued('new')
        self.assertEqual(self.p.input_continuation(self.work),'continue_inputs')
        self.p.request_inputs(self.work,[self.message('root'),self.message('new')])
        self.p.seen_inputs(self.work,[self.message('root'),self.message('new')])
        self.assertEqual(self.p.input_continuation(self.work),'inputs_not_adopted')
        self.assertEqual(self.db.execute("SELECT seen FROM pipeline_work_inputs WHERE event='new'").fetchone()[0],1)
    def test_other_scope_and_held_unknown_never_become_new_inputs(self):
        self.queued('other',scope='group:66666');self.queued('unknown',status='blocked_unknown')
        self.p.request_inputs(self.work,[self.message('root')])
        self.assertEqual(self.p.input_continuation(self.work),'inputs_decided')
        self.assertEqual(self.db.execute("SELECT status FROM pipeline_work_inputs WHERE event='unknown'").fetchone()[0],'blocked_unknown')
    def test_reaction_is_retained_and_not_treated_as_transport_noise(self):
        from bot_pipeline import is_transport_notification
        self.assertFalse(is_transport_notification({'post_type':'notice','notice_type':'group_msg_emoji_like'}))
        self.queued('reaction',{'post_type':'notice','notice_type':'group_msg_emoji_like'},status='blocked_recovery')
        self.queued('unknown',{'post_type':'notice','notice_type':'group_msg_emoji_like'},status='blocked_unknown')
        self.p.retire_transport_notifications()
        self.assertEqual(dict(self.db.execute('SELECT event,status FROM pipeline_work_inputs')),
                         {'reaction':'blocked_recovery','unknown':'blocked_unknown'})


if __name__=='__main__':unittest.main()
