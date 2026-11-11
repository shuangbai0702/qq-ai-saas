import ast
import asyncio
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters/astrbot'))
import bot_pipeline,worker_lifecycle
from runtime_care import RuntimeCare


def extract(path,name,namespace):
    tree=ast.parse((ROOT/path).read_text(encoding='utf8'))
    function=next(n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),str(path), 'exec'),namespace)
    return namespace[name]


class Ingress(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.pipeline=bot_pipeline.BotPipeline(self.db)
        self.lease={'tenantId':'tenant','botId':'bot','generation':1,'revision':1}
        self.member={'uin':'99999','groups':[],'modelConfigured':True,'events':0}
        self.errors={};self.executed=[];self.raw={'message_id':'101','user_id':'12345','message_type':'private','message':'hello'}
        self.message=NS(self_id='99999',group_id='',message_id='101',timestamp=time.time(),raw_message=self.raw)
        def create(message):
            extra={}
            return NS(message_obj=message,get_platform_id=lambda:'qq',get_sender_id=lambda:message.raw_message['user_id'],
                get_group_id=lambda:message.group_id,get_self_id=lambda:message.self_id,
                get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.update({k:v}))
        self.create=create
        st=NS(pipeline=self.pipeline,db=self.db,root=Path('.'))
        self.retain=extract(Path('adapters/astrbot/behavior.py'),'retain_ingress',{'settings':{'qq':self.lease},'store':lambda *args:st,'bot_pipeline':bot_pipeline,'time':time,'care':NS(stamp=lambda root:None)})
        self.fence=worker_lifecycle.StopFence(lambda:{'idle':True})
        async def original(message):
            # Durable queue and payload must predate any SDK preparation.
            self.executed.append(self.db.execute('SELECT status,payload FROM pipeline_work_inputs').fetchone())
        self.namespace={'id':'qq','member':self.member,'members':{'qq':self.member},
            'core':NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={'provider-qq':object()})),
            'platform':NS(create_event=create),'behavior':{'retain_ingress':self.retain},'errors':self.errors,
            'worker_lifecycle':worker_lifecycle,'stop_fence':self.fence,'original':original}
        self.gate=extract(Path('adapters/astrbot/shared-worker.py'),'gated',self.namespace)
    def tearDown(self):self.db.close()
    async def test_real_gate_retains_before_sdk_and_suppresses_identical_redelivery(self):
        await self.gate(self.message)
        self.assertEqual(len(self.executed),1);self.assertEqual(self.executed[0][0],'queued')
        row=self.db.execute('SELECT retry_at,observed_at FROM pipeline_work_inputs').fetchone()
        self.assertGreater(row[0],time.time()+25);self.assertEqual(row[1],self.message.timestamp)
        await self.gate(self.message)
        self.assertEqual(len(self.executed),1)
    async def test_sdk_notice_and_request_without_protocol_message_id_are_retained(self):
        for kind in ('notice','request'):
            with self.subTest(kind=kind):
                self.raw={'post_type':kind,'user_id':'12345','self_id':'99999','sub_type':'poke','target_id':'99999'}
                self.message.raw_message=self.raw;self.message.message_id='sdk-'+kind
                await self.gate(self.message)
                row=self.db.execute('SELECT event,payload FROM pipeline_work_inputs WHERE event=?',('sdk-'+kind,)).fetchone()
                self.assertIsNotNone(row);self.assertNotIn('message_id',__import__('json').loads(row[1]))
        self.assertEqual(len(self.executed),2)

    async def test_typing_status_never_joins_work_or_enters_recovery_queue(self):
        self.db.execute('INSERT INTO pipeline_scope_work VALUES(?,?,?,?,?)',('private:12345','100','12345',self.pipeline.runtime,123))
        for status in ('对方正在输入...',''):
            self.message.raw_message={'post_type':'notice','notice_type':'notify','sub_type':'input_status','user_id':'12345','status_text':status}
            await self.gate(self.message)
            self.assertFalse(self.pipeline.join_active(self.create(self.message)))
            self.assertFalse(self.pipeline.persist_input(self.create(self.message),self.lease))
        self.assertFalse(self.executed);self.assertFalse(self.errors)
        self.assertEqual(self.db.execute('SELECT count(*) FROM pipeline_work_inputs').fetchone()[0],0)

    async def test_restart_reconciles_only_unexecuted_typing_notifications(self):
        import json
        typing={'post_type':'notice','notice_type':'notify','sub_type':'input_status','user_id':'12345'}
        for event,raw,status in [('typing',typing,'blocked_recovery'),('chat',self.raw,'pending'),('unknown',typing,'blocked_unknown'),('executed',typing,'pending'),('poke',{**typing,'sub_type':'poke'},'pending')]:
            self.db.execute('INSERT INTO pipeline_work_inputs(scope,event,payload,status,created) VALUES(?,?,?,?,?)',('private:12345',event,json.dumps(raw),status,time.time()))
        self.db.execute("INSERT INTO pipeline_events(scope,event,subject,status,attempted) VALUES('private:12345','executed','12345','unknown',1)")
        self.db.commit();bot_pipeline.BotPipeline(self.db)
        rows={event:(status,payload) for event,status,payload in self.db.execute('SELECT event,status,payload FROM pipeline_work_inputs')}
        self.assertEqual(rows['typing'],('settled_notification',None))
        self.assertEqual(rows['unknown'][0],'blocked_unknown')
        for event in ('chat','executed','poke'):
            self.assertEqual(rows[event][0],'pending');self.assertIsNotNone(rows[event][1])

    async def test_quiescing_retains_input_without_sdk_call_and_restart_can_recover_it(self):
        self.assertTrue(self.fence.request_if_idle())
        await self.gate(self.message)
        self.assertFalse(self.executed)
        self.assertEqual(self.db.execute('SELECT status FROM pipeline_work_inputs').fetchone()[0],'queued')
        # Existing constructor recovers native queued input lacking an owner;
        # the existing retry_at still prevents immediate same-process racing.
        bot_pipeline.BotPipeline(self.db)
        self.assertEqual(self.db.execute('SELECT status FROM pipeline_work_inputs').fetchone()[0],'pending')
    async def test_retained_second_input_can_join_existing_scope_owner(self):
        self.db.execute('INSERT INTO pipeline_scope_work VALUES(?,?,?,?,?)',('private:12345','100','12345',self.pipeline.runtime,123))
        await self.gate(self.message)
        self.assertTrue(self.pipeline.join_active(self.create(self.message)))
        self.assertEqual(self.db.execute('SELECT status,owner FROM pipeline_work_inputs').fetchone(),('owned','100'))
    async def test_retention_failure_never_executes_unprotected_real_input(self):
        with patch.object(self.pipeline,'persist_input',side_effect=sqlite3.OperationalError('private database text')),patch('worker_lifecycle.record') as record:
            await self.gate(self.message)
        self.assertFalse(self.executed);self.assertEqual(self.errors['qq'],'Ingress retention failed')
        self.assertEqual(record.call_args.kwargs['reason'],'OperationalError')
        self.assertNotIn('private database text',str(record.call_args))
    async def test_original_identity_group_and_provider_gates_still_precede_retention(self):
        for case in ('self','group','provider','stopped'):
            with self.subTest(case=case):
                self.message.self_id='99999';self.message.group_id='';self.member['groups']=[]
                self.namespace['core'].provider_manager.inst_map={'provider-qq':object()}
                self.namespace['core'].event_bus.stopped_platforms=set()
                if case=='self':self.message.self_id='88888'
                if case=='group':self.message.group_id='55555';self.member['groups']=['77777']
                if case=='provider':self.namespace['core'].provider_manager.inst_map={}
                if case=='stopped':self.namespace['core'].event_bus.stopped_platforms.add('qq')
                await self.gate(self.message)
                self.assertEqual(self.db.execute('SELECT count(*) FROM pipeline_work_inputs').fetchone()[0],0)
        self.assertFalse(self.executed)
    async def test_prepare_stop_refuses_configuration_lock_and_health_reports_capability(self):
        class Conflict(Exception):
            def __init__(self,reason=''):super().__init__(reason)
        lock=asyncio.Lock()
        ns={'stop_fence':self.fence,'lock':lock,'web':NS(HTTPConflict=Conflict,json_response=lambda value:value),
            'os':NS(getpid=lambda:123),'behavior':{'activity':lambda:{'idle':True},'metrics':{}},
            'manifest':{'generation':1,'capacity':1},'members':{},'errors':{},'started':0}
        prepare=extract(Path('adapters/astrbot/shared-worker.py'),'prepare_stop',ns)
        health=extract(Path('adapters/astrbot/shared-worker.py'),'health',ns)
        async with lock:
            with self.assertRaises(Conflict):await prepare(None)
            result=await health(None)
            self.assertFalse(result['activity']['idle']);self.assertTrue(result['stopFence'])
        self.assertEqual(await prepare(None),{'prepared':True,'quiescing':True,'pid':123})
        self.assertTrue(self.fence.quiescing)
    async def test_existing_care_clock_starts_no_maintenance_while_fenced(self):
        care=RuntimeCare({},stop_requested=lambda:True)
        work=[]
        async def reconcile():work.append('reconcile')
        care.favorite_reconciler=reconcile
        calls=0
        async def tick(_):
            nonlocal calls
            calls+=1
            if calls>1:raise asyncio.CancelledError()
        with patch('runtime_care.asyncio.sleep',side_effect=tick):
            with self.assertRaises(asyncio.CancelledError):await care.run()
        self.assertFalse(work)
    async def test_care_readback_await_remains_visible_until_its_finally(self):
        care=RuntimeCare({});entered=asyncio.Event();release=asyncio.Event()
        core=NS(event_queue=asyncio.Queue(),event_bus=NS(_pending_tasks={}))
        async def reconcile():entered.set();await release.wait();care.closed=True
        care.favorite_reconciler=reconcile
        async def tick(_):return
        with patch('runtime_care.asyncio.sleep',side_effect=tick):
            task=asyncio.create_task(care.run());await entered.wait()
            self.assertTrue(care.status(core)['maintenanceActive']);self.assertFalse(care.status(core)['idle'])
            release.set();await task
        self.assertFalse(care.status(core)['maintenanceActive'])


if __name__=='__main__':unittest.main()
