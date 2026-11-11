"""Authoritative cycle reservation through the existing runtime bridge."""
import asyncio
import aiohttp
from bilibili_tools import manifest

async def update(settings,identity,logical,phase):
    # Older callers without an autonomous cycle keep their existing contract.
    if not identity.get('cycleId'):return
    bridge=manifest(settings)
    if not bridge:raise RuntimeError('external_action_budget_bridge_unavailable')
    url=bridge['url'].rstrip('/')+'/internal/v1/bots/'+bridge['botId']+'/external-action-budget'
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8),trust_env=False,headers={'Authorization':'Bearer '+bridge['token']}) as client:
        async with client.post(url,json={'workId':identity['id'],'epoch':identity['epoch'],'logicalActionId':logical,'phase':phase},allow_redirects=False) as response:
            if response.status>=400:raise RuntimeError('external_action_budget_rejected')
            return await response.json()

async def settle(settings,identity,logical,phase):
    # A failed settlement leaves its durable reservation charged. Never turn
    # a real protocol success into a retryable send because its report failed.
    try:await update(settings,identity,logical,phase)
    except (Exception,asyncio.CancelledError):pass

async def effects(settings,identity,existing):
    if not identity.get('cycleId'):return existing
    bridge=manifest(settings)
    if not bridge:return existing
    try:
        url=bridge['url'].rstrip('/')+'/internal/v1/bots/'+bridge['botId']+'/external-effects'
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8),trust_env=False,headers={'Authorization':'Bearer '+bridge['token']}) as client:
            async with client.post(url,json={'workId':identity['id']},allow_redirects=False) as response:
                if response.status>=400:return existing
                value=await response.json()
                return [e for e in existing if e.get('source')!='bilibili']+value.get('effects',[])
    except (Exception,asyncio.CancelledError):return existing
