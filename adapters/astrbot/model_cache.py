"""Stable routing hints for providers verified to accept prompt_cache_key.

This is a routing hint, never a context cache. No message/permission is frozen.
Unknown compatible providers keep their original request contract.
"""
import hashlib,json,os,time
import uuid
from collections import OrderedDict
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit

VERIFIED_HOSTS=frozenset(('api.openai.com','aiapi.codes'))
_decision=ContextVar('fishbot_decision_cache_scope',default=None)
_attempt=ContextVar('fishbot_model_attempt',default=None)
_shapes=OrderedDict()
_wire_shapes=OrderedDict()


def loaded_build():
    """One startup attestation of loaded functions, not merely files on disk."""
    import marshal,sys,types
    rows={}
    for name,function in (('context_transport','request_context_view'),('model_cache','begin_attempt'),
                          ('qq_actions','execute'),('tool_outcome','project'),('bot_pipeline','operation_source'),
                          ('decision_history','AppendHistory.prepare'),('readonly_journal','ReadonlyJournal.prepare')):
        module=sys.modules.get(name);target=module
        for part in function.split('.'):target=getattr(target,part,None)
        code=getattr(target,'__code__',None)
        if code is None:continue
        try:
            source=Path(module.__file__).read_bytes()
            compiled=compile(source,str(module.__file__),'exec',dont_inherit=True)
            expected=compiled
            for part in function.split('.'):
                expected=next(value for value in expected.co_consts if isinstance(value,types.CodeType) and value.co_name==part)
            def fingerprint(value):
                value=value.replace(co_filename='',co_consts=tuple(fingerprint_code(item) for item in value.co_consts))
                return hashlib.sha256(marshal.dumps(value)).hexdigest()
            def fingerprint_code(value):
                if isinstance(value,types.CodeType):
                    return value.replace(co_filename='',co_consts=tuple(fingerprint_code(item) for item in value.co_consts))
                return value
            rows[name]={'sourceSha':hashlib.sha256(source).hexdigest(),'loadedCodeSha':fingerprint(code),
                        'sourceMatchesLoaded':fingerprint(code)==fingerprint(expected)}
        except (OSError,ValueError,StopIteration,TypeError):
            rows[name]={'sourceMatchesLoaded':False}
    return rows


def record_diagnostic(lease, record):
    """Bounded content-free evidence; shared framework logs remain disabled."""
    if not isinstance(lease,dict) or not lease.get('root') or not lease.get('botId') or not lease.get('tenantId'):
        return False
    allowed=('kind','cycleId','workId','phase','systemSha','resetReason','historyTurn','historyMessages',
             'tailChars','requestIndex','responseId','inputTokens','cachedTokens','outputTokens','cacheUsageReported',
             'attemptId','status','reason','durationMs','firstResponseMs','toolsSha','routingSha','messagesSha',
             'messageCount','prefixMessages','firstDifference','inputChars','prefixChars','unsharedChars','costStatus','journalReason',
             'parametersSha','wireIndex','resetDetail','historyChars','baseChars','wireChars','textChars','plainTextChars',
             'mediaChars','imageCount','toolsChars','toolCount','wireBytes','diagnosticDetail')
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

def message_shape(messages,previous=()):
    """Bounded hashes only; never retain message bodies or credentials."""
    shape=[]
    for message in (messages or [])[:1024]:
        wire=json.dumps(message,ensure_ascii=False,separators=(',',':'),default=str)
        shape.append((hashlib.sha256(wire.encode()).hexdigest(),len(wire)))
    prefix=0
    for a,b in zip(shape,previous):
        if a!=b:break
        prefix+=1
    total=sum(s[1] for s in shape);shared=sum(s[1] for s in shape[:prefix])
    from decision_history import history_usage
    return shape,{**history_usage(messages or []),'messageCount':len(messages or []),'prefixMessages':prefix,'firstDifference':prefix,
                  'inputChars':total,'prefixChars':shared,'unsharedChars':total-shared,
                  'messagesSha':hashlib.sha256(json.dumps(shape).encode()).hexdigest()[:24]}

def begin_attempt(event,provider):
    decision=_decision.get();lease=event.get_extra('_hosted_lease') if event else decision[4] if decision and decision[0] is provider else None
    work=event.get_extra('_hosted_pipeline') if event else None
    phase=('inner_execution' if event.get_extra('_hosted_internal_work') else 'foreground') if event else (decision[6] if len(decision)>6 and decision[6] else decision[3].split(':',1)[-1]) if decision else 'data_task'
    value={'lease':lease,'provider':provider,'started':time.monotonic(),'phase':phase,'attemptId':str(uuid.uuid4()),'workId':getattr(work,'event_id',None) or (decision[5] if decision and len(decision)>5 else None),'scope':getattr(work,'scope',phase),'ended':False}
    return _attempt.set(value)

