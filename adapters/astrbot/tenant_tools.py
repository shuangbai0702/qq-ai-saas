"""Shared-runtime experiment guard. Only these two controlled tools are allowed.
Python extensions remain trusted service code, not a tenant security sandbox.
"""
from urllib.parse import urlsplit

def remote_media_url(value):
    if not isinstance(value,str) or value!=value.strip() or any(ord(c)<=32 or ord(c)==127 for c in value) or '\\' in value:
        return False
    try:
        url=urlsplit(value)
        return url.scheme in ('http','https') and bool(url.hostname) and url.username is None and url.password is None and (url.port is None or 1<=url.port<=65535)
    except ValueError:
        return False

def install(members):
    from astrbot.core.tools.message_tools import SendMessageToUserTool,GetGroupMessageHistoryTool
    send_original=SendMessageToUserTool.call
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
        return await send_original(self,context,**kwargs)
    async def history(self,context,**kwargs):
        event=context.context.event
        if event.get_platform_id() not in members:return 'error: inactive bot'
        if set(kwargs)-{'limit','before_id','keyword','sender'}:
            return 'error: history target is fixed to the current bot and group'
        if not active(event):return 'error: stale bot configuration'
        return await history_original(self,context,**kwargs)
    SendMessageToUserTool.call=send;GetGroupMessageHistoryTool.call=history
