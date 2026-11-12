import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from bot_pipeline import BotPipeline
import initiative


def event(identifier='101',at=None):
    extra={};at=at or time.time()
    raw={'message_id':identifier,'user_id':'12345','message_type':'private','message':'hello','time':at}
    return NS(get_group_id=lambda:'',get_sender_id=lambda:'12345',get_self_id=lambda:'99999',get_platform_id=lambda:'qq',
        get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.update({k:v}),
        message_obj=NS(message_id=identifier,timestamp=at,raw_message=raw))


class Recovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.p=BotPipeline(self.db)
        self.lease={'tenantId':'tenant','botId':'bot','generation':1,'revision':1}
        self.issues=[]
        self.store=NS(db=self.db,reflection=NS(checkpoint_issue=lambda *args:self.issues.append(args)))
        self.core=NS(platform_manager=NS(get_insts=lambda:[]),event_bus=NS(stopped_platforms=set()))
    def tearDown(self):self.db.close()
    def input(self):return self.db.execute('SELECT status FROM pipeline_work_inputs').fetchone()[0]
    async def resume(self,lease=None):
        value=lease or self.lease
        return await initiative.resume_scope_input(self.core,'qq',value,self.store,{'qq':value})
    async def test_raw_retention_clock_does_not_replace_bound_sdk_time_on_recovery(self):
        from context_protocol import event_message,model_json
        import shared_context
        shared_context.initialize(self.db)
        for location in ('queue','journal'):
            with self.subTest(location=location):
                self.db.execute('DELETE FROM pipeline_work_inputs')
                self.db.execute('DELETE FROM fishbot_shared_inputs')
                e=event(at=time.time());self.assertTrue(self.p.persist_input(e,self.lease))
                canonical_time=int(e.message_obj.timestamp)
                canonical=event_message({'scope':'private:12345','event_id':'101','sender_id':'12345','at':canonical_time},'hello')
                self.store.model_journals={}
                if location=='queue':
                    self.db.execute('INSERT INTO fishbot_shared_inputs(scope,event,message,created) VALUES(?,?,?,?)',('private:12345','101',model_json(canonical),time.time()))
                else:self.store.model_journals={'bot_shared':NS(last=NS(base=[canonical]))}
                self.db.commit();before=model_json(canonical);delivered=[]
                async def convert(raw):return NS(message_id='101',timestamp=time.time()+20,raw_message=raw)
                platform=NS(meta=lambda:NS(id='qq'),convert_message=convert,create_event=lambda m:m,commit_event=delivered.append)
                self.core.platform_manager.get_insts=lambda:[platform]
                with patch.dict(sys.modules,{'aiocqhttp':NS(Event=NS(from_payload=lambda raw:raw))}):
                    self.assertTrue(await self.resume())
                self.assertEqual(delivered[0].timestamp,canonical_time)
                self.assertEqual(self.db.execute('SELECT observed_at FROM pipeline_work_inputs').fetchone()[0],e.message_obj.timestamp)
                self.assertEqual(model_json(canonical),before,'recovery never rewrites committed source')
                self.assertEqual(delivered[0].raw_message,e.message_obj.raw_message)

    async def test_recovery_canonical_time_cannot_override_conflicting_author(self):
        from context_protocol import event_message,model_json
        import shared_context
        shared_context.initialize(self.db);e=event();self.assertTrue(self.p.persist_input(e,self.lease))
        wrong=event_message({'scope':'private:12345','event_id':'101','sender_id':'54321','at':int(time.time())},'hello')
        self.db.execute('INSERT INTO fishbot_shared_inputs(scope,event,message,created) VALUES(?,?,?,?)',('private:12345','101',model_json(wrong),time.time()));self.db.commit()
        delivered=[]
        async def convert(raw):return NS(message_id='101',timestamp=time.time(),raw_message=raw)
        self.core.platform_manager.get_insts=lambda:[NS(meta=lambda:NS(id='qq'),convert_message=convert,create_event=lambda m:m,commit_event=delivered.append)]
        with patch.dict(sys.modules,{'aiocqhttp':NS(Event=NS(from_payload=lambda raw:raw))}):
            with self.assertRaisesRegex(ValueError,'original_author_changed'):await self.resume()
        self.assertEqual(delivered,[])
    def test_cancelled_root_is_durable_and_only_unstarted_queued_root_can_reenter(self):
        e=event();work=self.p.bind(e,self.lease);work.stop('cancelled');self.p.finish(work,{})
        self.assertEqual(self.input(),'pending')
        self.assertEqual(self.db.execute('SELECT execution_started FROM pipeline_events').fetchone()[0],0)
        self.db.execute("UPDATE pipeline_work_inputs SET status='queued'")
        # Same original ID/payload can resume under current configuration.
        resumed=self.p.bind(e,{**self.lease,'revision':2})
        self.assertTrue(resumed.ledger_owner);self.assertEqual(resumed.status,'received')
        self.assertEqual(resumed.lease['revision'],2)
        resumed.stage('observe')
        self.assertEqual(self.db.execute('SELECT execution_started FROM pipeline_events').fetchone()[0],1)
        resumed.stop('cancelled');self.p.finish(resumed,{})
        self.assertEqual(self.input(),'blocked_unknown')
        self.assertEqual(self.p.bind(e,self.lease).status,'duplicate')
    async def test_restart_only_recovers_explicit_unstarted_root(self):
        self.p.bind(event(),self.lease)
        self.p=BotPipeline(self.db)
        self.assertEqual(self.db.execute('SELECT status,execution_started FROM pipeline_events').fetchone(),('interrupted',0))
        await self.resume()
        self.assertEqual(self.input(),'pending')
    async def test_existing_resume_entry_reuses_original_payload_time_and_current_revision(self):
        e=event(at=time.time()-30);work=self.p.bind(e,self.lease);work.stop('cancelled');self.p.finish(work,{})
        self.db.execute('UPDATE pipeline_work_inputs SET retry_at=0')
        delivered=[]
        async def convert(raw):
            restored=event(raw['message_id']);restored.message_obj.raw_message=raw
            # SDK normally stamps current time; recovery must restore first observation.
            return restored.message_obj
        platform=NS(meta=lambda:NS(id='qq'),convert_message=convert,create_event=lambda m:m,commit_event=delivered.append)
        self.core.platform_manager.get_insts=lambda:[platform]
        with patch.dict(sys.modules,{'aiocqhttp':NS(Event=NS(from_payload=lambda raw:raw))}):
            self.assertTrue(await self.resume({**self.lease,'revision':2}))
        self.assertEqual(len(delivered),1)
        self.assertEqual(delivered[0].timestamp,e.message_obj.timestamp)
        self.assertEqual(delivered[0].raw_message,e.message_obj.raw_message)
        self.assertEqual(self.input(),'queued')
    async def test_real_sdk_notification_conversion_and_recovery_keep_original_converted_id(self):
        from aiocqhttp import Event
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import AiocqhttpAdapter
        adapter=object.__new__(AiocqhttpAdapter)
        raw=Event.from_payload({'post_type':'notice','notice_type':'group_increase','sub_type':'approve','group_id':55555,'operator_id':12345,'user_id':12345,'self_id':99999,'time':int(time.time())})
        message=await adapter.convert_message(raw)
        e=event(message.message_id);e.message_obj=message;e.get_group_id=lambda:'55555'
        self.assertTrue(self.p.persist_input(e,self.lease))
        delivered=[]
        platform=NS(meta=lambda:NS(id='qq'),convert_message=adapter.convert_message,create_event=lambda m:m,commit_event=delivered.append)
        self.core.platform_manager.get_insts=lambda:[platform]
        self.assertTrue(await self.resume())
        self.assertEqual(delivered[0].message_id,message.message_id)
        self.assertEqual(delivered[0].raw_message,raw)
        self.assertNotIn('message_id',raw)

    def test_pokes_recalls_and_typing_never_enter_or_join_chat_work(self):
        from bot_pipeline import is_transport_notification
        for kind,sub in [('notify','poke'),('notify','input_status'),('group_recall',None),('friend_recall',None)]:
            with self.subTest(kind=kind,sub=sub):
                e=event();e.message_obj.raw_message.update(post_type='notice',notice_type=kind,sub_type=sub)
                self.assertTrue(is_transport_notification(e.message_obj.raw_message))
                self.assertFalse(self.p.persist_input(e,self.lease))
                self.assertFalse(self.p.join_active(e))
        self.assertEqual(self.db.execute('SELECT count(*) FROM pipeline_work_inputs').fetchone()[0],0)

    def test_legacy_unexecuted_notices_retire_without_touching_chat_or_unknown_effects(self):
        for name,raw in [('poke',{'post_type':'notice','notice_type':'notify','sub_type':'poke'}),('recall',{'post_type':'notice','notice_type':'group_recall'}),('chat',{'post_type':'message','message':'hello'})]:
            self.db.execute("INSERT INTO pipeline_work_inputs(scope,event,subject,payload,status,created) VALUES('private:12345',?,'12345',?,'pending',?)",(name,json.dumps(raw),time.time()))
        self.db.execute("INSERT INTO pipeline_work_inputs(scope,event,subject,payload,status,created) VALUES('private:12345','uncertain','12345',?,'blocked_unknown',?)",(json.dumps({'post_type':'notice','notice_type':'notify','sub_type':'poke'}),time.time()))
        self.p.retire_transport_notifications()
        rows=dict(self.db.execute('SELECT event,status FROM pipeline_work_inputs'))
        self.assertEqual(rows,{'poke':'settled_notification','recall':'settled_notification','chat':'pending','uncertain':'blocked_unknown'})
        self.assertEqual(self.db.execute("SELECT count(*) FROM pipeline_work_inputs WHERE event IN ('poke','recall') AND payload IS NOT NULL").fetchone()[0],0)

    async def test_nonmessage_recovery_restores_sdk_identity_without_forging_raw_id(self):
        for kind in ('notice','request'):
            with self.subTest(kind=kind):
                self.db.execute('DELETE FROM pipeline_work_inputs')
                e=event('sdk-'+kind);e.message_obj.raw_message={'post_type':kind,'user_id':'12345','self_id':'99999'}
                self.assertTrue(self.p.persist_input(e,self.lease))
                delivered=[]
                async def convert(raw):return NS(message_id='new-sdk-uuid',timestamp=time.time(),raw_message=raw)
                platform=NS(meta=lambda:NS(id='qq'),convert_message=convert,create_event=lambda m:m,commit_event=delivered.append)
                self.core.platform_manager.get_insts=lambda:[platform]
                with patch.dict(sys.modules,{'aiocqhttp':NS(Event=NS(from_payload=lambda raw:raw))}):
                    self.assertTrue(await self.resume())
                self.assertEqual(delivered[0].message_id,'sdk-'+kind)
                self.assertEqual(delivered[0].raw_message,e.message_obj.raw_message)
                self.assertNotIn('message_id',delivered[0].raw_message)

    async def test_repeated_unstarted_cancellation_is_bounded_and_reported(self):
        e=event();work=self.p.bind(e,self.lease)
        for index in range(5):
            work.stop('cancelled');self.p.finish(work,{})
            if index<4:
                self.db.execute("UPDATE pipeline_work_inputs SET status='queued'")
                work=self.p.bind(e,self.lease)
        self.assertEqual(self.input(),'blocked_recovery')
        await self.resume()
        self.assertTrue(any(row[-1].get('state')=='blocked_recovery' for row in self.issues))
    async def test_native_queue_and_same_member_prebind_await_keep_existing_owner(self):
        e=event();self.p.persist_input(e,self.lease)
        self.db.execute("UPDATE pipeline_work_inputs SET status='queued',retry_at=0")
        self.core.event_queue=asyncio.Queue();self.core.event_queue.put_nowait(object())
        self.assertFalse(await self.resume());self.assertEqual(self.input(),'queued')
        self.core.event_queue.get_nowait()
        entered=asyncio.Event();release=asyncio.Event()
        async def before_bind():entered.set();await release.wait()
        task=asyncio.create_task(before_bind());await entered.wait()
        self.core.event_bus._pending_tasks={task:NS(get_platform_id=lambda:'qq')}
        try:
            self.assertFalse(await self.resume());self.assertEqual(self.input(),'queued')
            # Other members' native work does not own this member's input.
            self.core.event_bus._pending_tasks={task:NS(get_platform_id=lambda:'another-bot')}
            await self.resume();self.assertEqual(self.input(),'pending')
        finally:
            release.set();await task
    async def test_started_or_legacy_blocked_zero_send_is_not_settled_or_replayed(self):
        work=self.p.bind(event(),self.lease);work.stage('observe');work.stop('cancelled');self.p.finish(work,{})
        self.db.execute("UPDATE pipeline_work_inputs SET status='queued'")
        await self.resume()
        self.assertEqual(self.input(),'blocked_unknown')
        self.db.execute('UPDATE pipeline_events SET execution_started=NULL')
        self.db.execute("UPDATE pipeline_work_inputs SET status='queued'")
        await self.resume();self.assertEqual(self.input(),'blocked_unknown')
    async def test_identity_mismatch_payload_change_and_expiry_are_visible_holds(self):
        for kind in ('tenant','generation','scope','payload','expired'):
            with self.subTest(kind=kind):
                self.db.execute('DELETE FROM pipeline_events');self.db.execute('DELETE FROM pipeline_work_inputs')
                e=event(at=time.time()-604801 if kind=='expired' else None)
                work=self.p.bind(e,self.lease);work.stop('cancelled');self.p.finish(work,{})
                self.db.execute('UPDATE pipeline_work_inputs SET retry_at=0')
                lease={**self.lease}
                if kind=='tenant':lease['tenantId']='other'
                if kind=='generation':lease['generation']=2
                if kind=='scope':self.db.execute("UPDATE pipeline_work_inputs SET scope='group:55555'")
                if kind=='payload':self.db.execute("UPDATE pipeline_work_inputs SET payload='{}'")
                await self.resume(lease)
                self.assertEqual(self.input(),'blocked_expired' if kind=='expired' else 'blocked_evidence')
                self.assertTrue(self.issues)
    def test_missing_or_changed_original_evidence_never_creates_permission(self):
        e=event();e.message_obj.raw_message['group_id']='55555';e.message_obj.raw_message['message_type']='group'
        self.assertFalse(self.p.persist_input(e,self.lease))
        self.assertEqual(self.db.execute('SELECT count(*) FROM pipeline_work_inputs').fetchone()[0],0)
        work=self.p.bind(e,self.lease);work.stop('cancelled');self.p.finish(work,{})
        self.assertIsNone(self.db.execute('SELECT execution_started FROM pipeline_events').fetchone()[0])


if __name__=='__main__':unittest.main()
