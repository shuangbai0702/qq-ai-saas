"""Hosted cohort: trusted code only. Keys arrive via authenticated local IPC, not disk/env.
Uses pinned AstrBot's native profiles, UMO routing, event draining and providers.
"""
import os,sys,json,copy,asyncio,signal,logging,time
from pathlib import Path
def validate_member(id,body):
    """Validate authenticated IPC before changing a live member; not a sandbox."""
    import math,re
    def require(ok):
        if not ok:raise ValueError('Invalid member configuration')
    def text(value,limit,empty=True):
        return isinstance(value,str) and (empty or bool(value)) and len(value)<=limit
    def tree(value,depth=0):
        require(depth<=16)
        if isinstance(value,dict):
            require(len(value)<=256)
            for key,item in value.items():require(text(key,256));tree(item,depth+1)
        elif isinstance(value,list):
            require(len(value)<=256)
            for item in value:tree(item,depth+1)
        elif isinstance(value,str):require(len(value)<=20000)
        else:require(value is None or type(value) is bool or type(value) is int and abs(value)<=9007199254740991 or type(value) is float and math.isfinite(value))
    require(isinstance(id,str) and re.fullmatch(r'[a-zA-Z0-9-]{1,80}',id) is not None)
    require(isinstance(body,dict));tree(body)
    for key in ('generation','revision'):
        require(type(body.get(key)) is int and 0<=body[key]<=9007199254740991)
    require(text(body.get('prompt'),20000,False) and text(body.get('key',''),8192))
    require(text(body.get('runtimeRoot'),4096,False) and '\x00' not in body['runtimeRoot'])
    require(text(body.get('uin',''),20) and (not body.get('uin') or re.fullmatch(r'\d{5,20}',body['uin']) is not None))
    require('botId' not in body or body['botId']==id)
    groups=body.get('groups')
    require(isinstance(groups,list) and len(groups)<=100 and all(isinstance(g,str) and re.fullmatch(r'\d{4,20}',g) for g in groups))
    cfg=body.get('config');platform=body.get('platform');behavior=body.get('behaviorConfig')
    require(isinstance(cfg,dict) and isinstance(platform,dict) and isinstance(behavior,dict))
    for key in ('dashboard','platform_settings','provider_settings','provider_ltm_settings','agent_runner'):
        require(key not in cfg or isinstance(cfg[key],dict))
    agent=cfg.get('agent_runner',{})
    require(isinstance(agent.get('config',{}),dict))
    for key in ('model','persona','compression','misc'):
        require(key not in agent.get('config',{}) or isinstance(agent['config'][key],dict))
    for key in ('platform','provider','provider_sources'):
        require(isinstance(cfg.get(key),list) and all(isinstance(item,dict) for item in cfg[key]))
    require(not body.get('key') or len(cfg['provider'])==1)
    for provider in cfg['provider']:
        for key in ('id','type','provider_type','api_base','model'):
            require(text(provider.get(key),2000,False))
        require(isinstance(provider.get('key'),list) and all(text(k,8192) for k in provider['key']))
    require(platform.get('type')=='aiocqhttp' and platform.get('enable') is True)
    require(platform.get('ws_reverse_host')=='127.0.0.1')
    require(type(platform.get('ws_reverse_port')) is int and 1<=platform['ws_reverse_port']<=65535)
    require(text(platform.get('ws_reverse_token'),256,False))
    require('behavior' not in behavior or isinstance(behavior['behavior'],dict))

source,root=sys.argv[1],Path(sys.argv[2]).resolve()
sys.path.insert(0,source);os.environ['ASTRBOT_ROOT']=str(root)
manifest=json.loads((root/'group.json').read_text(encoding='utf8'))
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
if os.getenv('FISHBOT_ASTRBOT_SLIM','1')!='0':
    from slim_bootstrap import install
    install()
