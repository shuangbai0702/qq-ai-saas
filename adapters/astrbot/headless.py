"""Thin launcher over upstream AstrBotCoreLifecycle, without Dashboard or Electron."""
import sys,os,json,asyncio,signal,copy
from pathlib import Path
import worker_lifecycle
worker_lifecycle.initialize()
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
    from native_ingress import admitted
    for platform in core.platform_manager.get_insts():
        if hasattr(platform,'handle_msg'):
            original=platform.handle_msg
            async def gated(message, original=original):
                if manifest.get('expectedUin') and str(message.self_id)!=str(manifest['expectedUin']):return
                if not admitted(getattr(message,'raw_message',{}) or {},manifest.get('config',{})):return
                await original(message)
            platform.handle_msg=gated
    from behavior import install_runtime
    settings={'qq':{'root':str(root.parent),'tenantId':manifest.get('tenantId'),'botId':manifest.get('botId',root.parent.name),'config':manifest.get('config',{}),'generation':manifest['generation'],'revision':manifest['revision'],'systemChangePermitId':manifest.get('systemChangePermitId'),'personaEdit':manifest.get('personaEdit'),'expectedUin':manifest.get('expectedUin',''),'_base_owner':object()}}
    behavior=install_runtime(core,settings)
    from native_ingress import bind as bind_ingress
    for platform in core.platform_manager.get_insts():
        if hasattr(platform,'convert_message'):
            bind_ingress(platform,lambda raw:behavior['acquire_raw_ingress']('qq',raw),
                lambda raw:(not manifest.get('expectedUin') or str(raw.get('self_id'))==str(manifest['expectedUin']))
                and admitted(raw,manifest.get('config',{})),
                prepare=lambda raw:behavior['prepare_raw_ingress']('qq',raw))
    stop=asyncio.Event()
    base_lock=asyncio.Lock()
    async def update_persona(request):
        if request.headers.get('Authorization')!='Bearer '+manifest['token']:raise web.HTTPUnauthorized()
        body=await request.json()
        async with base_lock:
            from web_base import sha,replace_cached_persona
            config=body.get('config');proof=body.get('personaEdit');permit=body.get('systemChangePermitId')
            if (body.get('generation')!=manifest['generation'] or body.get('botId')!=manifest.get('botId')
                or body.get('tenantId')!=manifest.get('tenantId') or type(body.get('revision')) is not int
                or body['revision']<manifest['revision'] or not isinstance(config,dict)
                or not isinstance(config.get('systemPrompt'),str) or not isinstance(proof,dict)
                or proof.get('target')!=sha(config['systemPrompt']) or not isinstance(proof.get('origins'),list)
                or not proof['origins'] or any(not isinstance(v,str) or len(v)!=64 or any(c not in '0123456789abcdef' for c in v) for v in proof['origins'])
                or not isinstance(permit,str) or not permit.isdecimal()
                or body.get('apiKey')!=os.getenv('FISHBOT_MODEL_KEY','')):raise web.HTTPConflict()
            non_persona=lambda value:{k:v for k,v in value.items() if k!='systemPrompt'}
            if non_persona(config)!=non_persona(manifest['config']):raise web.HTTPConflict()
            if body['revision']==manifest['revision']:
                if config!=manifest['config']:raise web.HTTPConflict()
                return web.json_response({'updatedRegion':'persona','unchanged':True})
            await db_helper.update_persona('platform',system_prompt=config['systemPrompt'])
            record=await db_helper.get_persona_by_id('platform')
            if record is None:raise web.HTTPConflict()
            replace_cached_persona(core.persona_mgr,'platform',record)
            settings['qq']={**settings['qq'],'config':copy.deepcopy(config),'revision':body['revision'],
                'systemChangePermitId':permit,'personaEdit':copy.deepcopy(proof)}
            manifest.update(config=copy.deepcopy(config),revision=body['revision'],systemChangePermitId=permit,personaEdit=copy.deepcopy(proof))
            behavior['refresh_base']('qq')
            return web.json_response({'updatedRegion':'persona'})
    async def update_tools(request):
        if request.headers.get('Authorization')!='Bearer '+manifest['token']:raise web.HTTPUnauthorized()
        body=await request.json()
        async with base_lock:
            from tool_config import apply_user_tools
            if body.get('apiKey')!=os.getenv('FISHBOT_MODEL_KEY',''):raise web.HTTPConflict()
            try:replacement=apply_user_tools(settings['qq'],body)
            except (ValueError,TypeError,KeyError):raise web.HTTPConflict()
            settings['qq']=replacement
            manifest.update(config=copy.deepcopy(replacement['config']),revision=replacement['revision'],toolsEdit=copy.deepcopy(replacement['toolsEdit']))
            behavior['refresh_base']('qq')
            return web.json_response({'updatedRegion':'tools'})
    async def health(request):
        if request.headers.get('Authorization')!='Bearer '+manifest['token']:raise web.HTTPUnauthorized()
        return web.json_response({'ready':True,'generation':manifest['generation'],'revision':manifest['revision'],'root':str(root),'pid':os.getpid(),'modelConfigured':bool(os.getenv('FISHBOT_MODEL_KEY')),'metrics':behavior['metrics'],'activity':behavior['activity']()})
    async def manage_data(request):
        if request.headers.get('Authorization')!='Bearer '+manifest['token']:raise web.HTTPUnauthorized()
        if request.headers.get('X-Member-Generation')!=str(manifest['generation']) or request.headers.get('X-Member-Revision')!=str(manifest['revision']):raise web.HTTPConflict()
        return web.json_response(behavior['manage']('qq',await request.json()))
    async def autonomous_work(request):
        if request.headers.get('Authorization')!='Bearer '+manifest['token']:raise web.HTTPUnauthorized()
        if request.headers.get('X-Member-Generation')!=str(manifest['generation']) or request.headers.get('X-Member-Revision')!=str(manifest['revision']):raise web.HTTPConflict()
        # Keep ordinary control requests bounded; full execute baselines need more room.
        data=await request.clone(client_max_size=4*1024*1024 if request.match_info['operation']=='execute' else 131072).json()
        try:return web.json_response(await behavior['autonomous']('qq',request.match_info['operation'],data))
        except (ValueError,KeyError,TypeError):raise web.HTTPConflict(reason='Autonomous work invalid or stale') from None
    app=web.Application(client_max_size=131072);app.router.add_get('/health',health);app.router.add_put('/base/persona',update_persona);app.router.add_put('/base/tools',update_tools);app.router.add_post('/behavior-data',manage_data);app.router.add_post('/autonomous/{operation}',autonomous_work)
    runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',manifest['aiPort']+1).start()
    task=asyncio.create_task(core.start())
    worker_lifecycle.install_shutdown(stop)
    try:await worker_lifecycle.wait_for_shutdown(task,stop)
    finally:
        await core.stop();task.cancel();await runner.cleanup();behavior['close']()

asyncio.run(main())
