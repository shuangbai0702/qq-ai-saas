"""Exercise original completion claims with a local bridge, without models or QQ."""
import asyncio,hashlib,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path('adapters/astrbot').resolve()))
sys.path.insert(0,str(Path('references/astrbot').resolve()))
from aiohttp import web
from bot_pipeline import BotPipeline
import pipeline_tools,dsh_completion,bilibili_tools
from types import SimpleNamespace


class DshCompletion(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.settings={'root':self.temp.name,'botId':'bot','tenantId':'tenant','generation':1,'revision':1,'config':{}}
        self.db=sqlite3.connect(':memory:');self.pipeline=BotPipeline(self.db);pipeline_tools.initialize(self.db)
        scope='private:12345';sender='12345';event='original'
        work='foreground:'+hashlib.sha256(json.dumps(['bot',scope,sender,event],ensure_ascii=False).encode()).hexdigest()
        self.db.execute("INSERT INTO pipeline_events(scope,subject,event,at,source,stage,status,reason) VALUES(?,?,?,?,'message','model','received','')",(scope,sender,event,time.time()));self.db.commit()
        record={'bot_id':'bot','tenant_id':'tenant','generation':1,'config_revision':1,'settings_revision':2,
            'account_revision':3,'account_uid':'999','authority':'foreground','work_id':work,'step_id':'delegate:call',
            'sender_id':sender,'origin_scope':scope,'origin_event':event,'operation':'analyze','args':{},'args_digest':'digest',
            'deadlineAt':int(time.time()*1000)+600000,'continuation':{'objective':'raw objective','dsh':{'delegationId':'delegate'}}}
        job=pipeline_tools.admit(self.pipeline,self.settings,record)
        with self.db:pipeline_tools.settle(self.db,pipeline_tools.row(self.db,job['id']),{'execution':'completed','receipt':{'status':'not_applicable'},'data':{'raw':'private media'}})
        claimed=pipeline_tools.completion(self.pipeline,self.settings,{'settingsRevision':2})
        self.item={**claimed,'workId':claimed['work_id'],'scope':scope,'senderId':sender,'eventId':event,'revision':1,'objective':'raw objective'}
        self.db.execute("UPDATE pipeline_events SET status='completed' WHERE event='original'");self.db.commit()
        self.frames=[{'type':'final','text':'已核实的视频结论'}];self.payloads=[];self.live=True
        self.ready=asyncio.Event();self.release=asyncio.Event();self.delayed=False
        async def submit(request):
            self.payloads.append(await request.json());self.ready.set()
            if self.delayed:await self.release.wait()
            return web.Response(text=''.join(json.dumps(frame,ensure_ascii=False)+'\n' for frame in self.frames),content_type='application/x-ndjson')
        self.acks=[]
        async def claim(request):return web.json_response({'completion':self.item})
        async def ack(request):
            value=await request.json();self.acks.append(value)
            return web.json_response(pipeline_tools.acknowledge(self.pipeline,value['id'],value['owner'],value['status']))
        app=web.Application();app.router.add_post('/delegate',submit)
        app.router.add_post('/internal/v1/bots/bot/tools/completion',claim)
        app.router.add_post('/internal/v1/bots/bot/tools/completion-ack',ack)
        self.runner=web.AppRunner(app);await self.runner.setup();site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
        self.bridge={**self.settings,'version':2,'mode':'production','url':'http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/delegate','token':'a'*64}
        self.file=Path(self.temp.name)/'ai/dsh-bridge.json';self.file.parent.mkdir();self.file.write_text(json.dumps(self.bridge),encoding='utf8')
        (self.file.parent/'bilibili-bridge.json').write_text(json.dumps({**self.bridge,'version':1,'hasCompletions':True,'url':self.bridge['url'].removesuffix('/delegate')}),encoding='utf8')
    async def asyncTearDown(self):
        self.release.set();await self.runner.cleanup();self.db.close();self.temp.cleanup()
    async def resume(self):return await dsh_completion.resume(self.settings,self.item,lambda:self.live,self.db)
    async def complete(self):
        events=[]
        async def status():return {'online':True}
        async def login():return {'user_id':888}
        def create(msg):
            extra={}
            return SimpleNamespace(message_obj=msg,set_extra=lambda key,value:extra.update({key:value}),extra=extra)
        platform=SimpleNamespace(meta=lambda:SimpleNamespace(id='bot'),bot=SimpleNamespace(get_status=status,get_login_info=login),create_event=create,commit_event=events.append)
        core=SimpleNamespace(platform_manager=SimpleNamespace(get_insts=lambda:[platform]),event_bus=SimpleNamespace(stopped_platforms=[]))
        self.assertTrue(await bilibili_tools.resume_completion(core,'bot',self.settings,SimpleNamespace(db=self.db),{'bot':self.settings}))
        return events
    async def test_completion_branch_delivers_only_final_text_in_original_event(self):
        events=await self.complete();self.assertEqual(len(events),1)
        prompt=events[0].message_obj.message_str
        self.assertIn('已核实的视频结论',prompt);self.assertNotIn('raw objective',prompt);self.assertNotIn('private media',prompt)
        self.assertNotIn('toolResult',prompt);self.assertEqual(events[0].extra['_hosted_tool_completion']['id'],self.item['id'])
        self.assertEqual(self.acks,[])
    async def test_completion_branch_pending_ack_and_error_release(self):
        self.frames=[{'type':'tool','id':'forbidden'}];self.assertEqual(await self.complete(),[])
        self.assertEqual(self.acks[-1]['status'],'pending')
        # Restore the real original claim after its safely released failure.
        claimed=pipeline_tools.completion(self.pipeline,self.settings,{'settingsRevision':2});self.item.update(claimed)
        self.frames=[{'type':'pending'}];self.assertEqual(await self.complete(),[])
        self.assertEqual(self.acks[-1]['status'],'done')
    async def test_actual_claim_retains_identity_and_final_only(self):
        self.assertEqual(await self.resume(),'已核实的视频结论')
        payload=self.payloads[0]
        self.assertEqual(payload['completion'],{'id':self.item['id'],'owner':self.item['owner']})
        self.assertEqual(payload['delegationId'],'delegate');self.assertNotIn('input',payload)
        self.assertNotIn('result',payload);self.assertEqual(payload['parent']['account_uid'],'999')
        self.assertEqual(self.db.execute("SELECT count(*) FROM pipeline_events WHERE source='tool'").fetchone()[0],1)
        self.assertEqual(pipeline_tools.view(pipeline_tools.row(self.db,self.item['id']))['completion_state'],'claimed')
    async def test_pending_no_final_and_raw_json_callbacks_rejected(self):
        self.frames=[{'type':'pending'}];self.assertIsNone(await self.resume())
        for frames in ([],[{'type':'tool','id':'x'}],[{'type':'final','text':'{"executionLog":"raw"}'}],[{'type':'final','text':'ok'},{'type':'pending'}]):
            self.frames=frames
            with self.assertRaises(ValueError):await self.resume()
    async def test_stale_owner_rejected_before_http(self):
        self.item['owner']='foreign'
        with self.assertRaisesRegex(ValueError,'continuation_stale'):await self.resume()
        self.assertEqual(self.payloads,[])
    async def test_late_success_after_shutdown_is_rejected(self):
        self.delayed=True;task=asyncio.create_task(self.resume());await self.ready.wait();self.live=False;self.release.set()
        with self.assertRaises(asyncio.CancelledError):await task
    async def test_late_success_after_completion_lease_takeover_is_rejected(self):
        self.delayed=True;task=asyncio.create_task(self.resume());await self.ready.wait()
        value=pipeline_tools.row(self.db,self.item['id']);value['request']['completion']['owner']='replacement'
        with self.db:pipeline_tools.save_request(self.db,value)
        self.release.set()
        with self.assertRaisesRegex(ValueError,'continuation_stale'):await task
    async def test_late_success_after_config_change_is_rejected(self):
        self.delayed=True;task=asyncio.create_task(self.resume());await self.ready.wait()
        self.file.write_text(json.dumps({**self.bridge,'token':'b'*64}),encoding='utf8');self.release.set()
        with self.assertRaisesRegex(ValueError,'configuration_changed'):await task

if __name__=='__main__':unittest.main()