def observe_payload(payload,journal_reason=None):
    try:
        value=_attempt.get()
        if not value:return
        lease=value.get('lease')
        if not isinstance(lease,dict):return
        key=(lease.get('tenantId'),lease.get('botId'),value['scope'],value['phase'])
        previous=_shapes.pop(key,())
        shape,fields=message_shape(payload.get('messages'),previous)
        _shapes[key]=shape
        while len(_shapes)>32:_shapes.popitem(last=False)
        value['fields']=fields
        value['journalReason']=journal_reason
        fields.update(toolsSha=hashlib.sha256(json.dumps(payload.get('tools'),ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:24],
                      routingSha=hashlib.sha256(str(payload.get('prompt_cache_key','')).encode()).hexdigest()[:24],journalReason=journal_reason)
        record_diagnostic(lease,{'kind':'request','attemptId':value['attemptId'],'phase':value['phase'],'workId':value['workId'],**fields})
    except Exception:
        return  # Diagnostics must never change request execution.


def observe_wire_request(request):
    """Hash the actual HTTP body after SDK tools and provider overrides.

    Independent bounded shapes avoid comparing final wire to the intermediate
    prepare view. No headers, paths, parameter values or message bodies persist.
    """
    try:
        value=_attempt.get()
        if not value:return
        lease=value.get('lease')
        if not isinstance(lease,dict):return
        raw=request.content
        value['wireIndex']=value.get('wireIndex',0)+1
        if len(raw)>4*1024*1024:
            record_diagnostic(lease,{'kind':'wire_request','attemptId':value['attemptId'],'phase':value['phase'],
                'workId':value['workId'],'wireIndex':value['wireIndex'],'wireBytes':len(raw),'diagnosticDetail':'wire_body_limit'})
            return
        payload=json.loads(raw)
        if not isinstance(payload,dict) or not isinstance(payload.get('messages'),list):return
        key=(lease.get('tenantId'),lease.get('botId'),value['scope'],value['phase'])
        shape,fields=message_shape(payload['messages'],_wire_shapes.pop(key,()))
        _wire_shapes[key]=shape
        while len(_wire_shapes)>32:_wire_shapes.popitem(last=False)
        sha=lambda v:hashlib.sha256(json.dumps(v,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:24]
        fields.update(toolsSha=sha(payload.get('tools')),routingSha=hashlib.sha256(str(payload.get('prompt_cache_key','')).encode()).hexdigest()[:24],
                      parametersSha=sha({k:v for k,v in payload.items() if k not in ('messages','tools','prompt_cache_key')}),
                      journalReason=value.get('journalReason'),wireIndex=value['wireIndex'],wireBytes=len(raw),
                      toolCount=len(payload.get('tools') or []),toolsChars=len(json.dumps(payload.get('tools'),ensure_ascii=False,separators=(',',':'))))
        record_diagnostic(lease,{'kind':'wire_request','attemptId':value['attemptId'],'phase':value['phase'],'workId':value['workId'],**fields})
    except Exception:
        return


def finish_attempt(status,response=None,reason=None):
    value=_attempt.get()
    if not value or value['ended']:return
    value['ended']=True
    record_diagnostic(value.get('lease'),{'kind':'request_result','attemptId':value['attemptId'],'phase':value['phase'],'workId':value['workId'],
        'status':status,'reason':reason,'durationMs':round((time.monotonic()-value['started'])*1000),'firstResponseMs':value.get('firstResponseMs'),'costStatus':'unknown',
        **(usage_evidence(response) if response is not None else {})})

def first_response():
    value=_attempt.get()
    if value and 'firstResponseMs' not in value:value['firstResponseMs']=round((time.monotonic()-value['started'])*1000)

def reset_attempt(token):_attempt.reset(token)


@contextmanager
def decision_scope(provider,lease,phase,work_id=None,request_phase=None):
    """Routing metadata only, bound to this provider and awaited decision.

    The existing Work/lease checks still own authorization and cancellation.
    No credential, messages or stale snapshot is cached here.
    """
    value=None
    if phase in ('orientation','planning','deliberation','group_freshness') and isinstance(lease,dict) and lease.get('tenantId') and lease.get('botId'):
        value=(provider,lease['tenantId'],lease['botId'],'autonomy:'+phase,lease,work_id,request_phase)
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
        namespace=json.dumps(list(decision[1:4]),ensure_ascii=False,separators=(',',':'))
        return 'fb-v1-'+hashlib.sha256(namespace.encode()).hexdigest()[:40]
    lease=event.get_extra('_hosted_lease')
    if not isinstance(lease,dict) or not lease.get('tenantId') or not lease.get('botId'):return None
    work=event.get_extra('_hosted_pipeline');scope=getattr(work,'scope',None)
    if not scope and hasattr(event,'get_group_id') and hasattr(event,'get_sender_id'):
        group=event.get_group_id();scope='group:'+str(group) if group else 'private:'+str(event.get_sender_id())
    if not isinstance(scope,str) or not scope:return None
    namespace=json.dumps([lease['tenantId'],lease['botId'],scope],ensure_ascii=False,separators=(',',':'))
    return 'fb-v1-'+hashlib.sha256(namespace.encode()).hexdigest()[:40]
