"""Trusted local host bridge; identities remain bound to the original Work."""
import asyncio,hashlib,json
from pathlib import Path
from urllib.parse import urlsplit
import aiohttp
from contextlib import asynccontextmanager

def manifest(settings):
    file=Path(settings['root'])/'ai'/'dsh-bridge.json'
    if not file.exists():return None
    if file.stat().st_size>8192:raise ValueError('dsh_manifest_too_large')
    value=json.loads(file.read_text(encoding='utf8'))
    url=urlsplit(value.get('url',''))
    if (value.get('version')!=2 or value.get('mode') not in ('local','production')
        or any(value.get(k)!=settings.get(k) for k in ('botId','tenantId','generation','revision'))
        or url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port
        or url.username or url.password or url.query or url.fragment or url.path!='/delegate'
        or not isinstance(value.get('token'),str) or len(value['token'])!=64):
        raise ValueError('dsh_manifest_invalid')
    return value

def parent_record(settings,event,current,db,context):
    if context.context.event is not event or not current():raise asyncio.CancelledError('stale DSH Work')
    work=event.get_extra('_hosted_pipeline');identity=event.get_extra('_hosted_internal_work')
    if work is None or event.get_extra('_hosted_tool_completion'):raise ValueError('dsh_foreground_work_required')
    if work.bot_id!=settings['botId']:raise ValueError('dsh_read_identity_mismatch')
    record={'bot_id':settings['botId'],'tenant_id':settings['tenantId'],'generation':settings['generation'],'config_revision':settings['revision'],
            'origin_scope':work.scope,'origin_event':work.event_id,'sender_id':work.subject}
    if identity:
        if not event.get_extra('_hosted_main_activation') or identity.get('principal')!='autonomous-service':raise ValueError('dsh_main_work_required')
        if any(identity.get(k)!=settings.get(k) for k in ('botId','tenantId','generation','revision')) or identity.get('id')!=work.event_id or work.subject!='service:'+settings['botId']:
            raise ValueError('dsh_autonomous_identity_mismatch')
        record.update(authority='autonomous',work_id=identity['id'],mode_epoch=identity['epoch'])
    else:
        if work.bot_id!=settings['botId'] or str(event.get_sender_id())!=str(work.subject):raise ValueError('dsh_read_identity_mismatch')
        record.update(authority='foreground',work_id='foreground:'+hashlib.sha256(json.dumps([work.bot_id,work.scope,work.subject,work.event_id],ensure_ascii=False).encode()).hexdigest())
    from pipeline_tools import validate_parent
    validate_parent(db,settings,record)
    return record


class ModelBudget:
    """Borrow the active RunControl; pending reservations die with this call."""
    def __init__(self,control):self.control=control;self.pending={}
    async def transition(self,frame):
        from native_internal import WorkBudgetExceeded
        c=self.control;phase=frame.get('phase');key=frame.get('requestId')
        if not isinstance(key,str) or not key or len(key)>300:raise ValueError('dsh_budget_identity')
        if phase=='reserve':
            c.check()
            if key in self.pending:raise ValueError('dsh_budget_duplicate')
            if c.used_models>=c.model_calls:raise WorkBudgetExceeded('model_call_budget')
            inputs=frame.get('inputTokens');desired=frame.get('outputTokens',c.output_tokens)
            if type(inputs) is not int or inputs<0 or type(desired) is not int or desired<=0:raise ValueError('dsh_budget_estimate')
            cap=min(c.output_tokens,desired,c.tokens-c.used_tokens-inputs)
            if cap<min(512,c.output_tokens):raise WorkBudgetExceeded('request_token_budget')
            estimate=inputs+cap
            if c.model_budget:
                await c.model_budget(key,'reserve',estimate)
                try:c.check()
                except BaseException:
                    await c.model_budget(key,'released',0)
                    raise
            c.used_models+=1;c.used_tokens+=estimate;c.output_caps.append(cap);self.pending[key]=estimate;c.progress()
            return {'maxTokens':cap}
        if phase not in ('settled','unknown','released') or key not in self.pending:raise ValueError('dsh_budget_reservation_missing')
        estimate=self.pending[key];actual=frame.get('tokens')
        tokens=0 if phase=='released' else actual if phase=='settled' and type(actual) is int and actual>=0 else estimate
        if c.model_budget:await c.model_budget(key,phase,tokens)
        self.pending.pop(key);c.used_tokens+=tokens-estimate
        if phase=='released':c.used_models-=1
        elif phase!='settled' or type(actual) is not int:c.estimated_usage=True
        c.progress();return {}


