"""Failed owner-contact controls cannot enter the chat compression pool."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters/astrbot'),str(ROOT/'references/astrbot')]
from behavior import BehaviorStore
from context_protocol import SOURCE,model_json
from semantic_batch import discard_failed_service_inputs


class ServiceInputs(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.temp.name)/'behavior.sqlite');self.db=self.store.db
        self.scope='private:12345678';self.subject='12345678';self.event='12345'
        self.shared=model_json([self.scope,self.event])
        self.question=SOURCE+model_json({'scope':self.scope,'sender_id':self.subject,'event_id':self.event,'kind':'platform_service_not_user'})+'\n[平台内部主动时刻]'
        self.binding={'source_scope':self.scope,'event_id':self.event,'sender_id':self.subject,'outputs':[],
                      'settlement':{'status':'error','attempted':0,'confirmed':0,'planned':0,'confirmed_messages':0}}
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,0)',('bot_shared',self.subject,self.shared,self.question,''))
        self.db.execute("INSERT INTO pipeline_events(scope,subject,event,status,source,attempted,confirmed,at) VALUES(?,?,?,'error','owner_contact',0,0,0)",(self.scope,self.subject,self.event))
        self.db.execute("INSERT INTO memory_message_sources VALUES('bot_shared',?,'model',?)",(self.shared,model_json(self.binding)))
        self.db.execute("INSERT INTO memory_message_sources VALUES('bot_shared',?,'incoming','{}')",(self.shared,))
        self.db.commit()

    def tearDown(self):self.store.close();self.temp.cleanup()

    def save_binding(self,value):
        self.db.execute("UPDATE memory_message_sources SET data=? WHERE scope='bot_shared' AND event=? AND direction='model'",(model_json(value),self.shared));self.db.commit()

    def assert_protected(self):
        self.assertEqual(discard_failed_service_inputs(self.db,'bot_shared'),0)
        self.assertTrue(self.db.execute("SELECT 1 FROM memory_pending WHERE scope='bot_shared' AND event=?",(self.shared,)).fetchone())
        self.assertEqual(self.db.execute("SELECT count(*) FROM memory_message_sources WHERE scope='bot_shared' AND event=?",(self.shared,)).fetchone()[0],2)

    def test_exact_failed_control_leaves_pool_but_not_security_ledger_and_does_not_commit(self):
        self.db.execute('BEGIN IMMEDIATE')
        self.assertEqual(discard_failed_service_inputs(self.db,'bot_shared'),1)
        self.assertTrue(self.db.in_transaction)
        self.assertFalse(self.db.execute('SELECT 1 FROM memory_pending').fetchone())
        self.assertFalse(self.db.execute('SELECT 1 FROM memory_message_sources').fetchone())
        self.assertTrue(self.db.execute('SELECT 1 FROM pipeline_events').fetchone())
        self.assertEqual(self.db.execute('SELECT count(*) FROM memory_round_material').fetchone()[0],0)
        self.db.rollback();self.assertTrue(self.db.execute('SELECT 1 FROM memory_pending').fetchone())

    def test_real_qq_source_is_protected(self):
        question=self.question.replace(',"kind":"platform_service_not_user"','')
        origin=json.loads(question.split('\n',1)[0][len(SOURCE):]);origin.pop('kind',None)
        self.db.execute('UPDATE memory_pending SET question=?',(SOURCE+model_json(origin)+'\nreal user text',));self.db.commit();self.assert_protected()

    def test_service_principal_can_differ_from_owner_contact_work_subject(self):
        self.db.execute("UPDATE pipeline_events SET subject='87654321'");self.db.commit()
        self.assertEqual(discard_failed_service_inputs(self.db,'bot_shared'),1)
        self.assertEqual(self.db.execute('SELECT subject FROM pipeline_events').fetchall(),[('87654321',)])

    def test_ambiguous_work_subjects_do_not_authorize_cleanup(self):
        self.db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,attempted,confirmed) VALUES(?, '87654321', ?, 0, 'owner_contact','settle','error',0,0)",(self.scope,self.event))
        self.db.commit();self.assert_protected()

    def test_mirrored_failed_pending_is_removed_together_and_rollback_restores_both(self):
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,0)',
                        (self.scope,self.subject,self.event,self.question,''))
        self.db.execute("INSERT INTO memory_message_sources VALUES(?,?,'model',?)",
                        (self.scope,self.event,model_json(self.binding)))
        self.db.execute("INSERT INTO memory_message_sources VALUES(?,?,'incoming','{}')",
                        (self.scope,self.event))
        self.db.commit()
        pending=self.db.execute('SELECT * FROM memory_pending ORDER BY scope,event').fetchall()
        bindings=self.db.execute('SELECT * FROM memory_message_sources ORDER BY scope,event,direction').fetchall()
        ledger=self.db.execute('SELECT * FROM pipeline_events').fetchall()
        self.db.execute('BEGIN IMMEDIATE')
        self.assertEqual(discard_failed_service_inputs(self.db,'bot_shared'),2)
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT * FROM memory_pending').fetchall(),[])
        self.assertEqual(self.db.execute('SELECT * FROM memory_message_sources').fetchall(),[])
        self.assertEqual(self.db.execute('SELECT * FROM pipeline_events').fetchall(),ledger)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT * FROM memory_pending ORDER BY scope,event').fetchall(),pending)
        self.assertEqual(self.db.execute('SELECT * FROM memory_message_sources ORDER BY scope,event,direction').fetchall(),bindings)

    def test_partial_unknown_or_non_owner_contact_ledger_is_protected(self):
        for status,kind,attempted,confirmed in (('error','message',0,0),('partial','owner_contact',1,1),('unknown','owner_contact',1,0),('sent','owner_contact',1,1)):
            with self.subTest(status=status,kind=kind):
                self.db.execute('UPDATE pipeline_events SET status=?,source=?,attempted=?,confirmed=?',(status,kind,attempted,confirmed));self.db.commit();self.assert_protected()

    def test_model_capture_output_or_uncertain_settlement_is_protected(self):
        for field,value in (('input_digest','digest'),('outputs',[{'digest':'output'}]),('settlement',{'status':'error','attempted':0,'confirmed':0,'planned':0}),('settlement',{'status':'unknown','attempted':0,'confirmed':0,'planned':0,'confirmed_messages':0})):
            with self.subTest(field=field,value=value):
                binding=copy.deepcopy(self.binding);binding[field]=value;self.save_binding(binding);self.assert_protected()

    def test_original_or_shared_round_material_and_active_inputs_protect(self):
        for logical,key in ((self.scope,self.event),('bot_shared',self.shared)):
            for table in ('memory_rounds','memory_round_material','pipeline_scope_work','pipeline_work_inputs'):
                with self.subTest(logical=logical,table=table):
                    if table=='memory_rounds':self.db.execute('INSERT INTO memory_rounds(scope,subject,event,at) VALUES(?,?,?,0)',(logical,self.subject,key))
                    elif table=='memory_round_material':self.db.execute('INSERT INTO memory_round_material VALUES(?,?,?,?)',(logical,key,self.subject,'{}'))
                    elif table=='pipeline_scope_work':self.db.execute('INSERT INTO pipeline_scope_work(scope,event) VALUES(?,?)',(logical,key))
                    else:self.db.execute("INSERT INTO pipeline_work_inputs(scope,event,subject,status,created) VALUES(?,?,?,'owned',0)",(logical,key,self.subject))
                    self.db.commit();self.assert_protected()
                    self.db.execute('DELETE FROM '+table+' WHERE scope=? AND event=?',(logical,key));self.db.commit()

    def test_real_original_input_with_same_transport_key_is_protected(self):
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,0)',(self.scope,self.subject,self.event,SOURCE+model_json({'scope':self.scope,'sender_id':self.subject,'event_id':self.event})+'\nreal QQ text',''));self.db.commit()
        self.assert_protected()

    def test_other_scope_and_other_event_are_untouched(self):
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,0)',('private:87654321','87654321','other','real QQ text',''))
        self.db.commit();self.assertEqual(discard_failed_service_inputs(self.db,'bot_shared'),1)
        self.assertEqual(self.db.execute('SELECT scope,event,question FROM memory_pending').fetchall(),[('private:87654321','other','real QQ text')])


if __name__=='__main__':unittest.main()
