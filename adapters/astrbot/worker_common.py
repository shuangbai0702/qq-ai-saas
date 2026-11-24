"""Shared runtime bootstrap, constant-time bearer auth, ingress retention, and graceful SQLite drain for AstrBot workers."""
import asyncio,hmac,inspect,os,sys
from pathlib import Path
from aiohttp import web


def prepare_environment(source,root):
    root=Path(root).resolve()
    sys.path.insert(0,source)
    os.environ['ASTRBOT_ROOT']=str(root)
    import runtime_bootstrap
    runtime_bootstrap.initialize_runtime_bootstrap()
    if os.getenv('FISHBOT_ASTRBOT_SLIM','1')!='0':
        from slim_bootstrap import install
        install()
    return root


def verify_bearer(request,expected_token):
    header=request.headers.get('Authorization') or ''
    expected='Bearer '+str(expected_token or '')
    if not expected_token or not hmac.compare_digest(header,expected):
        raise web.HTTPUnauthorized()


async def upsert_persona(db_helper,persona_id,prompt):
    existing=await db_helper.get_persona_by_id(persona_id)
    if existing:await db_helper.update_persona(persona_id,system_prompt=prompt,tools=[],skills=[])
    else:await db_helper.insert_persona(persona_id,prompt,tools=[],skills=[])


async def apply_persona_db_update(db_helper,persona_mgr,persona_id,prompt):
    from web_base import replace_cached_persona
    await db_helper.update_persona(persona_id,system_prompt=prompt)
    record=await db_helper.get_persona_by_id(persona_id)
    if record is None:raise web.HTTPConflict(reason='Persona projection missing')
    replace_cached_persona(persona_mgr,persona_id,record)
    return record


async def retain_platform_ingress(behavior,platform_id,platform,message):
    raw=getattr(message,'raw_message',{}) or {}
    if raw.get('post_type','message')!='message' or getattr(message,'_hosted_ingress_retained',False):
        return True
    retained=behavior['retain_ingress'](platform_id,platform.create_event(message))
    if inspect.isawaitable(retained):retained=await retained
    return bool(retained)


async def drain_behavior_stores(behavior):
    """Join local SQLite background worker threads before process shutdown."""
    from semantic_async import drain_store,settled
    async def drain_all():
        for store in list(behavior['stores'].values()):
            await drain_store(store)
    await settled(asyncio.create_task(drain_all()))
