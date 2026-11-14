"""Real aiohttp boundary tests; no Bilibili account or external writes."""
import asyncio,json,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
from aiohttp import web
sys.path.insert(0,str(Path('adapters/astrbot').resolve()))
import bilibili_tools

async def main():
    admitted=asyncio.Event();canceled=asyncio.Event();late_admitted=asyncio.Event();release_admission=asyncio.Event();reject_model=False;payloads=[]
    async def submit(request):
        payload=await request.json();payloads.append(payload);admitted.set()
        if reject_model:return web.json_response({'execution':'failed','receipt':{'status':'rejected'},'error':{'code':'model_not_configured','retry':'never','userMessage':'configure image model'}},status=409)
        if payload['operation']=='analyze':late_admitted.set();await release_admission.wait()
        return web.json_response({'id':'job','state':'queued'})
    async def get(request):return web.json_response({'id':'job','state':'queued'})
    async def cancel(request):canceled.set();return web.json_response({'cancelRequested':True})
    app=web.Application();app.router.add_post('/internal/v1/bots/bot/tools',submit);app.router.add_get('/internal/v1/bots/bot/tools/job',get);app.router.add_delete('/internal/v1/bots/bot/tools/job',cancel);app.router.add_post('/internal/v1/bots/bot/tools/cancel-work',cancel)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    try:
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'ai';p.mkdir();settings={'root':root,'botId':'bot','generation':1,'revision':2,'config':{'behavior':{'ownerUin':'123'}}}
            manifest={'version':1,'botId':'bot','generation':1,'revision':2,'settingsRevision':1,'url':'http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]),'token':'fixture','operations':[{'operation':'popular','scope':'public.read','write':False,'args':{'type':'object','properties':{}}}]}
            (p/'bilibili-bridge.json').write_text(json.dumps(manifest),encoding='utf-8')
            work=SimpleNamespace(bot_id='bot',scope='private',subject='123',event_id='real-event')
            event=SimpleNamespace(get_extra=lambda key:work if key=='_hosted_pipeline' else None,get_sender_id=lambda:'123',get_message_str=lambda:'analyze fixture')
            tool=bilibili_tools.tools(settings,event,lambda:True,[])[0]
            task=asyncio.create_task(tool.call(None));await admitted.wait();await asyncio.sleep(.02);task.cancel()
            try:await task;raise AssertionError('cancellation swallowed')
            except asyncio.CancelledError:pass
            assert canceled.is_set(),'cancel request missing'
            manifest['settingsRevision']=2;(p/'bilibili-bridge.json').write_text(json.dumps(manifest),encoding='utf-8')
            result=json.loads(await tool.call(None));assert result['error']=='configuration_changed'
            manifest['operations']=[];(p/'bilibili-bridge.json').write_text(json.dumps(manifest),encoding='utf-8');assert bilibili_tools.tools(settings,event,lambda:True,[])==[]
            manifest['operations']=[{'operation':'analyze','scope':'media.analyze','write':False,'args':{'type':'object','properties':{}}}]
            (p/'bilibili-bridge.json').write_text(json.dumps(manifest),encoding='utf-8')
            live=True;evidence=[];canceled.clear()
            tool=bilibili_tools.tools(settings,event,lambda:live,evidence)[0]
            task=asyncio.create_task(tool.call(None));await late_admitted.wait();live=False;release_admission.set()
            try:await task;raise AssertionError('stale queue admission returned pending')
            except asyncio.CancelledError:pass
            assert canceled.is_set(),'late admission did not cancel original Work'
            assert evidence==[],'stale admission leaked pending evidence'
            reject_model=True;live=True
            rejection=json.loads(await tool.call(None))
            assert rejection=={'execution':'failed','receipt':{'status':'rejected'},'error':{'code':'model_not_configured','retry':'never','userMessage':'configure image model'}}
            assert evidence==[],'rejected admission must not publish a pending task'
            # Completion is not a semantic veto on further observation. Keep
            # the original Work and completion lease; exact args retain the
            # same idempotency key, a different range is a distinct observation.
            reject_model=False
            completion={'id':'original-job','owner':'original-completion-lease','workId':'original-work','scope':work.scope,'senderId':'123','originEvent':'real-event','objective':'analyze fixture'}
            event.get_extra=lambda key:work if key=='_hosted_pipeline' else completion if key=='_hosted_tool_completion' else None
            continued=bilibili_tools.tools(settings,event,lambda:True,[])[0]
            before=len(payloads)
            for start in (0,0,30):
                result=json.loads(await continued.call(None,start=start))
                assert result['execution']=='pending'
            calls=payloads[before:]
            assert len(calls)==3
            assert calls[0]['stepId']==calls[1]['stepId'] and calls[0]['stepId']!=calls[2]['stepId']
            assert all(c['workId']=='original-work' and c['senderId']=='123' and c['continuation']=={'actionId':'original-job','owner':'original-completion-lease'} and c['origin']['eventId']=='real-event' for c in calls)
            print('PASS completion preserves exact Work/lease and param identity for model-chosen supplementary analysis')
            print('PASS missing model 409 remains failed/rejected without queue evidence')
            print('PASS Bilibili cancellation propagates, cancels original job, and stale configuration cannot submit')
            print('PASS delayed media admission cancels stale Work before pending evidence')
    finally:await runner.cleanup()

asyncio.run(main())
