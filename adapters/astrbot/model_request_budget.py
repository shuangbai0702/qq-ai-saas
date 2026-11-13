"""Per-request bridge into the existing authoritative autonomy cycle budget."""
import asyncio
import aiohttp
from bilibili_tools import manifest


def bind(settings,identity,current):
    if not identity.get('cycleId'):return None
    async def transition(request_id,phase,tokens):
        # Settlement must survive a stale execution lease: the server verifies
        # ownership and permits receipts for an already reserved request.
        if phase=='reserve' and not current():raise asyncio.CancelledError('stale model budget owner')
        bridge=manifest(settings)
        if not bridge:raise RuntimeError('model_budget_bridge_unavailable')
        url=bridge['url'].rstrip('/')+'/internal/v1/bots/'+bridge['botId']+'/model-request-budget'
        body={'workId':identity['id'],'epoch':identity['epoch'],'requestId':request_id,'phase':phase,'source':'main','tokens':tokens}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8),trust_env=False,headers={'Authorization':'Bearer '+bridge['token']}) as client:
            async with client.post(url,json=body,allow_redirects=False) as response:
                if response.status>=400:raise RuntimeError('model_budget_'+phase+'_rejected:'+str(response.status))
                await response.json()
    return transition