from astrbot.core import db_helper,LogBroker,LogManager,logger
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from aiohttp import web
from tenant_tools import install as guard_tools
members={};guard_tools(members)
# Shared raw framework logs may contain another tenant's prompt/response. Never
# fan them into per-tenant logs. Each member exposes content-free counters instead.
logging.disable(logging.CRITICAL)

async def main():
    broker=LogBroker();LogManager.set_queue_handler(logger,broker)
    from model_egress import install as guard_model_egress
    guard_model_egress()
    core=AstrBotCoreLifecycle(broker,db_helper);await core.initialize()
    from behavior import install_runtime
    behavior_settings={}
    behavior=install_runtime(core,behavior_settings)
    # Remove only disposable profile metadata after crash; histories and facts remain.
    for conf_id in list(core.astrbot_config_mgr.confs):
        if conf_id!='default':await core.astrbot_config_mgr.delete_conf(conf_id)
    lock=asyncio.Lock();errors={};tombstones={};started=time.time()
    def merge(dst,src):
        for k,v in src.items():
            if isinstance(v,dict) and isinstance(dst.get(k),dict):merge(dst[k],v)
            else:dst[k]=copy.deepcopy(v)
    async def unload(id,permanent=False):
        # Invalidate learning callbacks before draining provider/platform awaits.
        behavior_settings.pop(id,None)
        active_store=behavior['stores'].get(id)
        if active_store:active_store.reflection.close();active_store.social.close()
        await core.event_bus.stop_platform(id)
        await core.platform_manager.terminate_platform(id)
        await core.provider_manager.terminate_provider('provider-'+id)
        behavior_settings.pop(id,None)
        behavior['release'](id)
        if id in members:
            m=members.pop(id);tombstones[id]=(m['generation'],m['revision'])
            await core.umop_config_router.delete_route(id+'::')
            await core.astrbot_config_mgr.delete_conf(m['confId'])
        core.astrbot_config['platform']=[p for p in core.astrbot_config['platform'] if p.get('id')!=id]
        core.provider_manager.providers_config[:]=[p for p in core.provider_manager.providers_config if p.get('id')!='provider-'+id]
        if permanent:
            await db_helper.delete_persona('persona-'+id)
    async def install(id,body):
        cfg=copy.deepcopy(dict(core.astrbot_config));merge(cfg,body['config'])
        cfg['platform']=[];cfg['provider']=[];cfg['disable_builtin_commands']=True;cfg['plugin_set']=[]
        cfg['agent_runner']['config']['persona']['persona_id']='persona-'+id
        cfg['agent_runner']['config']['model']['provider_id']='provider-'+id if body.get('key') else ''
        prompt=body['prompt'];persona='persona-'+id
        if await db_helper.get_persona_by_id(persona):await db_helper.update_persona(persona,system_prompt=prompt,tools=[],skills=[])
        else:await db_helper.insert_persona(persona,prompt,tools=[],skills=[])
        await core.persona_mgr.initialize()
        conf_id=await core.astrbot_config_mgr.create_conf(cfg,name=id)
        member={k:v for k,v in body.items() if k not in ('key','config','prompt')}
        member.update(confId=conf_id,events=0,modelConfigured=bool(body.get('key')))
        members[id]=member
        behavior_settings[id]={'root':body['runtimeRoot'],'botId':id,'config':body['behaviorConfig'],'generation':body['generation'],'revision':body['revision']}
        if body.get('key'):
            provider=copy.deepcopy(body['config']['provider'][0]);provider.update(id='provider-'+id,key=[body['key']])
            # load_provider initializes the real SDK but does not save the key.
            # Never call create/update_provider: those persist their input.
            await core.provider_manager.load_provider(provider)
            if 'provider-'+id not in core.provider_manager.inst_map:raise RuntimeError('Provider did not initialize')
            core.provider_manager.providers_config.append({**provider,'key':['[IPC-only]']})
        await core.umop_config_router.update_route(id+'::',conf_id)
        await core.reload_pipeline_scheduler(conf_id)
        platform_config={**body['platform'],'id':id}
        core.astrbot_config['platform'].append(platform_config)
        await core.platform_manager.load_platform(platform_config)
        platform=next(p for p in core.platform_manager.get_insts() if p.meta().id==id)
        original=platform.handle_msg
        async def gated(message):
            m=members.get(id)
            if m is not member or id in core.event_bus.stopped_platforms:return
            if m.get('uin') and str(message.self_id)!=str(m['uin']):return
            allowed=set(m.get('groups',[]))
            if allowed and message.group_id and str(message.group_id) not in allowed:return
            if m['modelConfigured'] and 'provider-'+id not in core.provider_manager.inst_map:return
            m['events']+=1
            await original(message)
        platform.handle_msg=gated
        core.event_bus.stopped_platforms.discard(id)
        errors.pop(id,None)
    @web.middleware
    async def auth(request,handler):
        if request.headers.get('Authorization')!='Bearer '+manifest['token']:raise web.HTTPUnauthorized()
        if request.method!='GET' and request.headers.get('X-Group-Generation')!=str(manifest['generation']):raise web.HTTPConflict()
        return await handler(request)
    async def health(request):
        return web.json_response({'ready':True,'pid':os.getpid(),'generation':manifest['generation'],'capacity':manifest['capacity'],'members':{id:{'generation':m['generation'],'revision':m['revision'],'events':m['events'],'modelConfigured':m['modelConfigured']} for id,m in members.items()},'metrics':behavior['metrics'],'memberErrors':errors,'startedAt':started})
    async def put(request):
        id=request.match_info['id']
        try:
            body=await request.json()
            validate_member(id,body)
        except (ValueError,TypeError,RecursionError):
            raise web.HTTPBadRequest(reason='Invalid member configuration') from None
        async with lock:
            current=members.get(id)
            stamp=(body['generation'],body['revision'])
            if current:
                prev=(current['generation'],current['revision'])
                if stamp<prev:raise web.HTTPConflict()
                if stamp==prev:return web.json_response({'generation':stamp[0],'revision':stamp[1],'unchanged':True})
            elif id in tombstones and stamp<tombstones[id]:raise web.HTTPConflict()
            if not current and len(members)>=manifest['capacity']:raise web.HTTPConflict(reason='Group capacity reached')
            try:
                if current:await unload(id)
                await install(id,body)
                return web.json_response({'generation':stamp[0],'revision':stamp[1]})
            except Exception:
                errors[id]='Member apply failed; member paused'
                await unload(id)
                raise web.HTTPBadRequest(reason='Member apply failed; check provider configuration')
    async def remove(request):
        id=request.match_info['id'];generation=int(request.headers.get('X-Member-Generation','-1'))
        async with lock:
            if id in members and generation<members[id]['generation']:raise web.HTTPConflict()
            await unload(id,request.query.get('permanent')=='true')
            old=tombstones.get(id,(-1,-1));tombstones[id]=(max(generation,old[0]),old[1])
        return web.json_response({'removed':id})
    async def manage_data(request):
        id=request.match_info['id']
        async with lock:
            m=members.get(id)
            if not m or request.headers.get('X-Member-Generation')!=str(m['generation']) or request.headers.get('X-Member-Revision')!=str(m['revision']):raise web.HTTPConflict()
            return web.json_response(behavior['manage'](id,await request.json()))
    app=web.Application(middlewares=[auth],client_max_size=131072);app.router.add_post('/members/{id}/behavior-data',manage_data);app.router.add_get('/health',health);app.router.add_put('/members/{id}',put);app.router.add_delete('/members/{id}',remove)
    runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',manifest['healthPort']).start()
    task=asyncio.create_task(core.start());stop=asyncio.Event()
    for sig in [signal.SIGINT,signal.SIGTERM]:
        try:asyncio.get_running_loop().add_signal_handler(sig,stop.set)
        except(NotImplementedError,RuntimeError):pass
    try:await stop.wait()
    finally:await core.stop();task.cancel();await runner.cleanup();behavior['close']()
asyncio.run(main())
