"""Stable routing hints for providers verified to accept prompt_cache_key.

This is a routing hint, never a context cache. No message/permission is frozen.
Unknown compatible providers keep their original request contract.
"""
import hashlib,json,os,time,re
import uuid
from threading import Lock
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
_wire_signatures=OrderedDict()
_system_contracts={}
_auxiliary=ContextVar('fishbot_auxiliary_diagnostic',default=None)
_diagnostic_lock=Lock()

class AppendOnlyWireViolation(RuntimeError):pass


def loaded_build():
    """One startup attestation of loaded functions, not merely files on disk."""
    import marshal,sys,types
    rows={}
    for name,function in (('context_transport','request_context_view'),('model_cache','begin_attempt'),
                          ('qq_actions','execute'),('tool_outcome','project'),('bot_pipeline','operation_source'),
                          ('decision_history','AppendHistory.prepare'),('behavior','install_runtime'),
                            ('autonomous_runtime','AutonomousRuntime.snapshot'),('readonly_journal','ReadonlyJournal.prepare'),
                            ('output_clean','without_analysis'),('behavior','sanitize_new_response'),
                            ('tool_outcome','structured'),('group_freshness','response_coverage'),
                            ('memory_scope','pending_messages'),('memory_scope','retained_context_visible'),
                            ('agreement_state','context_view'),('bot_pipeline','conversation_context'),
                            ('memory_reflection','Reflection.recent'),('shared_context','compose'),
                            ('shared_context','Writer.acquire'),('readonly_journal','ReadonlyJournal.append_completion')):
        module=sys.modules.get(name);target=module
        for part in function.split('.'):target=getattr(target,part,None)
        code=getattr(target,'__code__',None)
        if code is None:continue
        key=name if name not in rows else name+'.'+function
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
            rows[key]={'sourceSha':hashlib.sha256(source).hexdigest(),'loadedCodeSha':fingerprint(code),
                        'sourceMatchesLoaded':fingerprint(code)==fingerprint(expected)}
        except (OSError,ValueError,StopIteration,TypeError):
            rows[key]={'sourceMatchesLoaded':False}
    return rows


def record_diagnostic(lease, record):
    # Threads in one worker must not rename a file while a sibling writes it.
    # Independent processes still append atomically and use unique archives.
    with _diagnostic_lock:return _record_diagnostic(lease,record)


def _record_diagnostic(lease, record):
    """Bounded content-free evidence; shared framework logs remain disabled."""
    if not isinstance(lease,dict) or not lease.get('root') or not lease.get('botId') or not lease.get('tenantId'):
        return False
    allowed=('kind','cycleId','workId','phase','systemSha','resetReason','historyTurn','historyMessages',
             'tailChars','requestIndex','responseId','inputTokens','cachedTokens','outputTokens','cacheUsageReported',
             'attemptId','status','reason','durationMs','firstResponseMs','toolsSha','routingSha','messagesSha',
             'messageCount','prefixMessages','firstDifference','inputChars','prefixChars','unsharedChars','costStatus','journalReason',
             'parametersSha','wireIndex','resetDetail','historyChars','baseChars','wireChars','textChars','plainTextChars',
             'mediaChars','imageCount','toolsChars','toolCount','wireBytes','diagnosticDetail','systemContractStatus',
             'contentChars','reasoningChars','reasoningReported','finishReason','responseIdShape','appendOnlyStatus',
             'model','httpStatus','outputLimit','adoption','wireCount','scopeHash','eventKind','senderKind','scopeKind',
             # Bounded journal-candidate evidence written by the foreground
             # boundary; without these entries the fields were silently dropped
             # and "observed" coverage was overstated (E package audit).
             'layoutVersion','journalScopeKind','committedPrefixMessages','committedPrefixSha','permitKind','candidateAppendChars')
    row={key:value for key,value in record.items() if key in allowed and (value is None or type(value) in (str,int,float,bool))}
    # A long diagnostic must not erase the request's identity and outcome.
    # These fields contain only program metadata; never serialize exceptions.
    for key,value in list(row.items()):
        if isinstance(value,str) and len(value)>(1024 if key=='diagnosticDetail' else 256):
            row[key]='diagnostic_field_oversize'
    row.update(at=time.time(),pid=os.getpid(),botId=lease['botId'],generation=lease.get('generation'),revision=lease.get('revision'))
    try:
        path=Path(lease['root'])/'model-diagnostics.jsonl'
        limit=max(1024,min(16*1024*1024,int(os.environ.get('FISHBOT_DIAGNOSTIC_ROTATE_BYTES',1024*1024))))
        try:size=path.stat().st_size
        except FileNotFoundError:size=0 # Another writer may just have rotated.
        if size>=limit:
            # All writers share one append stream, but archives have unique
            # names: concurrent rotations cannot overwrite retained evidence.
            archive=path.with_name(f'model-diagnostics.previous.{int(time.time()*1000):013d}.{os.getpid()}.{uuid.uuid4().hex}.jsonl')
            try:path.rename(archive)
            except OSError:pass
            for old in sorted(path.parent.glob('model-diagnostics.previous.*.jsonl'),reverse=True)[3:]:
                try:old.unlink()
                except OSError:pass
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as file:file.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
        return True
    except (OSError,TypeError,ValueError):
        return False  # Observability never changes a model request's result.


