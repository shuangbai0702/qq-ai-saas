"""Seven confirmed business rounds, small legal appends, no extra model calls."""
import copy,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from bot_pipeline import BotPipeline,Work
from context_transport import request_context_view
from context_protocol import READONLY_CURRENT,model_json
from decision_history import apply_changes
from readonly_journal import ReadonlyJournal
import periodic_guidance as guidance


class Cadence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'cadence.sqlite';self.db=sqlite3.connect(self.path)
        BotPipeline(self.db);guidance.initialize(self.db)
        self.addCleanup(lambda:self.db.close())

    def completed(self,index,scope=None,status='sent',source='message'):
        scope=scope or ('private:12345678' if index%2 else 'group:55555')
        work=Work('bot',scope,'12345678','99999999',str(index),source,time.time(),{})
        work.status=status;work.planned_messages=work.confirmed_messages=1
        self.db.execute('''INSERT OR IGNORE INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,attempted,confirmed)
            VALUES(?,?,?,?,?,'commit',?,'',1,1)''',(scope,work.subject,work.event_id,time.time(),source,status));self.db.commit()
        return work

    def test_cadence_unifies_scopes_restart_duplicate_and_nonbusiness_outcomes(self):
        for index in range(1,15):
            work=self.completed(index)
            self.assertTrue(guidance.record(self.db,work,'bot_shared'))
            self.assertFalse(guidance.record(self.db,work,'bot_shared'))
            value=guidance.view(self.db,'bot_shared')
            if index<7:self.assertIsNone(value)
            else:
                self.assertIn('已完成'+str((index//7)*7)+'轮',value)
                self.assertTrue(value.endswith(guidance.REMINDER))
            if index==7:
                self.db.close();self.db=sqlite3.connect(self.path);guidance.initialize(self.db)
                self.assertFalse(guidance.record(self.db,work,'bot_shared'))
                self.assertEqual(guidance.view(self.db,'bot_shared'),value)
        for index,status,source in [(20,'silent','message'),(21,'partial','message'),(22,'failed','message'),(23,'sent','tool_continuation'),(24,'sent','autonomous')]:
            self.assertFalse(guidance.record(self.db,self.completed(index,status=status,source=source),'bot_shared'))
        self.assertEqual(self.db.execute('SELECT completed FROM fishbot_guidance_cadence WHERE scope=?',('bot_shared',)).fetchone()[0],14)
        self.assertIsNone(guidance.view(self.db,'another_bot_or_scope'))

    def test_exact_short_reminder_appends_at_seven_and_fourteen_with_immediate_authority_and_agreements(self):
        journal=ReadonlyJournal();native=[];previous=[];system=None
        current={'identity':{'sender_id':'12345678'},'permissions':{'send':True},'active_agreements':[]}
        for index in range(15):
            if index:guidance.record(self.db,self.completed(index),'bot_shared')
            if index==4:
                current['permissions']['send']=False
                current['active_agreements']=[{'id':'real','status':'accepted','revision':2,'detail':'此处保持沉默'}]
            native.append({'role':'user','content':'real question '+str(index)})
            context=guidance.attach(model_json(current),self.db,'bot_shared')
            current_system,contexts=request_context_view('FIXED_SYSTEM',native,None,'UNCHANGED_POLICY','CURRENT_RULES',context,incremental_policy=True)
            if system is None:system=current_system
            self.assertEqual(current_system,system)
            candidate=journal.prepare([{'role':'system','content':current_system}]+contexts,('same',),now=index+1,compact_policy=True)
            self.assertEqual(candidate.wire[:len(previous)],previous)
            added=json.dumps(candidate.wire[len(previous):],ensure_ascii=False)
            self.assertEqual(guidance.REMINDER in added,index in (7,14))
            if index==4:
                self.assertFalse(json.loads(candidate.authority['content'].split('\n',1)[1])['authority']['permissions']['send'])
                state={}
                for message in candidate.wire:
                    text=message.get('content')
                    if isinstance(text,str) and text.startswith(READONLY_CURRENT+'\n'):
                        packet=json.loads(text.split('\n',1)[1]);state=apply_changes(state,packet['readonlyChanges']) if set(packet)=={'readonlyChanges'} else packet
                self.assertEqual(state['active_agreements'],current['active_agreements'])
                self.assertNotIn('periodic_guidance',state)
            self.assertTrue(journal.commit(candidate));previous=copy.deepcopy(candidate.wire)
            native.append({'role':'assistant','content':'confirmed answer '+str(index)})
        self.assertEqual(json.dumps(previous,ensure_ascii=False).count(guidance.REMINDER),2)
        self.assertEqual(sum(message.get('role')=='system' for message in previous),1)


class Settlement(unittest.IsolatedAsyncioTestCase):
    async def test_existing_work_finalization_counts_with_memory_disabled(self):
        from behavior import install_runtime
        with tempfile.TemporaryDirectory() as root:
            lease={'botId':'bot','tenantId':'tenant','generation':1,'revision':1,'root':root,'config':{'behavior':{'memory':False}}}
            core=NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={}))
            runtime=install_runtime(core,{'bot':lease})
            try:
                runtime['manage']('bot',{'action':'list'});store=runtime['stores']['bot'];store.shared_context=True
                event=NS(get_platform_id=lambda:'bot',get_extra=lambda k,d=None:d)
                for index in range(1,8):
                    scope='private:12345678' if index%2 else 'group:55555'
                    work=Work('bot',scope,'12345678','99999999',str(index),'message',time.time(),lease)
                    work.status='sent';work.planned_messages=work.confirmed_messages=1
                    store.db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason,attempted,confirmed) VALUES(?,?,?,?,'message','commit','sent','',1,1)",(scope,work.subject,work.event_id,time.time()));store.db.commit()
                    runtime['settle_work_round'](store,event,work,lease)
                    runtime['settle_work_round'](store,event,work,lease)
                self.assertIn(guidance.REMINDER,guidance.view(store.db,'bot_shared'))
                self.assertEqual(store.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],0)
            finally:runtime['close']()


if __name__=='__main__':unittest.main()
