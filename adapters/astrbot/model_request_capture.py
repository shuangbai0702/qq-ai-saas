"""Operator-armed, bounded wire diagnostics. Disabled without a bot-local lease.

Only a trusted bot event or its canonical live provider selects its directory. Never copy
Authorization/cookies. Raw messages are private production data, not telemetry.
"""
import json,os,re,time,logging,contextvars,inspect
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import httpx

LOG=logging.getLogger(__name__)
from credential_redaction import pattern
SECRET=pattern(12,bearer=False)
_provider_owner=contextvars.ContextVar('fishbot_capture_provider_owner',default=None)
_cleaned={}

def prune_captures(root,now=None):
    """Expire only this module's recognized diagnostic files, never bot data."""
    now=time.time() if now is None else now
    folder=root/'model-request-captures'
    if not folder.is_dir() or folder.is_symlink():return
    for session in folder.iterdir():
        if not re.fullmatch(r'[a-f0-9]{24}',session.name) or session.is_symlink() or not session.is_dir():continue
        entries=list(session.iterdir())
        if not entries or any(p.is_symlink() or not p.is_file() or not re.fullmatch(r'[0-7]\.(request|response)\.json',p.name) for p in entries):continue
        if max(p.stat().st_mtime for p in entries)>=now-86400:continue
        for item in entries:item.unlink()
        try:session.rmdir()
        except OSError:pass # An active capture can have created a fresh file.

@contextmanager
def provider_scope(provider,settings,resolve):
    """Diagnostic attribution only; never supplies a conversational identity.

    Background reflection/orientation intentionally clears current_message_event.
    Bind only the canonical live provider, rechecking ownership at wire egress.
    """
    owner=None
    try:
        provider_id=provider.provider_config.get('id','')
        if isinstance(provider_id,str) and provider_id.startswith('provider-'):
            bot_id=provider_id[len('provider-'):];lease=settings.get(bot_id)
            if lease and lease.get('root') and resolve(bot_id) is provider:
                owner=(bot_id,lease,lambda:settings.get(bot_id) is lease and resolve(bot_id) is provider)
    except Exception:pass
    token=_provider_owner.set(owner)
    try:yield
    finally:_provider_owner.reset(token)

def bind_provider(call,settings,resolve):
    """Keep the existing async/stream contract, including cancellation cleanup."""
    if inspect.isasyncgenfunction(call):
        @wraps(call)
        async def stream(provider,*args,**kwargs):
            responses=call(provider,*args,**kwargs)
            try:
                while True:
                    with provider_scope(provider,settings,resolve):
                        try:response=await anext(responses)
                        except StopAsyncIteration:return
                    # Do not lend the provider binding to the stream consumer.
                    yield response
            finally:
                with provider_scope(provider,settings,resolve):await responses.aclose()
        return stream
    @wraps(call)
    async def chat(provider,*args,**kwargs):
        with provider_scope(provider,settings,resolve):return await call(provider,*args,**kwargs)
    return chat

def _private(path,value):
    raw=SECRET.sub('[redacted-key]',json.dumps(value,ensure_ascii=False)).encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw)

def begin(request):
    try:
        from astrbot.core.platform.astr_message_event import current_message_event
        event=current_message_event.get()
        lease=event.get_extra('_hosted_lease') if event else None
        bot_id=event.get_platform_id() if event else None
        kind='event'
        if event is None:
            owner=_provider_owner.get()
            if not owner or not owner[2]():return None
            bot_id,lease,_=owner;kind='background'
        if not lease or not lease.get('root'):return None
        root=Path(lease['root']);arm=root/'model-request-capture.json'
        if time.time()-_cleaned.get(str(root),0)>300:
            _cleaned[str(root)]=time.time()
            while len(_cleaned)>128:_cleaned.pop(next(iter(_cleaned)))
            prune_captures(root)
        if not arm.is_file():return None
        cfg=json.loads(arm.read_text())
        if cfg.get('botId')!=bot_id or not time.time()<cfg.get('expiresAt',0)<=time.time()+1800:return None
        session=cfg.get('session','')
        if not re.fullmatch(r'[a-f0-9]{24}',session):return None
        limit=cfg.get('maxRequests',0)
        if type(limit) is not int or not 1<=limit<=8:return None
        from model_cache import diagnostic_identity
        identity=diagnostic_identity(lease)
        phase=cfg.get('phase')
        if phase is not None and (not isinstance(phase,str) or not re.fullmatch(r'[a-z_]{1,48}',phase) or identity.get('phase')!=phase):return None
        raw=request.content
        if len(raw)>4*1024*1024:return None
        body=json.loads(raw)
        if not isinstance(body,dict) or body.get('model')!=cfg.get('model'):return None
        folder=root/'model-request-captures'/session;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        for index in range(limit):
            path=folder/(str(index)+'.request.json')
            try:_private(path,{'at':time.time(),'method':request.method,'endpoint':str(request.url.copy_with(query=None)),
                'headers':{k:v for k,v in request.headers.items() if k.lower() in ('user-agent','content-type','x-astrbot-conversation-id')},
                'botId':bot_id,'callKind':kind,'eventId':str(getattr(event.message_obj,'message_id','')) if event else '',
                **identity,
                'body':body})
            except FileExistsError:continue
            return Capture(folder/(str(index)+'.response.json'))
    except Exception:
        LOG.warning('Model request capture unavailable; request unchanged')
    return None

class Capture:
    def __init__(self,path):self.path=path;self.raw=bytearray();self.done=False;self.status=None
    def feed(self,data):
        if len(self.raw)+len(data)<=1024*1024:self.raw.extend(data)
    def finish(self,complete):
        if self.done:return
        self.done=True
        try:
            usage=[];finish_reasons=[];data=bytes(self.raw)
            parts=[line[5:].strip() for line in data.splitlines() if line.startswith(b'data:')]
            if not parts:parts=[data]
            for raw in parts:
                try:item=json.loads(raw)
                except (ValueError,UnicodeError):continue
                if isinstance(item,dict):
                    if item.get('usage') is not None:usage.append(item['usage'])
                    for choice in item.get('choices') or []:
                        if choice.get('usage') is not None:usage.append(choice['usage'])
                        reason=choice.get('finish_reason')
                        if isinstance(reason,str) and re.fullmatch(r'[a-z_]{1,32}',reason) and reason not in finish_reasons:
                            finish_reasons.append(reason)
            _private(self.path,{'at':time.time(),'status':self.status,'complete':complete,'usage':usage,'finishReasons':finish_reasons[:8]})
        except Exception:LOG.warning('Model response capture unavailable; response unchanged')

class CaptureStream(httpx.AsyncByteStream):
    def __init__(self,stream,capture):self.stream=stream;self.capture=capture
    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                self.capture.feed(chunk)
                yield chunk
        except BaseException:
            self.capture.finish(False)
            raise
        else:self.capture.finish(True)
    async def aclose(self):
        try:await self.stream.aclose()
        finally:self.capture.finish(False)