def response_shape(raw):
    """Where the visible reply came from: content, a separate reasoning field, or neither.

    A leaked analysis is only attributable when the two channels are measured
    apart; lengths and the finish reason are enough, so no text is retained.
    """
    def read(value,key):return value.get(key) if isinstance(value,dict) else getattr(value,key,None)
    try:
        choices=read(raw,'choices')
        choice=choices[0] if isinstance(choices,(list,tuple)) and choices else None
        message=read(choice,'message')
    except (IndexError,TypeError,AttributeError):
        return {}
    content=read(message,'content');reasoning=read(message,'reasoning_content')
    if reasoning is None:reasoning=read(message,'reasoning')
    finish=read(choice,'finish_reason')
    # An explicit empty reasoning field is still a provider statement; absent is not.
    reported=isinstance(reasoning,str)
    return {'contentChars':len(content) if isinstance(content,str) else None,
            'reasoningChars':len(reasoning) if reported else None,'reasoningReported':reported,
            'finishReason':finish if isinstance(finish,str) and len(finish)<=32 else None}

def usage_evidence(response):
    """Missing upstream cache usage is unknown, not the SDK's default zero."""
    def read(value,key):return value.get(key) if isinstance(value,dict) else getattr(value,key,None)
    raw=getattr(response,'raw_completion',None);usage=read(raw,'usage');details=read(usage,'prompt_tokens_details')
    number=lambda value:value if type(value) is int and value>=0 else None
    identifier=read(raw,'id')
    cached=number(read(details,'cached_tokens'))
    if cached is None:cached=number(read(usage,'prompt_cache_hit_tokens'))
    return {'responseId':identifier,'inputTokens':number(read(usage,'prompt_tokens')),
            'cachedTokens':cached,'outputTokens':number(read(usage,'completion_tokens')),
            'cacheUsageReported':cached is not None,
            # A response-id prefix is a shape, never a vendor or route claim.
            # It is recorded so a correlation with cache reuse stays auditable.
            'responseIdShape':'gen-prefixed' if isinstance(identifier,str) and identifier.startswith('gen-') else 'other',
            **response_shape(raw)}

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

def current_lease(event,provider):
    lease=event.get_extra('_hosted_lease') if event else None
    if isinstance(lease,dict):return lease
    decision=_decision.get()
    if decision and decision[0] is provider and isinstance(decision[4],dict):return decision[4]
    binding=_system_contracts.get(id(provider))
    return binding[2] if binding and binding[0] is provider and isinstance(binding[2],dict) else None


@contextmanager
def auxiliary_scope(provider,phase,work_id=None,scope=None):
    """Diagnostic ownership only; never an authorization or a new model call.

    The provider wrapper binds its attempt to this scope. Its technical result
    and the caller's adoption are separate records in the existing journal.
    A completed request is not automatically a successfully applied result.
    """
    value={'provider':provider,'phase':phase,'workId':work_id,'attempts':[],
           'adoption':'not_adopted','reason':'caller_did_not_adopt','scopeHash':hashlib.sha256(scope.encode()).hexdigest()[:16] if isinstance(scope,str) else None}
    token=_auxiliary.set(value)
    try:yield value
    except BaseException as error:
        value.update(adoption='not_adopted',reason=type(error).__name__)
        raise
    finally:
        for attempt in value['attempts']:
            record_diagnostic(attempt.get('lease'),{'kind':'request_disposition',
                'attemptId':attempt['attemptId'],'phase':attempt['phase'],'workId':attempt['workId'],
                'adoption':value['adoption'],'reason':value['reason'],'scopeHash':value['scopeHash']})
        _auxiliary.reset(token)


