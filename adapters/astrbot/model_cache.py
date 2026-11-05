"""Stable routing hints for providers verified to accept prompt_cache_key.

This is a routing hint, never a context cache. No message/permission is frozen.
Unknown compatible providers keep their original request contract.
"""
import hashlib,json,os,time
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit

VERIFIED_HOSTS=frozenset(('api.openai.com','aiapi.codes'))
_decision=ContextVar('fishbot_decision_cache_scope',default=None)


def record_diagnostic(lease, record):
    """Bounded content-free evidence; shared framework logs remain disabled."""
    if not isinstance(lease,dict) or not lease.get('root') or not lease.get('botId') or not lease.get('tenantId'):
        return False
    allowed=('kind','cycleId','workId','phase','systemSha','resetReason','historyTurn','historyMessages',
             'tailChars','requestIndex','responseId','inputTokens','cachedTokens','outputTokens','cacheUsageReported')
    row={key:value for key,value in record.items() if key in allowed and (value is None or type(value) in (str,int,float,bool))}
    if any(isinstance(value,str) and len(value)>256 for value in row.values()):return False
    row.update(at=time.time(),pid=os.getpid(),botId=lease['botId'],generation=lease.get('generation'),revision=lease.get('revision'))
    try:
        path=Path(lease['root'])/'model-diagnostics.jsonl'
        if path.exists() and path.stat().st_size>=256*1024:
            path.replace(path.with_name('model-diagnostics.previous.jsonl'))
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as file:file.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
        return True
    except (OSError,TypeError,ValueError):
        return False  # Observability never changes a model request's result.


def usage_evidence(response):
    """Missing upstream cache usage is unknown, not the SDK's default zero."""
    def read(value,key):return value.get(key) if isinstance(value,dict) else getattr(value,key,None)
    raw=getattr(response,'raw_completion',None);usage=read(raw,'usage');details=read(usage,'prompt_tokens_details')
    number=lambda value:value if type(value) is int and value>=0 else None
    return {'responseId':read(raw,'id'),'inputTokens':number(read(usage,'prompt_tokens')),
            'cachedTokens':number(read(details,'cached_tokens')),'outputTokens':number(read(usage,'completion_tokens')),
            'cacheUsageReported':number(read(details,'cached_tokens')) is not None}


@contextmanager
def decision_scope(provider,lease,phase):
    """Routing metadata only, bound to this provider and awaited decision.

    The existing Work/lease checks still own authorization and cancellation.
    No credential, messages or stale snapshot is cached here.
    """
    value=None
    if phase in ('orientation','planning','group_freshness') and isinstance(lease,dict) and lease.get('tenantId') and lease.get('botId'):
        value=(provider,lease['tenantId'],lease['botId'],'autonomy:'+phase)
    token=_decision.set(value)
    try:yield
    finally:_decision.reset(token)

def routing_key(event,provider,model):
    if not isinstance(model,str) or not model.lower().startswith('gpt-'):return None
    config=getattr(provider,'provider_config',{})
    if not isinstance(config,dict):return None
    try:host=urlsplit(config.get('api_base','')).hostname
    except (TypeError,ValueError):return None
    if host not in VERIFIED_HOSTS:return None
    if event is None:
        decision=_decision.get()
        if not decision or decision[0] is not provider:return None
        namespace=json.dumps(list(decision[1:]),ensure_ascii=False,separators=(',',':'))
        return 'fb-v1-'+hashlib.sha256(namespace.encode()).hexdigest()[:40]
    lease=event.get_extra('_hosted_lease')
    if not isinstance(lease,dict) or not lease.get('tenantId') or not lease.get('botId'):return None
    work=event.get_extra('_hosted_pipeline');scope=getattr(work,'scope',None)
    if not scope and hasattr(event,'get_group_id') and hasattr(event,'get_sender_id'):
        group=event.get_group_id();scope='group:'+str(group) if group else 'private:'+str(event.get_sender_id())
    if not isinstance(scope,str) or not scope:return None
    namespace=json.dumps([lease['tenantId'],lease['botId'],scope],ensure_ascii=False,separators=(',',':'))
    return 'fb-v1-'+hashlib.sha256(namespace.encode()).hexdigest()[:40]
