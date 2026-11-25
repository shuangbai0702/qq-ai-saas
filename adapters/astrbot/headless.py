"""Thin launcher over upstream AstrBotCoreLifecycle, without Dashboard or Electron."""
import sys,os,json,asyncio,copy
from pathlib import Path
import worker_lifecycle
import worker_common
worker_lifecycle.initialize()
root=worker_common.prepare_environment(sys.argv[1],sys.argv[2])
manifest=json.loads((root/'runtime.json').read_text(encoding='utf8'))
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
    await worker_common.upsert_persona(db_helper,'platform',prompt)
    await core.persona_mgr.initialize()
    from behavior import install_runtime
    settings={'qq':{'root':str(root.parent),'tenantId':manifest.get('tenantId'),'botId':manifest.get('botId',root.parent.name),'config':manifest.get('config',{}),'generation':manifest['generation'],'revision':manifest['revision'],'systemChangePermitId':manifest.get('systemChangePermitId'),'personaEdit':manifest.get('personaEdit'),'characterCardsEdit':manifest.get('characterCardsEdit'),'expectedUin':manifest.get('expectedUin',''),'_base_owner':object()}}
    behavior=install_runtime(core,settings)
    # Upstream allowlist gates private sessions too. Restrict only group input at
    # the OneBot adapter boundary so private chat remains independently usable.
    from native_ingress import admitted,bind as bind_ingress
    for platform in core.platform_manager.get_insts():
        if hasattr(platform,'handle_msg'):
            original=platform.handle_msg
            async def gated(message,original=original,platform=platform):
                if manifest.get('expectedUin') and str(message.self_id)!=str(manifest['expectedUin']):return
                if not admitted(getattr(message,'raw_message',{}) or {},manifest.get('config',{})):return
                try:retained=await worker_common.retain_platform_ingress(behavior,'qq',platform,message)
                except Exception as error:
                    worker_lifecycle.record('ingress_retention_failed',member='qq',reason=type(error).__name__)
                    return
                if not retained:return
                await original(message)
            platform.handle_msg=gated
        if hasattr(platform,'convert_message'):
            bind_ingress(platform,lambda raw:behavior['acquire_raw_ingress']('qq',raw),
                lambda raw:(not manifest.get('expectedUin') or str(raw.get('self_id'))==str(manifest['expectedUin']))
                and admitted(raw,manifest.get('config',{})),
                prepare=lambda raw:behavior['prepare_raw_ingress']('qq',raw))
    stop=asyncio.Event()
    base_lock=asyncio.Lock()

    @web.middleware
    async def auth(request,handler):
        worker_common.verify_bearer(request,manifest['token'])
        return await handler(request)

    async def update_persona(request):
        body=await request.json()
        async with base_lock:
            from web_base import sha
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
            await worker_common.apply_persona_db_update(db_helper,core.persona_mgr,'platform',config['systemPrompt'])
            settings['qq']={**settings['qq'],'config':copy.deepcopy(config),'revision':body['revision'],
                'systemChangePermitId':permit,'personaEdit':copy.deepcopy(proof)}
            manifest.update(config=copy.deepcopy(config),revision=body['revision'],systemChangePermitId=permit,personaEdit=copy.deepcopy(proof))
            behavior['refresh_base']('qq')
            return web.json_response({'updatedRegion':'persona'})

    async def update_cards(request):
        body=await request.clone(client_max_size=524288).json()
        async with base_lock:
            from character_cards import apply_user_cards
            if body.get('apiKey')!=os.getenv('FISHBOT_MODEL_KEY',''):raise web.HTTPConflict()
            try:replacement=apply_user_cards(settings['qq'],body)
            except (ValueError,TypeError,KeyError):raise web.HTTPConflict()
            try:behavior['validate_cards']('qq',replacement)
            except (ValueError,RuntimeError):raise web.HTTPConflict(reason='Character card Journal projection rejected') from None
            settings['qq']=replacement
            manifest.update(config=copy.deepcopy(replacement['config']),revision=replacement['revision'],characterCardsEdit=copy.deepcopy(replacement['characterCardsEdit']),systemChangePermitId=replacement.get('systemChangePermitId'))
            behavior['refresh_base']('qq')
            return web.json_response({'updatedRegion':'character_cards','generation':replacement['generation'],'revision':replacement['revision'],'target':replacement['characterCardsEdit']['target']})

    async def update_tools(request):
        body=await request.json()
        async with base_lock:
            from tool_config import apply_user_tools
            if body.get('apiKey')!=os.getenv('FISHBOT_MODEL_KEY',''):raise web.HTTPConflict()
            try:replacement=apply_user_tools(settings['qq'],body)
            except (ValueError,TypeError,KeyError):raise web.HTTPConflict()
            settings['qq']=replacement
            manifest.update(config=copy.deepcopy(replacement['config']),revision=replacement['revision'],toolsEdit=copy.deepcopy(replacement['toolsEdit']),systemChangePermitId=replacement.get('systemChangePermitId'))
            behavior['refresh_base']('qq')
            return web.json_response({'updatedRegion':'tools'})

    async def health(request):
        return web.json_response({'ready':True,'generation':manifest['generation'],'revision':manifest['revision'],'root':str(root),'pid':os.getpid(),'modelConfigured':bool(os.getenv('FISHBOT_MODEL_KEY')),'metrics':behavior['metrics'],'activity':behavior['activity']()})

    async def manage_data(request):
        if request.headers.get('X-Member-Generation')!=str(manifest['generation']) or request.headers.get('X-Member-Revision')!=str(manifest['revision']):raise web.HTTPConflict()
        return web.json_response(behavior['manage']('qq',await request.json()))

    async def autonomous_work(request):
        if request.headers.get('X-Member-Generation')!=str(manifest['generation']) or request.headers.get('X-Member-Revision')!=str(manifest['revision']):raise web.HTTPConflict()
        data=await request.clone(client_max_size=4*1024*1024 if request.match_info['operation']=='execute' else 131072).json()
        try:return web.json_response(await behavior['autonomous']('qq',request.match_info['operation'],data))
        except (ValueError,KeyError,TypeError):raise web.HTTPConflict(reason='Autonomous work invalid or stale') from None

    app=web.Application(middlewares=[auth],client_max_size=131072);app.router.add_get('/health',health);app.router.add_put('/base/persona',update_persona);app.router.add_put('/base/tools',update_tools)
    app.router.add_put('/base/character-cards',update_cards);app.router.add_post('/behavior-data',manage_data);app.router.add_post('/autonomous/{operation}',autonomous_work)
    runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',manifest['aiPort']+1).start()
    task=asyncio.create_task(core.start())
    worker_lifecycle.install_shutdown(stop)
    try:await worker_lifecycle.wait_for_shutdown(task,stop)
    finally:
        core.event_bus.stopped_platforms.add('qq')
        try:await worker_common.drain_behavior_stores(behavior)
        finally:
            await core.stop();task.cancel();await runner.cleanup();behavior['close']()

asyncio.run(main())
