"""Read durable receipts for both layers; no new model, timer or memory store."""
import asyncio,json
import aiohttp
from bilibili_tools import manifest


GUIDANCE = ('execution_experience 是自己的真实工具经历，不是当前平台观看历史。回顾做过什么时先用这些回执，'
            '需要更多再调用 hosted_recall_experience；能力已关闭不代表过去没有做过。'
            '保留实际时间、覆盖范围和失败/未知，不把工具返回的候选材料说成亲历，不能用当前热门代替回顾。'
            '按已确认成果、尝试及阻塞理解可续接的部分；经历是复盘依据，不是当前授权，也不要求重复原操作。'
            '跨会话 public_activity_only 仅允许说明已公开的动作/视频及覆盖范围，不能推测或披露原私聊请求。')


def context_projection(result,foundation):
    """Checkpoint-owned ordinary experience; live execution state stays live.

    Only automatic context is projected. Explicit recall and real tool results
    remain complete. The Journal freezes foundation until lawful retirement.
    """
    if not isinstance(result,dict):return result
    current={k:v for k,v in result.items() if k!='sampledAt'}
    rows=result.get('experiences')
    if not isinstance(rows,list):return current
    import copy
    def settled(row):
        return isinstance(row,dict) and row.get('execution')=='completed' and (row.get('receipt') or {}).get('status') in ('confirmed','not_applicable')
    if isinstance(foundation,dict) and 'execution_experience' not in foundation:
        foundation['execution_experience']={**copy.deepcopy(current),'experiences':[copy.deepcopy(row) for row in rows if settled(row)]}
    # Keep the complete current view, including newly completed prose and
    # future fields. Presentation deduplication follows Journal foundation
    # freezing, so it can only reference a basis actually visible on the wire.
    return copy.deepcopy(current)


async def recall(settings,event,scope,current,query='',limit=6,share=False,identity=None):
    if not current():raise asyncio.CancelledError('stale execution memory reader')
    if (settings.get('config',{}).get('behavior') or {}).get('memory') is False:
        return {'memoryEnabled':False,'experiences':[]}
    bridge=manifest(settings)
    if bridge is None:return {'status':'unavailable','reason':'memory_bridge_unavailable'}
    sender=str(event.get_sender_id()) if event is not None else '0'
    payload={'scope':scope,'senderId':sender,'query':str(query)[:100],'limit':max(1,min(12,limit)),'shareOwnerActivity':bool(share)}
    if scope=='self':
        if not identity:return {'status':'unavailable','reason':'missing_internal_work'}
        payload['work']={'id':identity['id'],'epoch':identity['epoch']}
    url=bridge['url'].rstrip('/')+'/internal/v1/bots/'+bridge['botId']+'/memory/experience'
    try:
        # No polling/retry. A degraded memory endpoint must not hold foreground
        # chats behind background tools or consume the five-second latency margin.
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1.2),trust_env=False,headers={'Authorization':'Bearer '+bridge['token']}) as client:
            async with client.post(url,json=payload,allow_redirects=False) as response:
                if response.status!=200:raise ValueError('memory_bridge_rejected')
                raw=bytearray()
                async for block in response.content.iter_chunked(4096):
                    raw.extend(block)
                    if len(raw)>20000:raise ValueError('memory_bridge_rejected')
                result=json.loads(raw)
        if not current():raise asyncio.CancelledError('stale execution memory response')
        if not isinstance(result,dict) or not isinstance(result.get('experiences'),list):raise ValueError('invalid_experience_response')
        return result
    except (aiohttp.ClientError,asyncio.TimeoutError,ValueError,TypeError):
        if not current():raise asyncio.CancelledError('stale execution memory reader')
        return {'status':'unavailable','reason':'experience_read_unconfirmed','meaning':'本轮未能读取经历，不代表没有经历；不要编造或宣称已经遗忘。'}


async def context(settings,event,scope,current,identity=None):
    owner=str((settings.get('config',{}).get('behavior') or {}).get('ownerUin') or '')
    return await recall(settings,event,scope,current,share=bool(owner and scope=='private:'+owner and event is not None and str(event.get_sender_id())==owner),identity=identity)


def tools(settings,event,scope,current,identity=None):
    # Bridge startup/recovery changes availability, not this read capability's
    # declaration. recall() checks the live bridge and Work on every call.
    if (settings.get('config',{}).get('behavior') or {}).get('memory') is False:return []
    from astrbot.core.agent.tool import FunctionTool
    class Recall(FunctionTool):
        async def call(self,context,query='',limit=8,shareOwnerActivity=False):
            return json.dumps(await recall(settings,event,scope,current,query,limit,shareOwnerActivity,identity),ensure_ascii=False)
    return [Recall(name='hosted_recall_experience',description='回顾自己在本系统真实做过的工具任务；读取持久回执，不调用外部平台，不需要重新开启原工具。query可用操作名、BV号或关键词；不匹配不代表从未做过。真实主人明确要求在群中介绍自己的公开任务时，可设shareOwnerActivity=true，只返回公开动作/覆盖范围，不返回私聊正文、私信或通知。普通群成员无跨会话私有查询权。',parameters={'type':'object','properties':{'query':{'type':'string','maxLength':100},'limit':{'type':'integer','minimum':1,'maximum':12},'shareOwnerActivity':{'type':'boolean'}},'additionalProperties':False})]
