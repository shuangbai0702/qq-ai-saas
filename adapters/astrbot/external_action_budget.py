"""Authoritative cycle reservation through the existing runtime bridge."""
import asyncio
import aiohttp
from bilibili_tools import manifest

async def update(settings,identity,logical,phase):
    # Older callers without an autonomous cycle keep their existing contract.
    if not identity.get('cycleId'):return
    bridge=manifest(settings)
    if not bridge:raise RuntimeError('external_action_budget_bridge_unavailable')
    from bridge_http import post
    status,value=await post(bridge,'external-action-budget',{'workId':identity['id'],'epoch':identity['epoch'],'logicalActionId':logical,'phase':phase})
    if status>=400:raise RuntimeError('external_action_budget_rejected')
    return value

async def settle(settings,identity,logical,phase):
    # A failed settlement leaves its durable reservation charged. Never turn
    # a real protocol success into a retryable send because its report failed.
    task=asyncio.create_task(update(settings,identity,logical,phase))
    try:await asyncio.shield(task)
    except asyncio.CancelledError:
        # Finish this bounded receipt before returning cancellation to Owner.
        while not task.done():
            try:await asyncio.shield(task)
            except asyncio.CancelledError:continue
            except Exception:break
        # Retrieve failures even if the receipt finished between cancellations.
        if task.done() and not task.cancelled():task.exception()
        raise
    except Exception:pass

async def effects(settings,identity,existing):
    if not identity.get('cycleId'):return existing
    bridge=manifest(settings)
    if not bridge:return existing
    try:
        from bridge_http import post
        status,value=await post(bridge,'external-effects',{'workId':identity['id']})
        if status>=400:return existing
        return [e for e in existing if e.get('source')!='bilibili']+value.get('effects',[])
    except Exception:return existing
