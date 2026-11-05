"""Native tools reuse hosted identity, media guards and the output pipeline.
Python extensions remain trusted service code, not a tenant security sandbox.
"""
from media_egress import valid_url

def remote_media_url(value):return valid_url(value)

def install(members):
    if getattr(install,'installed',False):return
    from media_egress import install_downloads
    install_downloads()
    from astrbot.core.tools.message_tools import SendMessageToUserTool,GetGroupMessageHistoryTool
    history_original=GetGroupMessageHistoryTool.call
    def active(event):
        member=members.get(event.get_platform_id())
        lease=event.get_extra('_hosted_lease')
        return member is not None and lease is not None and member.get('generation')==lease.get('generation') and member.get('revision')==lease.get('revision')
    async def send(self,context,**kwargs):
        event=context.context.event;id=event.get_platform_id()
        if id not in members:return 'error: inactive bot'
        target=kwargs.get('session')
        if target is not None and (not isinstance(target,str) or len(target)>256):
            return 'error: invalid session'
        if target and ':' in str(target) and str(target).split(':',1)[0]!=id:
            return 'error: cross-bot destination denied'
        if set(kwargs)-{'session','messages'}:return 'error: unsupported send parameters'
        messages=kwargs.get('messages')
        if not isinstance(messages,list) or not 1<=len(messages)<=100:
            return 'error: invalid messages'
        for message in messages:
            if not isinstance(message,dict) or set(message)-{'type','text','path','url','mention_user_id'}:
                return 'error: invalid message component'
            if any(not isinstance(value,str) or len(value)>20000 for value in message.values()):
                return 'error: invalid message fields'
            if message.get('path'):
                return 'error: local file access denied in shared mode'
            kind=message.get('type','').lower()
            if kind not in ('plain','image','record','video','file','mention_user'):
                return 'error: invalid message type'
            required='text' if kind=='plain' else 'mention_user_id' if kind=='mention_user' else 'url'
            if not message.get('path') and not message.get(required,'').strip():
                return 'error: missing message content'
            if kind in ('image','record','video','file') and not remote_media_url(message.get('url')):
                return 'error: only HTTP(S) media URLs are allowed in shared mode'
        # A shared host's filesystem is not a tenant capability. URL media and
        # normal text are retained; arbitrary local/sandbox paths are denied.
        if any(message.get('path') for message in messages):
            return 'error: local file access denied in shared mode'
        if not active(event):return 'error: stale bot configuration'
        # Native generic sends use the same event.send/OutputPlan as replies.
        # The native SDK also requires admin permission for another session;
        # never silently reroute a different target into the current session.
        if target and str(target)!=str(getattr(event,'unified_msg_origin','')):
            return 'error: use the authorized hosted target tool for another session'
        work=event.get_extra('_hosted_pipeline')
        if work is None:return 'error: an owned pipeline Work is required'
        import asyncio,json,tempfile,time
        from pathlib import Path
        from media_egress import download_file,MEDIA_BYTES,MEDIA_SECONDS
        from astrbot.core.message import components as Comp
        from astrbot.core.message.message_event_result import MessageChain
        components=[]
        before=work.confirmed;before_attempted=work.attempted
        remaining=MEDIA_BYTES;deadline=time.monotonic()+MEDIA_SECONDS
        try:
            with tempfile.TemporaryDirectory(prefix='fishbot-media-') as directory:
                for index,message in enumerate(messages):
                    kind=message['type'].lower()
                    if kind=='plain':components.append(Comp.Plain(text=message['text']))
                    elif kind=='mention_user':components.append(Comp.At(qq=message['mention_user_id']))
                    else:
                        path=directory+'/'+str(index)
                        await asyncio.wait_for(download_file(message['url'],path,max_bytes=remaining),max(.01,deadline-time.monotonic()))
                        remaining-=Path(path).stat().st_size
                        if not active(event):return 'error: stale bot configuration'
                        cls={'image':Comp.Image,'record':Comp.Record,'video':Comp.Video,'file':Comp.File}[kind]
                        components.append(cls(file=path,name='media') if kind=='file' else cls.fromFileSystem(path=path))
                if not active(event):return 'error: stale bot configuration'
                await event.send(MessageChain(chain=components))
            confirmed=work.confirmed-before
            return json.dumps({'status':'confirmed' if confirmed else 'unknown','evidence':{'type':'protocol_receipt_count','confirmed':confirmed},'meaning':'protocol acceptance only; unknown must not be resent blindly'})
        except asyncio.CancelledError:raise
        except Exception:
            return json.dumps({'status':'partial' if work.confirmed>before else 'unknown' if work.attempted>before_attempted else 'rejected','code':'media_or_delivery_failed','evidence':{'confirmed':work.confirmed-before}})
    async def history(self,context,**kwargs):
        event=context.context.event
        if event.get_platform_id() not in members:return 'error: inactive bot'
        if set(kwargs)-{'limit','before_id','keyword','sender'}:
            return 'error: history target is fixed to the current bot and group'
        if not active(event):return 'error: stale bot configuration'
        return await history_original(self,context,**kwargs)
    SendMessageToUserTool.call=send;GetGroupMessageHistoryTool.call=history
    install.installed=True
