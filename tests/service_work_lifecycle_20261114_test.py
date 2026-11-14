"""Service Work shares the existing event owner; no model or protocol I/O."""
import importlib.util,json,sqlite3,sys,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters/astrbot'))
from bot_pipeline import BotPipeline
from activation_rounds import record,valid,settled

class ServiceWork(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.p=BotPipeline(self.db)
        self.lease={'botId':'bot','tenantId':'tenant','generation':1,'revision':1}
        self.identity={**self.lease,'id':'real-work','epoch':2,'cycleId':'cycle','kind':'research','expiresAt':(time.time()+300)*1000}
        self.extra={};self.event=NS(get_self_id=lambda:'90000000',set_extra=lambda k,v:self.extra.update({k:v}))
        self.db.execute('CREATE TABLE memory_message_sources(scope TEXT,event TEXT,direction TEXT,data TEXT,PRIMARY KEY(scope,event,direction))')
        self.db.execute('CREATE TABLE memory_rounds(scope TEXT,subject TEXT,event TEXT,question TEXT,answer TEXT,at REAL,PRIMARY KEY(scope,event))')
    def tearDown(self):self.db.close()
    def bind(self):return self.p.bind_internal(self.event,self.lease,self.identity,'self','choose freely')
    def old_owner(self,state='completed'):
        self.db.execute('CREATE TABLE pipeline_actions(id TEXT PRIMARY KEY,fingerprint TEXT,epoch INTEGER,scope TEXT,expires REAL,state TEXT,result TEXT,at REAL)')
        self.db.execute('INSERT INTO pipeline_actions VALUES(?,?,?,?,?,?,?,?)',('old','exact-fingerprint',2,'self',time.time()+300,state,json.dumps({'status':state,'summary':'actual result'}),time.time()-172800))
        self.db.execute('INSERT INTO pipeline_action_effects VALUES(?,?,?,?,?,?,?)',('old',0,'send_group_msg','group:12345','unknown',None,time.time()))
        self.db.execute("INSERT INTO memory_message_sources VALUES('bot_shared','old-round','activation',?)",(json.dumps({'round_kind':'autonomous_activation','owner_work':'old','source_scope':'self'}),))
        self.db.execute("INSERT INTO memory_rounds VALUES('bot_shared','0','old-round','','',?)",(time.time(),));self.db.commit()
    def test_current_owner_and_one_round_with_exact_duplicate_fence(self):
        work=self.bind();self.assertTrue(work.ledger_owner)
        with self.db:
            record(self.db,'bot_shared','activation',work.event_id,source_scope='self',subject='0')
            record(self.db,'bot_shared','activation',work.event_id,source_scope='self',subject='0')
        rows=self.db.execute('SELECT * FROM memory_rounds').fetchall();self.assertEqual(len(rows),1)
        self.assertTrue(valid(self.db,'bot_shared',rows[0]));self.assertFalse(settled(self.db,'bot_shared',rows))
        self.p.finish_internal(work,{'status':'completed','summary':'silent decision'});self.p.finish(work,{})
        self.assertTrue(settled(self.db,'bot_shared',rows));self.assertEqual(self.p.internal_result(work.event_id)['status'],'completed')
        self.assertTrue(self.bind()['duplicate']);self.identity['cycleId']='forged'
        with self.assertRaisesRegex(ValueError,'identity_mismatch'):self.bind()
        self.assertIsNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='pipeline_actions'").fetchone())
    def test_restart_preserves_unknown_and_never_reexecutes(self):
        work=self.bind();seq=self.p.internal_effect(work,'send_group_msg','group:12345')
        self.p=BotPipeline(self.db)
        self.assertEqual(self.p.internal_result(work.event_id)['effects'][0]['status'],'unknown')
        self.assertTrue(self.bind()['duplicate'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM pipeline_events WHERE source='autonomous'").fetchone()[0],1)
    def test_transfer_preserves_current_round_receipts_and_is_one_time(self):
        self.old_owner();before=self.db.execute('SELECT * FROM memory_message_sources').fetchall()
        self.p=BotPipeline(self.db);self.p=BotPipeline(self.db)
        self.assertEqual(self.db.execute('SELECT * FROM memory_message_sources').fetchall(),before)
        row=self.db.execute('SELECT * FROM memory_rounds').fetchone();self.assertTrue(valid(self.db,'bot_shared',row));self.assertTrue(settled(self.db,'bot_shared',[row]))
        self.assertEqual(self.p.internal_result('old')['effects'][0]['status'],'unknown')
        self.assertEqual(self.p.internal_result('old')['result']['summary'],'actual result')
        self.assertIsNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='pipeline_actions'").fetchone())
    def test_old_activation_owner_survives_normal_event_prune(self):
        self.old_owner();self.p=BotPipeline(self.db)
        extra={};event=NS(get_group_id=lambda:'',get_sender_id=lambda:'12345',get_self_id=lambda:'90000000',get_platform_id=lambda:'qq',get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.update({k:v}),message_obj=NS(message_id='human',timestamp=time.time()))
        self.db.execute("UPDATE pipeline_events SET status='silent' WHERE event='old'");self.db.commit()
        self.p.bind(event,self.lease)
        self.assertIsNotNone(self.p.internal_result('old'))
    def test_rollback_restores_owner_without_changing_journal_provenance(self):
        work=self.bind();self.p.finish_internal(work,{'status':'completed','summary':'actual result'})
        with self.db:record(self.db,'bot_shared','activation',work.event_id,source_scope='self',subject='0')
        before=self.db.execute('SELECT * FROM memory_message_sources').fetchall()
        module=importlib.util.spec_from_file_location('rollback',ROOT/'scripts/rollback-service-work.py');m=importlib.util.module_from_spec(module);module.loader.exec_module(m);m.rollback(self.db)
        self.assertEqual(self.db.execute('SELECT * FROM memory_message_sources').fetchall(),before)
        self.assertEqual(self.db.execute('SELECT state FROM pipeline_actions').fetchone()[0],'completed')
        self.p=BotPipeline(self.db)
        self.assertTrue(self.bind()['duplicate']);self.assertTrue(valid(self.db,'bot_shared',self.db.execute('SELECT * FROM memory_rounds').fetchone()))

    def test_rollback_failure_keeps_owner_and_does_not_leave_old_table(self):
        work=self.bind();self.p.finish_internal(work,{'status':'completed','summary':'real result'})
        before=self.db.execute('SELECT * FROM pipeline_events').fetchall()
        self.db.execute("CREATE TRIGGER refuse_owner_delete BEFORE DELETE ON pipeline_events BEGIN SELECT RAISE(ABORT,'injected'); END")
        spec=importlib.util.spec_from_file_location('rollback',ROOT/'scripts/rollback-service-work.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with self.assertRaisesRegex(sqlite3.IntegrityError,'injected'):module.rollback(self.db)
        self.assertEqual(self.db.execute('SELECT * FROM pipeline_events').fetchall(),before)
        self.assertIsNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='pipeline_actions'").fetchone())
        self.assertTrue(self.bind()['duplicate'])

    def test_unknown_receipt_owner_survives_without_activation_metadata(self):
        self.old_owner();self.db.execute('DELETE FROM memory_message_sources');self.db.execute('DELETE FROM memory_rounds');self.db.commit()
        self.p=BotPipeline(self.db)
        self.db.execute("UPDATE pipeline_events SET status='silent' WHERE event='old'");self.db.commit()
        extra={};event=NS(get_group_id=lambda:'',get_sender_id=lambda:'12345',get_self_id=lambda:'90000000',get_platform_id=lambda:'qq',get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.update({k:v}),message_obj=NS(message_id='human',timestamp=time.time()))
        self.p.bind(event,self.lease)
        self.assertEqual(self.p.internal_result('old')['effects'][0]['status'],'unknown')
        self.db.execute("UPDATE pipeline_action_effects SET state='confirmed',receipt='real-ack' WHERE action='old'");self.db.commit()
        event.message_obj.message_id='next';self.p.bind(event,self.lease)
        self.assertIsNone(self.p.internal_result('old'))

if __name__=='__main__':unittest.main()