def adopted(reason='applied'):
    value=_auxiliary.get()
    if value:value.update(adoption='adopted',reason=reason)


def discarded(reason):
    value=_auxiliary.get()
    if value:value.update(adoption='not_adopted',reason=reason)

def is_foreground_event(event):
    if not event or event.get_extra('_hosted_internal_work'):return False
    work=event.get_extra('_hosted_pipeline')
    # These all enter the same native conversation. Timer/owner/tool wakeups
    # must not bypass its durable wire/System contract just because no user
    # message triggered them. Effective business rounds are settled separately.
    return getattr(work,'source',None) in ('message','deferred_group','owner_contact','scheduled_contact','tool_continuation')


def bind_system_contract(provider,contract,lease):
    if provider is not None and contract is not None and isinstance(lease,dict):_system_contracts[id(provider)]=(provider,contract,lease)


def unbind_system_contract(provider):
    if provider is not None:
        binding=_system_contracts.get(id(provider))
        if binding and binding[0] is provider:_system_contracts.pop(id(provider),None)


def begin_attempt(event,provider,system_contract=None,system_permit_id=None):
    decision=_decision.get();lease=current_lease(event,provider)
    binding=_system_contracts.get(id(provider))
    foreground=is_foreground_event(event)
    if system_contract is None and foreground and binding and binding[0] is provider:
        system_contract=binding[1]
        if system_permit_id is None and isinstance(binding[2],dict):system_permit_id=binding[2].get('systemChangePermitId')
    work=event.get_extra('_hosted_pipeline') if event else None
    attempt_id=str(uuid.uuid4())
    if foreground and system_contract is not None:
        from system_contract import request_identity
        attempt_id=request_identity()
    auxiliary=_auxiliary.get()
    if auxiliary and auxiliary['provider'] is not provider:auxiliary=None
    phase=auxiliary['phase'] if auxiliary else ('inner_execution' if event.get_extra('_hosted_internal_work') else 'foreground') if event else (decision[6] if len(decision)>6 and decision[6] else decision[3].split(':',1)[-1]) if decision else 'data_task'
    value={'lease':lease,'provider':provider,'started':time.monotonic(),'phase':phase,'attemptId':attempt_id,'workId':getattr(work,'event_id',None) or (decision[5] if decision and len(decision)>5 else None),'scope':getattr(work,'scope',phase),'ended':False,
           'systemContract':system_contract,'systemPermitId':system_permit_id,'systemReservation':None}
    # Classifications only: no author ID, message text, credential or tool args.
    source=getattr(work,'source',None)
    config=lease.get('config') if isinstance(lease,dict) else None
    behavior=config.get('behavior') if isinstance(config,dict) else None
    owner=behavior.get('ownerUin') if isinstance(behavior,dict) else None
    physical_scope=getattr(work,'scope','')
    if not isinstance(physical_scope,str):physical_scope=''
    value['origin']={'eventKind':source if source in ('message','deferred_group','owner_contact','scheduled_contact','tool_completion','autonomy') else 'other' if work else 'auxiliary',
                     'senderKind':('owner' if owner and str(owner)==str(getattr(work,'subject',None)) else 'member') if work else 'none',
                     'scopeKind':'group' if physical_scope.startswith('group:') else 'private' if physical_scope.startswith('private:') else 'internal'}
    if foreground and event and event.get_extra('_hosted_journal_scope'):
        value['scope']=event.get_extra('_hosted_journal_scope')
    if auxiliary:
        value['workId']=auxiliary['workId']
        # Each bounded job normally makes one request; image batching is also
        # bounded. Do not retain response objects or request bodies here.
        auxiliary['attempts'].append(value)
    record_diagnostic(lease,{'kind':'request_start','attemptId':attempt_id,'phase':phase,'workId':value['workId'],'scopeHash':auxiliary['scopeHash'] if auxiliary else None,**value['origin']})
    return _attempt.set(value)

