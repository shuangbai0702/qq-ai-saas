"""Authenticated control-plane maintenance on the running member's owner.

The public tenant mutation schema excludes these internal commands. The API
derives administrator from the authenticated session, never a browser field.
"""
import re
import time

ADMIN_COMPRESSION_OUTPUT_TOKENS=16384


def manage(store,data,care):
    status_only=data.get('action')=='admin_force_compress_status'
    allowed={'action','requestId'} if status_only else {'action','requestId','administrator','allowPartial','discardUnavailable'}
    request_id=data.get('requestId')
    if set(data)-allowed or not (allowed-{'discardUnavailable'})<=set(data) or not isinstance(request_id,str) or not re.fullmatch(r'[a-fA-F0-9-]{36}',request_id):
        return {'error':'维护请求无效','status':400}
    jobs=getattr(store,'_compression_jobs',None)
    if jobs is None:jobs={};store._compression_jobs=jobs
    if request_id in jobs:return dict(jobs[request_id])
    if status_only:return {'error':'维护请求不存在或运行实例已更换','status':404}
    administrator=data.get('administrator')
    if not isinstance(administrator,str) or not administrator.strip() or type(data.get('allowPartial')) is not bool or type(data.get('discardUnavailable',False)) is not bool:
        return {'error':'管理员维护凭据无效','status':400}
    if not store.shared_context:return {'error':'当前账号不属于统一主Journal','status':409}
    # A diagnostic cache must never veto an authorized compression. Committed
    # request IDs remain idempotent in the existing checkpoint override table.
    finished=[key for key,value in jobs.items() if value.get('state') not in ('running','queued')]
    for key in finished[:-31]:jobs.pop(key,None)
    reflection=store.reflection
    from autonomy import decision_token_limit
    limit_token=decision_token_limit.set(ADMIN_COMPRESSION_OUTPUT_TOKENS)
    failure=None
    try:
        # create_task inside force_compress_once inherits this request budget;
        # the API caller and unrelated foreground tasks keep their own context.
        result=reflection.force_compress_once('bot_shared',allow_partial=data['allowPartial'],administrator=administrator,request_id=request_id,discard_unavailable=data.get('discardUnavailable',False))
    except Exception as error:
        failure=type(error).__name__
        result={'started':False,'reason':failure}
    finally:
        decision_token_limit.reset(limit_token)
    job={'ok':True,'requestId':request_id,'state':'running' if result.get('started') else 'not_started','startedAt':time.time(),**result}
    jobs[request_id]=job
    if failure:
        job.update(state='failed',finishedAt=time.time())
        return dict(job)
    if not result.get('started'):return dict(job)
    task=reflection.task
    care.track(store,task)
    def completed(done):
        job['finishedAt']=time.time()
        if done.cancelled():job['state']='cancelled';return
        error=done.exception()
        if error:job['state']='failed';job['reason']=type(error).__name__;return
        value=done.result()
        job['state']='completed' if isinstance(value,dict) and value.get('ok') is True else 'failed'
        if not isinstance(value,dict):job['reason']='completion_unconfirmed'
        if isinstance(value,dict):job['result']=value
    task.add_done_callback(completed)
    return dict(job)
