import json,sqlite3,sys,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from bot_pipeline import BotPipeline,waiting_reconciliation_plan
from autonomy import GroupPresence

class WaitingReconciliation(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.p=BotPipeline(self.db)
        self.db.execute('CREATE TABLE autonomy_group_inbox(scope TEXT PRIMARY KEY,event TEXT,payload TEXT,at REAL,queued REAL)')
        self.db.execute('CREATE TABLE autonomy_groups(scope TEXT PRIMARY KEY,checked REAL,interaction REAL,version INTEGER,event TEXT)')
    def tearDown(self):self.db.close()
    def waiting(self,event='old',**kw):
        row={'scope':'group:12345','subject':'12345678','event':event,'at':1,'source':'message','stage':'admission','status':'waiting','reason':'group_waiting','attempted':0,'confirmed':0,'updated_at':1,**kw}
        self.db.execute('INSERT INTO pipeline_events('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()));self.db.commit()
    def test_settled_children_close_activation_without_claiming_a_send(self):
        self.waiting()
        self.db.execute("INSERT INTO pipeline_work_inputs(scope,event,owner,status,seen) VALUES('group:12345','joined','old','settled',1)")
        self.assertEqual(self.p.reconcile_waiting(),1)
        self.assertEqual(self.db.execute('SELECT status,reason,attempted,confirmed FROM pipeline_events').fetchone(),('superseded','waiting_inputs_settled',0,0))
        self.assertEqual(self.db.execute('SELECT status,seen FROM pipeline_work_inputs').fetchone(),('settled',1))
        self.assertEqual(self.p.reconcile_waiting(),0)
    def test_missing_all_responsibility_closes_honestly_not_as_handled(self):
        self.waiting();self.assertEqual(self.p.reconcile_waiting(),1)
        self.assertEqual(self.db.execute('SELECT status,reason FROM pipeline_events').fetchone(),('blocked','waiting_responsibility_missing'))
    def test_active_inbox_unknown_and_directed_unfinished_are_protected(self):
        for n in range(5):self.waiting(str(n),scope='group:'+str(12345+n))
        self.db.execute("INSERT INTO pipeline_scope_work VALUES('group:12345','0','person','owner',1)")
        self.db.execute("INSERT INTO autonomy_group_inbox VALUES('group:12346','1','{}',1,0)")
        for scope,event,status,addr in [('group:12347','2','blocked_unknown',0),('group:12348','3','deferred',1),('group:12349','4','deferred',0)]:
            self.db.execute('INSERT INTO pipeline_work_inputs(scope,event,status,addressed_self,seen) VALUES(?,?,?,?,0)',(scope,event,status,addr))
        self.assertEqual(self.p.reconcile_waiting(),1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM pipeline_events WHERE status='waiting'").fetchone()[0],4)
        self.assertEqual(self.db.execute("SELECT status,reason FROM pipeline_events WHERE event='2'").fetchone(),('blocked','waiting_input_blocked'))
        self.assertEqual(self.db.execute("SELECT status FROM pipeline_work_inputs WHERE event='2'").fetchone(),('blocked_unknown',))
    def test_blocked_inputs_close_only_activation_and_never_become_processed(self):
        self.waiting()
        self.db.execute("INSERT INTO pipeline_work_inputs(scope,event,owner,status,seen,payload) VALUES('group:12345','joined','old','blocked_recovery',0,'real-body')")
        self.db.execute("INSERT INTO pipeline_work_inputs(scope,event,owner,status,seen) VALUES('group:12345','other','old','settled',1)")
        before=self.db.execute('SELECT * FROM pipeline_work_inputs').fetchall()
        self.assertEqual(self.p.reconcile_waiting(),1)
        self.assertEqual(self.db.execute('SELECT status,reason FROM pipeline_events').fetchone(),('blocked','waiting_input_blocked'))
        self.assertEqual(self.db.execute('SELECT * FROM pipeline_work_inputs').fetchall(),before)
        self.assertEqual(self.p.reconcile_waiting(),0)
    def test_pending_queued_and_mixed_runnable_inputs_cannot_close(self):
        for n,status in enumerate(('pending','queued','owned','deferred')):
            self.waiting(str(n),scope='group:'+str(12345+n))
            self.db.execute('INSERT INTO pipeline_work_inputs(scope,event,owner,status) VALUES(?,?,?,?)',('group:'+str(12345+n),'input',str(n),status))
            self.db.execute('INSERT INTO pipeline_work_inputs(scope,event,owner,status) VALUES(?,?,?,?)',('group:'+str(12345+n),'blocked',str(n),'blocked_recovery'))
        self.assertEqual(self.p.reconcile_waiting(),0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM pipeline_events WHERE status='waiting'").fetchone(),(4,))
    def test_missing_responsibility_key_survives_cleanup_and_redelivery_stays_blocked(self):
        from types import SimpleNamespace as NS
        self.waiting();self.p.reconcile_waiting()
        extra={}
        event=NS(get_group_id=lambda:'12345',get_sender_id=lambda:'12345678',get_self_id=lambda:'99999',get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.update({k:v}),message_obj=NS(message_id='old',timestamp=time.time()))
        work=self.p.bind(event,{'botId':'bot','generation':1})
        self.assertEqual(work.status,'duplicate');self.assertFalse(work.ledger_owner)
        self.assertEqual(self.db.execute('SELECT status,reason FROM pipeline_events').fetchone(),('blocked','waiting_responsibility_missing'))
    def test_current_journal_and_unsettled_draft_cannot_be_discarded(self):
        from readonly_journal import ReadonlyJournal
        from context_protocol import event_message,READONLY_CURRENT,model_json
        from context_transport import EVENT_CONTEXT_CURRENT
        j=ReadonlyJournal(self.db,'bot_shared')
        message=event_message({'scope':'group:12345','event_id':'old','sender_id':'12345678','at':1},'真实未完成问题')
        messages=[{'role':'system','content':'stable'},
                  {'role':'user','content':EVENT_CONTEXT_CURRENT+'\n'+model_json({'authority':{},'policy':{},'currentGuidance':'','meaning':'platform'})},message,
                  {'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{}})}]
        p=j.prepare(messages,'same',strict=True);self.assertIsNotNone(p);self.assertTrue(j.commit(p))
        before=self.db.execute('SELECT payload,sha FROM fishbot_readonly_journals').fetchall()
        self.waiting();self.assertEqual(self.p.reconcile_waiting(),0)
        self.assertEqual(self.db.execute('SELECT payload,sha FROM fishbot_readonly_journals').fetchall(),before)
        self.waiting('other',scope='group:23456')
        self.db.execute('CREATE TABLE memory_message_sources(scope TEXT,event TEXT,direction TEXT,data TEXT)')
        self.db.execute('INSERT INTO memory_message_sources VALUES(?,?,?,?)',('bot_shared','key','model',json.dumps({'source_scope':'group:23456','event_id':'other'})))
        self.assertEqual(self.p.reconcile_waiting(),0)
    def test_corrupt_journal_hash_protects_waiting_responsibility_without_writes(self):
        from readonly_journal import ReadonlyJournal
        ReadonlyJournal(self.db,'bot_shared')
        payload=json.dumps({'scope':json.dumps('bot_shared'),'base':[],'wire':[]})
        self.db.execute("INSERT INTO fishbot_readonly_journals VALUES(?,1,'ready',?,?,0)",(json.dumps('bot_shared'),payload,'0'*64))
        self.waiting()
        before=self.db.total_changes
        plan=waiting_reconciliation_plan(self.db)
        self.assertEqual(plan['actions'],[])
        self.assertEqual(plan['protected'],{'journal_or_unreadable':1})
        self.assertEqual(self.db.total_changes,before)

    def test_plan_does_not_write_and_dispatch_or_unknown_never_closes(self):
        self.waiting('unsafe',attempted=1);self.waiting('tools',stage='model_tools')
        changes=self.db.total_changes
        self.assertEqual(waiting_reconciliation_plan(self.db)['actions'],[])
        self.assertEqual(self.db.total_changes,changes)
    def test_valid_old_inbox_is_not_deleted_by_age(self):
        cfg={'behavior':{'groupParticipationEnabled':True,'groupParticipationGroups':['12345']}}
        old=time.time()-3*86400
        self.db.execute("INSERT INTO autonomy_group_inbox VALUES('group:12345','old','{}',?,0)",(old,))
        from unittest.mock import patch
        with patch.object(GroupPresence,'ready',return_value=False),patch.object(GroupPresence,'snapshot',return_value=(0,0,0,'')):
            self.assertIsNone(GroupPresence(self.db).pending(cfg,object(),lambda:True,time.time()))
        self.assertEqual(self.db.execute('SELECT event FROM autonomy_group_inbox').fetchone(),('old',))

if __name__=='__main__':unittest.main()
