"""Trusted local host bridge; identities remain bound to the original Work."""
import asyncio,hashlib,json
from pathlib import Path
from urllib.parse import urlsplit
import aiohttp
from contextlib import asynccontextmanager
from dsh_bridge import manifest,bridge_current,parent_record


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


def tools(settings,event,current,db,bridge,social=None,*,effect_current=None):
    from astrbot.core.agent.tool import FunctionTool
    vision_enabled='image_analysis' in bridge.get('capabilities',[])
    web_enabled=(settings.get('config',{}).get('delegatedCapabilities') or {}).get('webSearch',True)
    class Delegate(FunctionTool):
        hosted_effect=False
        @property
        def hosted_final_text(self):return True
        async def call(self,context,goal,background='',constraints=None,image=None,continue_task=None):
            try:return await self.run(context,goal,background,constraints,image=image,continue_task=continue_task)
            except asyncio.CancelledError:raise
            except Exception as error:
                from native_internal import WorkStopped,WorkBudgetExceeded
                if isinstance(error,(WorkStopped,WorkBudgetExceeded)):raise
                from model_cache import record_diagnostic
                from qq_diagnostics import failure_notice
                record_diagnostic(settings,{'kind':'dsh_delegation_error','workId':getattr(event.get_extra('_hosted_pipeline'),'event_id',''),'reason':type(error).__name__})
                return failure_notice(type(error).__name__,stage='delegation')
        async def run(self,context,goal,background='',constraints=None,_completion=None,*,image=None,continue_task=None):
            if context.context.event is not event or not current():raise asyncio.CancelledError('stale DSH Work')
            if not bridge_current(settings,bridge):raise ValueError('dsh_configuration_changed')
            if _completion is None and (not isinstance(goal,str) or not goal.strip() or len(goal)>12000):raise ValueError('invalid_delegation_goal')
            if not isinstance(background,str) or len(background)>16000:raise ValueError('invalid_delegation_background')
            constraints=[] if constraints is None else constraints
            if not isinstance(constraints,list) or len(constraints)>32 or any(not isinstance(x,str) or len(x)>2000 for x in constraints):raise ValueError('invalid_delegation_constraints')
            if continue_task is not None and (not isinstance(continue_task,str) or not continue_task or len(continue_task)>256 or image is not None or _completion is not None):raise ValueError('invalid_dsh_task_continuation')
            record=parent_record(settings,event,current,db,context)
            control=getattr(context.context,'hosted_run_control',None)
            if record['authority']=='autonomous' and control is None:raise ValueError('dsh_autonomous_budget_required')
            from group_actions import tool_action_id
            call_id=_completion['dsh']['delegationId'] if _completion is not None else tool_action_id.get()
            if not isinstance(call_id,str) or not call_id:raise ValueError('dsh_tool_call_identity_required')
            def assert_current():
                if parent_record(settings,event,current,db,context)!=record:raise ValueError('dsh_parent_changed')
                if not bridge_current(settings,bridge):raise ValueError('dsh_configuration_changed')
            if image is not None:
                if not vision_enabled or record['authority']!='foreground' or _completion is not None:
                    raise ValueError('dsh_image_not_authorized')
                if not isinstance(image,dict) or not set(image)<= {'event_id','positions'}:
                    raise ValueError('invalid_dsh_image_selection')
                event_id=image.get('event_id',record['origin_event'])
                positions=image.get('positions',[])
                if not isinstance(event_id,str) or not 0<len(event_id)<=512 or not isinstance(positions,list) or len(positions)>32 or any(type(i) is not int or not 1<=i<=32 for i in positions) or len(set(positions))!=len(positions):
                    raise ValueError('invalid_dsh_image_selection')
                from semantic_media import delegation_originals
                scope=record['origin_scope']
                journal_scope=event.get_extra('_hosted_journal_scope') or scope
                if journal_scope not in (scope,'bot_shared'):raise ValueError('invalid_dsh_image_scope')
                images=await delegation_originals(db,journal_scope,scope,event_id,positions)
                assert_current()
                if images is None:return '没有可用于这次分析的原图。原图可能已清理、不可读取，或超出可分析的格式或大小。'
                positions=positions or list(range(1,len(images)+1))
            from dsh_group_tools import GroupReadBridge
            import dsh_read_tools,dsh_qq_tools
            group=GroupReadBridge(settings,event,current,db,context,record)
            def catalog():
                rows=[]
                for definition in group.definitions():
                    definition={**definition,'output':{'schema':{}}}
                    rows.append({'id':definition['name'],'capability':'group_history','effect':'read','available':True,
                        'version':hashlib.sha256(json.dumps(definition,sort_keys=True).encode()).hexdigest(),'definition':definition})
                return rows+dsh_read_tools.catalog(settings,event,current,db,context,social)+dsh_qq_tools.catalog(settings,event,current,db,context,effect_current=effect_current)
            payload={'protocol':2,'parent':record,'delegationId':call_id,'input':{'goal':goal,'background':background,'constraints':constraints}}
            if continue_task is not None:payload['continueDelegationId']=continue_task
            if image is not None:payload['imageAnalysis']=True
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
                        if not settling:assert_current()
                        if image is not None:
                            expected='tool' if images is not None else 'final'
                            if frame.get('type')!=expected or expected=='tool' and frame.get('operation')!='vision_images':
                                raise ValueError('dsh_vision_frame_invalid')
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
                            elif frame['operation']=='vision_images' and image is not None:
                                value={'eventId':event_id,'positions':positions,'images':images}
                            elif frame['operation']=='history_content':
                                value=await dsh_qq_tools.materialize(settings,event,current,db,context,frame.get('reference'))
                            elif frame['operation']=='execute':
                                item=next((row for row in catalog() if row['id']==frame.get('tool')),None)
                                if item is None:raise ValueError('dsh_tool_unavailable')
                                if item['capability']=='group_history':value=await group.execute(item['id'],frame.get('args',{}))
                                elif item['capability'] in ('chat_history','qq_space'):value=await dsh_qq_tools.execute(settings,event,current,db,context,item['id'],frame.get('args',{}),effect_current=effect_current)
                                else:value=await dsh_read_tools.execute(settings,event,current,db,context,item['id'],frame.get('args',{}),social)
                            else:raise ValueError('dsh_callback_invalid')
                            ack.update(ok=True,value=value)
                        except asyncio.CancelledError:raise
                        except Exception as error:
                            from model_cache import record_diagnostic
                            record_diagnostic(settings,{'kind':'dsh_callback_error','workId':record.get('work_id'),'reason':type(error).__name__,'diagnosticDetail':frame.get('operation')})
                            ack['error']='工具请求未完成；结果以权威回执为准，未确认的外部操作不得重复执行。'
                        if not settling:assert_current()
                        headers={'Authorization':'Bearer '+bridge['token']}
                        if frame['operation']=='vision_images':headers['X-FishBot-Vision-Callback']=frame['id']
                        async with client.post(bridge['url'].removesuffix('/delegate')+'/tool-result',json=ack,headers=headers,allow_redirects=False) as answered:
                            if answered.status!=200:raise ValueError('dsh_callback_rejected')
                        if frame['operation']=='vision_images':
                            images=value=None
                            ack.clear()
            assert_current()
            if final is None:raise ValueError('dsh_final_missing')
            return final
    description='将'+('联网搜索、网页查阅、' if web_enabled else '')+'授权私聊及群聊历史查证、查图选图、QQ空间查看及获授权发布、真实执行结果核对、收藏与素材语义盘点、B站及多片段媒体任务完整委托给DSH。只交代目标、必要背景和约束，DSH自行查历史、看图和操作；只接收最终文字，不传历史全文或查询进度。'
    description+='同一Work的任务追问可用continue_task引用此前dsh_delegate调用ID，复用原对话；省略则创建独立任务。'
    properties={
        'continue_task':{'type':'string','minLength':1,'maxLength':256},
        'goal':{'type':'string','maxLength':12000},'background':{'type':'string','maxLength':16000},'constraints':{'type':'array','maxItems':32,'items':{'type':'string','maxLength':2000}}}
    if vision_enabled and not event.get_extra('_hosted_internal_work'):
        description+='用户明确要求识图、读取图中文字或查看细节，或主线无法读取当前图片时，使用 image 委托原图分析后再答复；普通图片闲聊由主线直接理解。image={}分析当前消息；图片在前一条消息时填写其真实event_id，仅使用当前会话仍有效的图片。委托也失败时如实说明限制，不把接口不支持或未读取图片说成原图模糊，不凭旧记忆断定本次图片不可读。'
        properties['image']={'type':'object','additionalProperties':False,'properties':{
            'event_id':{'type':'string','maxLength':512,'description':'可信 QQ 消息的事件 ID，省略表示当前消息。'},
            'positions':{'type':'array','maxItems':32,'items':{'type':'integer','minimum':1,'maximum':32},'description':'消息图片从 1 开始编号，省略表示分析此消息可用的全部图片。'}}}
    return [Delegate(name='dsh_delegate',description=description,parameters={
        'type':'object','additionalProperties':False,'required':['goal'],'properties':properties})]


async def resume_autonomous(settings,event,current,db,context,completion):
    """Consume an already claimed media completion under this new owned cycle."""
    bridge=manifest(settings,allow_newer=True)
    if not bridge:raise ValueError('dsh_completion_bridge_unavailable')
    if not isinstance(completion.get('dsh'),dict) or not completion['dsh'].get('delegationId'):raise ValueError('dsh_completion_identity_invalid')
    return await tools(settings,event,current,db,bridge)[0].run(context,'',_completion=completion)
