"""Trusted local host bridge; identities remain bound to the original Work."""
import asyncio,hashlib,json
from pathlib import Path
from urllib.parse import urlsplit
import aiohttp

def manifest(settings):
    file=Path(settings['root'])/'ai'/'dsh-bridge.json'
    if not file.exists():return None
    if file.stat().st_size>8192:raise ValueError('dsh_manifest_too_large')
    value=json.loads(file.read_text(encoding='utf8'))
    url=urlsplit(value.get('url',''))
    if (value.get('version')!=1 or value.get('mode') not in ('local','production')
        or any(value.get(k)!=settings.get(k) for k in ('botId','tenantId','generation','revision'))
        or url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port
        or url.username or url.password or url.query or url.fragment or url.path!='/delegate'
        or not isinstance(value.get('token'),str) or len(value['token'])!=64):
        raise ValueError('dsh_manifest_invalid')
    return value

def tools(settings,event,current,db,bridge):
    from astrbot.core.agent.tool import FunctionTool
    class Delegate(FunctionTool):
        hosted_effect=False
        async def call(self,context,goal,background='',constraints=None):
            if context.context.event is not event or not current():raise asyncio.CancelledError('stale DSH Work')
            if manifest(settings)!=bridge:raise ValueError('dsh_configuration_changed')
            if not isinstance(goal,str) or not goal.strip() or len(goal)>12000:raise ValueError('invalid_delegation_goal')
            if not isinstance(background,str) or len(background)>16000:raise ValueError('invalid_delegation_background')
            constraints=[] if constraints is None else constraints
            if not isinstance(constraints,list) or len(constraints)>32 or any(not isinstance(x,str) or len(x)>2000 for x in constraints):raise ValueError('invalid_delegation_constraints')
            work=event.get_extra('_hosted_pipeline')
            if work is None or event.get_extra('_hosted_internal_work') or event.get_extra('_hosted_tool_completion'):raise ValueError('dsh_foreground_work_required')
            from group_actions import tool_action_id
            call_id=tool_action_id.get()
            if not isinstance(call_id,str) or not call_id:raise ValueError('dsh_tool_call_identity_required')
            work_id='foreground:'+hashlib.sha256(json.dumps([work.bot_id,work.scope,work.subject,work.event_id],ensure_ascii=False).encode()).hexdigest()
            record={'bot_id':settings['botId'],'tenant_id':settings['tenantId'],'generation':settings['generation'],'config_revision':settings['revision'],
                'work_id':work_id,'authority':'foreground','origin_scope':work.scope,'origin_event':work.event_id,'sender_id':work.subject}
            from pipeline_tools import validate_parent
            validate_parent(db,settings,record)
            payload={'parent':record,'delegationId':call_id,'input':{'goal':goal,'background':background,'constraints':constraints}}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=150)) as client:
                async with client.post(bridge['url'],json=payload,headers={'Authorization':'Bearer '+bridge['token']},allow_redirects=False) as response:
                    chunks=[];size=0
                    async for chunk in response.content.iter_chunked(8192):
                        size+=len(chunk)
                        if size>131072:raise ValueError('dsh_result_too_large')
                        chunks.append(chunk)
                    if response.status!=200:raise ValueError('dsh_delegation_failed')
                    result=json.loads(b''.join(chunks))
            if not current():raise asyncio.CancelledError('stale DSH result')
            validate_parent(db,settings,record)
            if set(result)!={'result','evidence','unfinished','limitations'}:raise ValueError('dsh_final_contract_invalid')
            return json.dumps(result,ensure_ascii=False)
    return [Delegate(name='dsh_delegate',description='将联网搜索、网页查阅或B站任务完整委托给DSH。只提供目标、必要背景和约束，不指定工具或执行步骤；等待最终结果，不查询进度。',parameters={
        'type':'object','additionalProperties':False,'required':['goal'],'properties':{
        'goal':{'type':'string','maxLength':12000},'background':{'type':'string','maxLength':16000},'constraints':{'type':'array','maxItems':32,'items':{'type':'string','maxLength':2000}}}})]
