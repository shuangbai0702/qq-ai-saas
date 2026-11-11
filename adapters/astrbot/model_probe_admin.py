"""Admin-only, one-call provider probe through the member's real chat hooks.

No QQ ingress, delivery or tool execution is synthesized. The platform service
entry is appended to the existing Journal; it never counts as a business round.
"""
import asyncio,json,re,time


def manage(store,data,care,run):
    status=data.get('action')=='admin_model_probe_status'
    allowed={'action','requestId'} if status else {'action','requestId','administrator','scope'}
    if set(data)!=allowed or not isinstance(data.get('requestId'),str) or not re.fullmatch(r'[a-fA-F0-9-]{36}',data['requestId']):
        return {'error':'调试请求无效','status':400}
    jobs=getattr(store,'_model_probe_jobs',None)
    if jobs is None:jobs={};store._model_probe_jobs=jobs
    key=data['requestId']
    if key in jobs:return dict(jobs[key])
    if status:return {'error':'调试请求不存在或运行实例已更换','status':404}
    if not isinstance(data.get('administrator'),str) or not data['administrator'].strip() or not isinstance(data.get('scope'),str) or not re.fullmatch(r'(private|group):[1-9][0-9]{3,19}',data['scope']):
        return {'error':'管理员身份或会话无效','status':400}
    scope=data['scope']
    if not store.db.execute('SELECT 1 FROM pipeline_events WHERE scope=? LIMIT 1',(scope,)).fetchone():
        return {'error':'只能调试机器人已接触的真实会话','status':400}
    if not store.shared_context or care.busy(store) or getattr(store,'_model_probe_task',None) and not store._model_probe_task.done():
        return {'error':'机器人当前不可调试，请稍后重试','status':409}
    if len(jobs)>=16:return {'error':'本次运行调试次数已达上限','status':429}
    job={'ok':True,'requestId':key,'state':'running','startedAt':time.time(),'qqSend':False,'toolExecution':False,'businessRound':False}
    jobs[key]=job
    async def execute():
        try:
            job['result']=await asyncio.wait_for(run(data),60)
            job['state']='completed'
        except asyncio.CancelledError:
            job['state']='cancelled';raise
        except Exception as error:
            job.update(state='failed',reason=type(error).__name__)
        finally:job['finishedAt']=time.time()
    task=asyncio.create_task(execute());store._model_probe_task=task;care.track(store,task)
    def settled(done):
        if done.cancelled():job.update(state='cancelled',finishedAt=time.time())
    task.add_done_callback(settled)
    return dict(job)


async def request(brain,id,store,lease,data):
    from astrbot.core.platform.astr_message_event import current_message_event
    from bot_pipeline import Work
    from autonomy import decision_token_limit
    from model_cache import usage_evidence
    from readonly_journal import ReadonlyJournal
    from shared_context import JOURNAL_SCOPE
    from system_contract import system_vector_sha
    if brain.settings.get(id) is not lease:raise asyncio.CancelledError('stale_debug_lease')
    prompt='[管理员调试；平台内部事件，不是QQ用户发言]只确认模型请求链路可用，简短回答即可。不执行工具，不发送QQ，不创建任务。'
    event=brain.make_event(id,{'id':'admin-probe:'+data['requestId']},data['scope'],prompt,str(lease.get('expectedUin') or ''))
    event.set_extra('_hosted_internal_work',None)
    event.set_extra('_hosted_initiative',False)
    event.set_extra('_hosted_admin_debug',True)
    event.set_extra('_hosted_pipeline',Work(id,data['scope'],'0',str(event.get_self_id()),'admin-probe:'+data['requestId'],'admin_debug',time.time(),lease))
    context=brain.prepare_context(id,event,data['scope'],prompt)
    context['identity']['principal']='administrator-debug-service'
    context['administrator_debug']={'requestId':data['requestId'],'sender':'platform_service','qqSend':False,'toolExecution':False,'businessRound':False}
    event.set_extra('_hosted_context',json.dumps(context,ensure_ascii=False,separators=(',',':')))
    token=current_message_event.set(event);limit=decision_token_limit.set(1024)
    store.active+=1
    try:
        provider=brain.provider(id)
        if provider is None:raise RuntimeError('model_unavailable')
        response=await provider.text_chat(prompt=prompt,system_prompt=lease['config'].get('systemPrompt',''),contexts=[],func_tool=None)
        if brain.settings.get(id) is not lease:raise asyncio.CancelledError('stale_debug_result')
        journal=store.model_journals.get(JOURNAL_SCOPE)
        if journal is None:journal=ReadonlyJournal(store.db,JOURNAL_SCOPE)
        wire=journal.last.wire if journal.last else []
        return {'text':(response.completion_text or '')[:4000],'usage':usage_evidence(response),'systemSha':system_vector_sha(wire),
                'quoteRuleInSystem':any('[引用习惯]' in str(m.get('content')) for m in wire if m.get('role')=='system'),
                'journalCommitted':journal.last is not None,'qqSend':False,'toolExecution':False,'businessRound':False}
    finally:
        store.active-=1;store.slot_changed.set()
        current_message_event.reset(token);decision_token_limit.reset(limit)
