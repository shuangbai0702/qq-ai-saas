"""Thin launcher over upstream AstrBotCoreLifecycle, without Dashboard or Electron."""
import sys,os,json,asyncio,signal
from pathlib import Path
source,root=sys.argv[1],Path(sys.argv[2]).resolve()
sys.path.insert(0,source)
os.environ['ASTRBOT_ROOT']=str(root)
manifest=json.loads((root/'runtime.json').read_text(encoding='utf8'))
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
if os.getenv('FISHBOT_ASTRBOT_SLIM','1')!='0':
    from slim_bootstrap import install
    install()
from astrbot.core import db_helper,LogBroker,LogManager,logger
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from aiohttp import web

async def main():
    broker=LogBroker();LogManager.set_queue_handler(logger,broker)
    from model_egress import install as guard_model_egress
    guard_model_egress()
    core=AstrBotCoreLifecycle(broker,db_helper)
    await core.initialize()
    core.astrbot_config['platform_settings']['empty_mention_waiting']=False
    prompt=(root/'persona.txt').read_text(encoding='utf8')
    existing=await db_helper.get_persona_by_id('platform')
    if existing:await db_helper.update_persona('platform',system_prompt=prompt,tools=[],skills=[])
    else:await db_helper.insert_persona('platform',prompt,tools=[],skills=[])
    await core.persona_mgr.initialize()
    # Upstream allowlist gates private sessions too. Restrict only group input at
    # the OneBot adapter boundary so private chat remains independently usable.
    allowed=set(manifest.get('groups',[]))
    for platform in core.platform_manager.get_insts():
        if hasattr(platform,'handle_msg'):
            original=platform.handle_msg
            async def gated(message, original=original):
                if manifest.get('expectedUin') and str(message.self_id)!=str(manifest['expectedUin']):return
                if allowed and message.group_id and str(message.group_id) not in allowed:return
                await original(message)
            platform.handle_msg=gated
    from behavior import install_runtime
    behavior=install_runtime(core,{'qq':{'root':str(root.parent),'tenantId':manifest.get('tenantId'),'botId':manifest.get('botId',root.parent.name),'config':manifest.get('config',{}),'generation':manifest['generation'],'revision':manifest['revision'],'expectedUin':manifest.get('expectedUin','')}})
    stop=asyncio.Event()
    async def health(request):
        if request.headers.get('Authorization')!='Bearer '+manifest['token']:raise web.HTTPUnauthorized()
        return web.json_response({'ready':True,'generation':manifest['generation'],'revision':manifest['revision'],'root':str(root),'pid':os.getpid(),'modelConfigured':bool(os.getenv('FISHBOT_MODEL_KEY')),'metrics':behavior['metrics']})
    async def manage_data(request):
        if request.headers.get('Authorization')!='Bearer '+manifest['token']:raise web.HTTPUnauthorized()
        if request.headers.get('X-Member-Generation')!=str(manifest['generation']) or request.headers.get('X-Member-Revision')!=str(manifest['revision']):raise web.HTTPConflict()
        return web.json_response(behavior['manage']('qq',await request.json()))
    async def autonomous_work(request):
        if request.headers.get('Authorization')!='Bearer '+manifest['token']:raise web.HTTPUnauthorized()
        if request.headers.get('X-Member-Generation')!=str(manifest['generation']) or request.headers.get('X-Member-Revision')!=str(manifest['revision']):raise web.HTTPConflict()
        try:return web.json_response(await behavior['autonomous']('qq',request.match_info['operation'],await request.json()))
        except (ValueError,KeyError,TypeError):raise web.HTTPConflict(reason='Autonomous work invalid or stale') from None
    app=web.Application(client_max_size=131072);app.router.add_get('/health',health);app.router.add_post('/behavior-data',manage_data);app.router.add_post('/autonomous/{operation}',autonomous_work)
    runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',manifest['aiPort']+1).start()
    task=asyncio.create_task(core.start())
    for sig in [signal.SIGTERM,signal.SIGINT]:
        try:asyncio.get_running_loop().add_signal_handler(sig,stop.set)
        except (NotImplementedError,RuntimeError):pass
    try:await stop.wait()
    finally:
        await core.stop();task.cancel();await runner.cleanup();behavior['close']()

asyncio.run(main())