@asynccontextmanager
async def budget_scope(control):
    budget=ModelBudget(control) if control is not None else None
    try:yield budget
    finally:
        if budget:
            # Lost transport cannot prove dispatch did not happen. The existing
            # cycle reservation and RunControl both retain conservative usage.
            for key in list(budget.pending):
                control.estimated_usage=True
                try:await asyncio.shield(budget.transition({'phase':'unknown','requestId':key}))
                except Exception:control.failure='model_budget_settlement_unknown'


def tools(settings,event,current,db,bridge,social=None):
    from astrbot.core.agent.tool import FunctionTool
    class Delegate(FunctionTool):
        hosted_effect=False
        @property
        def hosted_final_text(self):return True
        async def call(self,context,goal,background='',constraints=None):
            try:return await self.run(context,goal,background,constraints)
            except asyncio.CancelledError:raise
            except Exception as error:
                from native_internal import WorkStopped,WorkBudgetExceeded
                if isinstance(error,(WorkStopped,WorkBudgetExceeded)):raise
                from model_cache import record_diagnostic
                from qq_diagnostics import failure_notice
                record_diagnostic(settings,{'kind':'dsh_delegation_error','workId':getattr(event.get_extra('_hosted_pipeline'),'event_id',''),'reason':type(error).__name__})
                return failure_notice(type(error).__name__,stage='delegation')
        async def run(self,context,goal,background='',constraints=None,_completion=None):
            if context.context.event is not event or not current():raise asyncio.CancelledError('stale DSH Work')
            if manifest(settings)!=bridge:raise ValueError('dsh_configuration_changed')
            if _completion is None and (not isinstance(goal,str) or not goal.strip() or len(goal)>12000):raise ValueError('invalid_delegation_goal')
            if not isinstance(background,str) or len(background)>16000:raise ValueError('invalid_delegation_background')
            constraints=[] if constraints is None else constraints
            if not isinstance(constraints,list) or len(constraints)>32 or any(not isinstance(x,str) or len(x)>2000 for x in constraints):raise ValueError('invalid_delegation_constraints')
            record=parent_record(settings,event,current,db,context)
            control=getattr(context.context,'hosted_run_control',None)
            if record['authority']=='autonomous' and control is None:raise ValueError('dsh_autonomous_budget_required')
            from group_actions import tool_action_id
            call_id=_completion['dsh']['delegationId'] if _completion is not None else tool_action_id.get()
            if not isinstance(call_id,str) or not call_id:raise ValueError('dsh_tool_call_identity_required')
            from pipeline_tools import validate_parent
            validate_parent(db,settings,record)
            from dsh_group_tools import GroupReadBridge
            import dsh_read_tools
            group=GroupReadBridge(settings,event,current,db,context,record)
            def catalog():
                rows=[]
                for definition in group.definitions():
                    definition={**definition,'output':{'schema':{}}}
                    rows.append({'id':definition['name'],'capability':'group_history','effect':'read','available':True,
                        'version':hashlib.sha256(json.dumps(definition,sort_keys=True).encode()).hexdigest(),'definition':definition})
                return rows+dsh_read_tools.catalog(settings,event,current,db,context,social)
            payload={'protocol':2,'parent':record,'delegationId':call_id,'input':{'goal':goal,'background':background,'constraints':constraints}}
            if _completion is not None:
                if record['authority']!='autonomous':raise ValueError('dsh_autonomous_completion_required')
                payload.pop('input')
                payload['completion']={'id':_completion['id'],'owner':_completion['owner']}
            final=None
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=150),trust_env=False) as client,budget_scope(control) as budget:
                async with client.post(bridge['url'],json=payload,headers={'Authorization':'Bearer '+bridge['token']},allow_redirects=False) as response:
                    if response.status!=200:raise ValueError('dsh_delegation_failed')
                    async for line in response.content:
                        if len(line)>131072:raise ValueError('dsh_result_too_large')
                        frame=json.loads(line)
                        settling=frame.get('type')=='tool' and frame.get('operation')=='model_budget' and frame.get('phase') in ('settled','unknown','released')
                        if not settling:
                            if parent_record(settings,event,current,db,context)!=record:raise ValueError('dsh_parent_changed')
                        if not settling and manifest(settings)!=bridge:raise ValueError('dsh_configuration_changed')
                        if frame.get('type')=='pending':
                            if final is not None:raise ValueError('dsh_final_contract_invalid')
                            final=None if _completion is not None else '委托仍在处理，完成后会返回结果。'
                            if _completion is not None:return None
                            continue
                        if frame.get('type')=='final':
                            if final is not None or not isinstance(frame.get('text'),str) or not frame['text'].strip():raise ValueError('dsh_final_contract_invalid')
                            final=frame['text'];continue
                        if frame.get('type')!='tool' or final is not None:raise ValueError('dsh_delegation_failed')
                        ack={'id':frame['id'],'ok':False}
                        try:
                            if frame['operation']=='model_budget' and budget is not None:value=await budget.transition(frame)
                            elif frame['operation']=='tool_budget' and control is not None:
                                from native_internal import WorkBudgetExceeded
                                control.check()
                                if control.used_tools>=control.tool_calls:raise WorkBudgetExceeded('tool_call_budget')
                                control.used_tools+=1;control.progress();value={}
                            elif frame['operation']=='discover':value=catalog()
                            elif frame['operation']=='execute':
                                item=next((row for row in catalog() if row['id']==frame.get('tool')),None)
                                if item is None:raise ValueError('dsh_tool_unavailable')
                                if item['capability']=='group_history':value=await group.execute(item['id'],frame.get('args',{}))
                                else:value=await dsh_read_tools.execute(settings,event,current,db,context,item['id'],frame.get('args',{}),social)
                            else:raise ValueError('dsh_callback_invalid')
                            ack.update(ok=True,value=value)
                        except asyncio.CancelledError:raise
                        except Exception as error:
                            from model_cache import record_diagnostic
                            record_diagnostic(settings,{'kind':'dsh_callback_error','workId':record.get('work_id'),'reason':type(error).__name__,'diagnosticDetail':frame.get('operation')})
                            ack['error']='工具请求未完成；结果以权威回执为准，未确认的外部操作不得重复执行。'
                        if not settling:validate_parent(db,settings,record)
                        async with client.post(bridge['url'].removesuffix('/delegate')+'/tool-result',json=ack,headers={'Authorization':'Bearer '+bridge['token']},allow_redirects=False) as answered:
                            if answered.status!=200:raise ValueError('dsh_callback_rejected')
            if not current():raise asyncio.CancelledError('stale DSH result')
            validate_parent(db,settings,record)
            if final is None:raise ValueError('dsh_final_missing')
            return final
    return [Delegate(name='dsh_delegate',description='将联网搜索、网页查阅、当前授权群的历史深度查证、真实执行结果核对、收藏与素材语义盘点、B站查询及获授权操作、多片段媒体分析完整委托给DSH。只提供目标、必要背景和约束，不指定工具或执行步骤；只接收最终文字，不查询进度。',parameters={
        'type':'object','additionalProperties':False,'required':['goal'],'properties':{
        'goal':{'type':'string','maxLength':12000},'background':{'type':'string','maxLength':16000},'constraints':{'type':'array','maxItems':32,'items':{'type':'string','maxLength':2000}}}})]


async def resume_autonomous(settings,event,current,db,context,completion):
    """Consume an already claimed media completion under this new owned cycle."""
    bridge=manifest(settings)
    if not bridge:raise ValueError('dsh_completion_bridge_unavailable')
    if not isinstance(completion.get('dsh'),dict) or not completion['dsh'].get('delegationId'):raise ValueError('dsh_completion_identity_invalid')
    return await tools(settings,event,current,db,bridge)[0].run(context,'',_completion=completion)
