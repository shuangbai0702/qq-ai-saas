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
        from bridge_http import post
        body={'workId':identity['id'],'epoch':identity['epoch'],'requestId':request_id,'phase':phase,'source':'main','tokens':tokens}
        status,_=await post(bridge,'model-request-budget',body)
        if status>=400:raise RuntimeError('model_budget_'+phase+'_rejected:'+str(status))
    return transition
