"""Shared-runtime experiment guard. Only these two controlled tools are allowed.
Python extensions remain trusted service code, not a tenant security sandbox.
"""
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
        if target and ':' in str(target) and str(target).split(':',1)[0]!=id:
            return 'error: cross-bot destination denied'
        if set(kwargs)-{'session','messages'}:return 'error: unsupported send parameters'
        # A shared host's filesystem is not a tenant capability. URL media and
        # normal text are retained; arbitrary local/sandbox paths are denied.
        if any(message.get('path') for message in kwargs.get('messages',[])):
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