def expect_append_only_messages(messages):
    """Bind the current candidate wire prefix to the final HTTP request check."""
    value=_attempt.get()
    if not value:return False
    if not isinstance(messages,list):raise AppendOnlyWireViolation('append_only_candidate_shape')
    serialized=[];size=0
    try:
        for message in messages:
            row=json.dumps(message,ensure_ascii=False,separators=(',',':'),default=str)
            size+=len(row.encode('utf-8'))
            if size>16*1024*1024:raise AppendOnlyWireViolation('append_only_candidate_limit')
            serialized.append(row)
    except (TypeError,ValueError) as error:
        raise AppendOnlyWireViolation('append_only_candidate_serialize') from error
    value['appendOnlyExpected']=tuple(serialized)
    return True


def diagnostic_identity(lease):
    """Content-free attribution for the existing operator-armed wire capture."""
    value=_attempt.get()
    if not value or value.get('lease') is not lease:return {}
    return {key:value[key] for key in ('phase','attemptId','wireIndex') if key in value}

def _append_only_failure(value,lease,reason):
    record_diagnostic(lease,{'kind':'wire_request','attemptId':value.get('attemptId'),'phase':value.get('phase'),
        'workId':value.get('workId'),'wireIndex':value.get('wireIndex'),'appendOnlyStatus':'blocked','diagnosticDetail':reason})
    raise AppendOnlyWireViolation(reason)

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
        model=payload.get('model')
        value['model']=model if isinstance(model,str) and re.fullmatch(r'[\w.:/-]{1,128}',model) else None
        fields.update(toolsSha=hashlib.sha256(json.dumps(payload.get('tools'),ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:24],
                      routingSha=hashlib.sha256(str(payload.get('prompt_cache_key','')).encode()).hexdigest()[:24],journalReason=journal_reason)
        record_diagnostic(lease,{'kind':'request','attemptId':value['attemptId'],'phase':value['phase'],'workId':value['workId'],'model':value.get('model'),**fields})
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
        if not isinstance(lease,dict):lease=None
        expected=value.get('appendOnlyExpected')
        if expected is not None:value['appendOnlyValidationStarted']=True
        raw=request.content
        value['wireIndex']=value.get('wireIndex',0)+1
        if len(raw)>16*1024*1024:
            record_diagnostic(lease,{'kind':'wire_request','attemptId':value['attemptId'],'phase':value['phase'],
                'workId':value['workId'],'wireIndex':value['wireIndex'],'wireBytes':len(raw),'diagnosticDetail':'wire_body_limit'})
            if expected is not None:_append_only_failure(value,lease,'append_only_wire_limit')
            return
        try:payload=json.loads(raw)
        except (UnicodeDecodeError,ValueError,TypeError):
            if expected is not None:_append_only_failure(value,lease,'append_only_wire_unreadable')
            return
        if isinstance(payload,dict):
            model=payload.get('model')
            value['model']=model if isinstance(model,str) and re.fullmatch(r'[\w.:/-]{1,128}',model) else None
            if value['phase']=='embedding':
                record_diagnostic(lease,{'kind':'wire_request','attemptId':value['attemptId'],'phase':value['phase'],
                    'workId':value['workId'],'wireIndex':value['wireIndex'],'model':value['model'],'wireBytes':len(raw)})
        if not isinstance(payload,dict) or not isinstance(payload.get('messages'),list):
            if expected is not None:_append_only_failure(value,lease,'append_only_wire_messages_missing')
            return
        if expected is not None:
            try:
                actual=tuple(json.dumps(message,ensure_ascii=False,separators=(',',':'),default=str) for message in payload['messages'][:len(expected)])
            except (TypeError,ValueError):_append_only_failure(value,lease,'append_only_wire_serialize')
            if len(payload['messages'])<len(expected) or actual!=expected:
                _append_only_failure(value,lease,'append_only_wire_prefix_mismatch')
            value['appendOnlyValidationStarted']=False
            record_diagnostic(lease,{'kind':'wire_request','attemptId':value['attemptId'],'phase':value['phase'],
                'workId':value['workId'],'wireIndex':value['wireIndex'],'appendOnlyStatus':'verified'})
        contract=value.get('systemContract')
        if contract is not None:
            value['systemValidationStarted']=True
            try:
                # Per-scope baseline: one conversation's licensed System
                # transition must never block another scope still running the
                # previously committed vector (R1 cross-scope fix).
                reservation=contract.reserve(payload['messages'],value.get('systemPermitId'),value['attemptId'],time.time(),scope=value.get('scope') or '')
                value['systemReservation']=reservation
                value['systemValidationStarted']=False
                record_diagnostic(lease,{'kind':'wire_request','attemptId':value['attemptId'],'phase':value['phase'],
                    'workId':value['workId'],'systemSha':reservation.get('systemSha'),'systemContractStatus':'authorized' if reservation.get('changed') else 'stable'})
            except Exception as error:
                from system_contract import SystemContractViolation
                if not isinstance(error,SystemContractViolation):raise
                record_diagnostic(lease,{'kind':'wire_request','attemptId':value['attemptId'],'phase':value['phase'],
                    'workId':value['workId'],'systemSha':__import__('system_contract').system_vector_sha(payload['messages']),
                    'systemContractStatus':'blocked'})
                raise
        if isinstance(lease,dict):
            model=payload.get('model')
            value['model']=model if isinstance(model,str) and re.fullmatch(r'[\w.:/-]{1,128}',model) else None
            key=(lease.get('tenantId'),lease.get('botId'),value['scope'],value['phase'],value.get('model'))
            shape,fields=message_shape(payload['messages'],_wire_shapes.pop(key,()))
            _wire_shapes[key]=shape
            while len(_wire_shapes)>32:_wire_shapes.popitem(last=False)
            sha=lambda v:hashlib.sha256(json.dumps(v,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:24]
            fields.update(toolsSha=sha(payload.get('tools')),routingSha=hashlib.sha256(str(payload.get('prompt_cache_key','')).encode()).hexdigest()[:24],
                          parametersSha=sha({k:v for k,v in payload.items() if k not in ('messages','tools','prompt_cache_key')}),
                          journalReason=value.get('journalReason'),wireIndex=value['wireIndex'],wireBytes=len(raw),
                          toolCount=len(payload.get('tools') or []),toolsChars=len(json.dumps(payload.get('tools'),ensure_ascii=False,separators=(',',':'))))
            signatures={name:fields[name] for name in ('toolsSha','parametersSha','routingSha')}
            previous=_wire_signatures.pop(key,None)
            _wire_signatures[key]=signatures
            while len(_wire_signatures)>32:_wire_signatures.popitem(last=False)
            # These are observed differences, not claims about an upstream's
            # private cache key or pricing. The first sample has no baseline.
            fields['diagnosticDetail']=json.dumps({
                'wireBaselineKnown':previous is not None,
                **{name.removesuffix('Sha')+'Changed':signatures[name]!=previous[name] if previous is not None else None
                   for name in signatures}},separators=(',',':'))
            fields.update(model=value.get('model'),outputLimit=payload.get('max_completion_tokens',payload.get('max_tokens')) if type(payload.get('max_completion_tokens',payload.get('max_tokens'))) is int else None)
            record_diagnostic(lease,{'kind':'wire_request','attemptId':value['attemptId'],'phase':value['phase'],'workId':value['workId'],**value['origin'],**fields})
    except Exception:
        value=_attempt.get()
        if value and (value.get('systemValidationStarted') or value.get('appendOnlyValidationStarted')):raise
        return


def transport_started():
    value=_attempt.get()
    if not value:return None
    value['transportStarted']=True
    return {'attempt':value,'index':value.get('wireIndex',0),'started':time.monotonic(),'ended':False}


def transport_result(wire,status,reason=None,http_status=None):
    if not wire or wire['ended']:return
    wire['ended']=True;value=wire['attempt']
    record_diagnostic(value.get('lease'),{'kind':'wire_result','attemptId':value['attemptId'],
        'phase':value['phase'],'workId':value['workId'],'wireIndex':wire['index'],'model':value.get('model'),
        'status':status,'reason':reason,'httpStatus':http_status,'costStatus':'unknown',
        'durationMs':round((time.monotonic()-wire['started'])*1000)})


def finish_attempt(status,response=None,reason=None):
    value=_attempt.get()
    if not value or value['ended']:return
    value['ended']=True
    contract=value.get('systemContract')
    if contract is not None and value.get('systemReservation') is not None:
        contract.settle(value['systemReservation'],status=='completed',time.time())
    # Typed request-outcome evidence: a local rejection before the HTTP body
    # was handed over is NOT the same state as "the provider received a
    # request but the outcome is unknown". Unknown is never read as
    # "the model never saw this request".
    detail=None
    if status!='completed':
        detail=('provider_stream_started' if value.get('firstResponseMs') is not None else
                'provider_response_received' if value.get('headersReceived') else
                'request_submission_unknown' if value.get('transportStarted') else 'request_not_sent')
    # The settle boundary (behavior.settle_journal) needs the same typed
    # submission stage: a candidate whose request may have been received must
    # never fall back to a prefix without its tail (A package).
    value['submissionStage']=detail
    record_diagnostic(value.get('lease'),{'kind':'request_result','attemptId':value['attemptId'],'phase':value['phase'],'workId':value['workId'],
        'status':status,'reason':reason,'durationMs':round((time.monotonic()-value['started'])*1000),'firstResponseMs':value.get('firstResponseMs'),'costStatus':'unknown',
        'diagnosticDetail':detail,'model':value.get('model'),'wireCount':value.get('wireIndex',0),
        **(usage_evidence(response) if response is not None else {})})

def first_response():
    value=_attempt.get()
    if value and 'firstResponseMs' not in value:value['firstResponseMs']=round((time.monotonic()-value['started'])*1000)

def submission_stage():
    """Typed submission stage of the current (usually already finished) attempt."""
    value=_attempt.get()
    return value.get('submissionStage') if value else None

def record_error_site(error):
    """Bounded, content-free raise-site evidence for any request failure.

    Replaces the old cross-bot raw /tmp/request-errors.log: module basename,
    line, symbol and attempt identity only; never the exception text, its
    arguments or a traceback body (E package). TypeError and outer execution
    boundaries previously left only a class name, which rolled out of the
    256KiB diagnostic window before any root cause could be read (R0).
    """
    value=_attempt.get()
    if not value or not value.get('ended'):return
    # The caller may be outside this ContextVar after the provider wrapper
    # returns. Carry only typed, non-sensitive submission evidence on the
    # exception so budget accounting does not label local rejects as unknown.
    try:error._fishbot_submission_stage=value.get('submissionStage')
    except Exception:pass
    frame=None
    try:
        import traceback as _traceback
        frames=_traceback.extract_tb(error.__traceback__)
        frame=frames[-1] if frames else None
    except Exception:frame=None
    module=str(frame.filename or '').replace('\\','/').rsplit('/',1)[-1] if frame else None
    record_diagnostic(value.get('lease'),{'kind':'request_error','attemptId':value['attemptId'],'phase':value['phase'],
        'workId':value.get('workId'),'reason':type(error).__name__,
        'diagnosticDetail':json.dumps({'module':module,'line':frame.lineno if frame else None,
                                       'symbol':frame.name if frame else None},ensure_ascii=False)})


def record_execution_error(lease,error,phase='outer_execute',work_id=None):
    """Bounded raise-site evidence outside a model attempt (guarded_execute).

    Same content contract as record_error_site: never the exception text,
    arguments or a traceback body. Keeps outer TypeError/failure evidence
    inside the same diagnostic window as its pipeline ledger row."""
    frame=None
    try:
        import traceback as _traceback
        frames=_traceback.extract_tb(error.__traceback__)
        frame=frames[-1] if frames else None
    except Exception:frame=None
    module=str(frame.filename or '').replace('\\','/').rsplit('/',1)[-1] if frame else None
    record_diagnostic(lease,{'kind':'request_error','phase':phase,'workId':work_id,
        'reason':type(error).__name__,
        'diagnosticDetail':json.dumps({'module':module,'line':frame.lineno if frame else None,
                                       'symbol':frame.name if frame else None},ensure_ascii=False)})

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
    if event.get_extra('_hosted_journal_scope'):scope=event.get_extra('_hosted_journal_scope')
    if not isinstance(scope,str) or not scope:return None
    namespace=json.dumps([lease['tenantId'],lease['botId'],scope],ensure_ascii=False,separators=(',',':'))
    return 'fb-v1-'+hashlib.sha256(namespace.encode()).hexdigest()[:40]
