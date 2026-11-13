"""Exact-ID goal tools over the existing authenticated runtime bridge."""
import asyncio,json
import aiohttp
from bilibili_tools import manifest

def tools(settings,event,db,current):
    if event is None or not manifest(settings):return []
    from astrbot.core.agent.tool import FunctionTool
    class GoalTool(FunctionTool):
        async def call(self,context,**args):
            if getattr(getattr(context,'context',None),'event',None) is not event or not current():raise asyncio.CancelledError('stale goal Work')
            bridge=manifest(settings)
            work=event.get_extra('_hosted_pipeline')
            internal=event.get_extra('_hosted_internal_work')
            if not bridge or work is None:return json.dumps({'status':'rejected','reason':'missing_trusted_work'})
            if internal:
                if internal.get('id')!=work.event_id:return json.dumps({'status':'rejected','reason':'work_identity_mismatch'})
                trusted={'scope':work.scope if work.scope.startswith(('private:','group:')) else 'self','senderId':'0','work':{'id':internal['id'],'epoch':internal['epoch']}}
            else:
                trusted={'scope':work.scope,'senderId':str(event.get_sender_id())}
            payload={'trusted':trusted}
            operation='read'
            if self.name=='update_autonomous_goal':operation='update';payload['update']=args
            try:
                from bridge_http import post
                status,value=await post(bridge,'autonomous-goals/'+operation,payload)
                if not current():raise asyncio.CancelledError('stale goal result')
                if status>=400:return json.dumps({'status':'rejected','reason':'goal_bridge_rejected','detail':value},ensure_ascii=False)
                return json.dumps(value,ensure_ascii=False)
            except (aiohttp.ClientError,asyncio.TimeoutError,ValueError):
                return json.dumps({'status':'unknown','reason':'goal_bridge_unavailable','instruction':'先按原goalId读取权威revision，不得假报修改成功或盲目重复修改'})
    changes={'type':'object','properties':{'title':{'type':'string','minLength':1,'maxLength':1000},'reason':{'type':'string','minLength':1,'maxLength':12000},'successCondition':{'type':'string','minLength':1,'maxLength':12000},'nextIntention':{'type':'string','maxLength':12000},'progress':{'type':'string','maxLength':12000},'status':{'type':'string','enum':['active','paused','completed','abandoned']}},'additionalProperties':False,'minProperties':1}
    return [GoalTool(name='read_autonomous_goals',description='完整读取当前授权范围内未终结目标和相关Action/工具结果/unfinished；没有截断。保留原始来源与约束，不授予外发权限。',parameters={'type':'object','properties':{},'additionalProperties':False}),
            GoalTool(name='update_autonomous_goal',description='按精确goalId和读取到的expectedRevision CAS修改目标当前表述、动机、进度、完成条件、下一意图或状态。source.originalGoal保留历史来源，用户明确限制持续有效；现行完成条件以最新已确认revision为准，旧表述不得覆盖合法修订。模型暂停paused可恢复；用户或模式控制暂停paused_user/paused_mode须由原控制入口解除。结果冲突须读取后重新判断；真实状态以返回为准。',parameters={'type':'object','properties':{'goalId':{'type':'string'},'expectedRevision':{'type':'integer','minimum':1},'changes':changes},'required':['goalId','expectedRevision','changes'],'additionalProperties':False})]
