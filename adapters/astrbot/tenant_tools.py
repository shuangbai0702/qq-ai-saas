"""Native tools reuse hosted identity, media guards and the output pipeline.
Python extensions remain trusted service code, not a tenant security sandbox.
"""
from media_egress import valid_url

def remote_media_url(value):return valid_url(value)

def hosted_send_definition(tool):
    """Copy the SDK declaration at the hosted boundary; never mutate its cache."""
    if tool.name!='send_message_to_user':return tool
    import copy
    # A changed SDK contract must stop before model submission, rather than
    # leave its unsupported capabilities advertised or fail with a bare KeyError.
    schema=tool.parameters
    try:
        properties=schema['properties'];messages=properties['messages'];item=messages['items'];fields=item['properties']
        supported={'type','text','url','mention_user_id'}
        compatible=(schema.get('type')=='object' and set(properties)=={'messages','session'}
                    and messages.get('type')=='array' and item.get('type')=='object'
                    and properties['session'].get('type')=='string'
                    and supported<=set(fields)<=supported|{'path'}
                    and all(isinstance(field,dict) and field.get('type')=='string' for field in fields.values()))
    except (KeyError,TypeError,AttributeError):compatible=False
    if not compatible:raise ValueError('hosted_native_send_schema_mismatch: unsupported SDK send declaration')
    result=copy.copy(tool)
    result.description=('向当前会话发送文本或媒体；普通文字回复可直接输出。媒体只接受HTTP(S) URL，'
                        '不支持本地或沙箱路径。session省略、留空或使用当前会话；其他会话须用获授权的hosted目标工具。'
                        '工具可见不等于获得新权限，实际发送仍按当前Work校验。')
    result.parameters=copy.deepcopy(tool.parameters)
    schema=result.parameters;schema['additionalProperties']=False
    messages=schema['properties']['messages'];messages.update(minItems=1,maxItems=100)
    item=messages['items'];item['additionalProperties']=False
    fields=item['properties'];fields.pop('path',None)
    fields['type']['enum']=['plain','image','record','video','file','mention_user']
    for field in fields.values():field['maxLength']=20000
    fields['url']['description']='image、record、video、file必填：可下载的HTTP(S)媒体URL。'
    fields['text']['description']='plain必填：非空文本。'
    fields['mention_user_id']['description']='mention_user必填：真实用户ID。'
    schema['properties']['session'].update(maxLength=256,description='省略或留空表示当前会话；如填写只能是当前unified_msg_origin，不支持跨会话。')
    return result

def _install_tool_catalog(active):
    """Keep native tools stable before the runner builds its execution table."""
    import inspect
    from functools import wraps
    from astrbot.core.agent.tool import ToolSet
    from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
    from astrbot.core.tools.message_tools import GetGroupMessageHistoryTool
    original=ToolLoopAgentRunner.reset
    @wraps(original)
    async def reset(self,*args,**kwargs):
        bound=inspect.signature(original).bind_partial(self,*args,**kwargs)
        context=bound.arguments.get('run_context')
        agent_context=getattr(context,'context',None)
        event=getattr(agent_context,'event',None)
        request=bound.arguments.get('request')
        if event is not None and request is not None and active(event) and request.func_tool:
            request.func_tool=ToolSet(tools=[hosted_send_definition(tool) for tool in request.func_tool.tools])
        if event is not None and request is not None and active(event) and not event.get_extra('_hosted_internal_work'):
            plugin_context=agent_context.context
            settings=plugin_context.get_config(umo=event.unified_msg_origin).get('provider_ltm_settings',{})
            if settings.get('group_message_history_enable',False):
                # The SDK adds this tool only for group events. Advertising the
                # same definition in private chats does not grant group access:
                # its guarded call still requires the current group and lease.
                previous=request.func_tool
                request.func_tool=ToolSet(tools=list(previous.tools) if previous else [])
                if request.func_tool.get_tool('get_group_message_history') is None:
                    from inner_tools import completion_guard
                    tool=plugin_context.get_llm_tool_manager().get_builtin_tool(GetGroupMessageHistoryTool)
                    request.func_tool.add_tool(completion_guard(tool,event))
                request.func_tool.tools.sort(key=lambda tool:tool.name)
        return await original(*bound.args,**bound.kwargs)
    ToolLoopAgentRunner.reset=reset
    # The SDK announces each call before invoking its executor, but does not
    # pass its ID to FunctionTool.call. Carry that existing identity only while
    # advancing this runner; no event-global mutable ID or model-facing data.
    handle=ToolLoopAgentRunner._handle_function_tools
    @wraps(handle)
    async def handle_tools(self,*args,**kwargs):
        from group_actions import tool_action_id
        iterator=handle(self,*args,**kwargs)
        action=None
        try:
            while True:
                token=tool_action_id.set(action)
                try:result=await anext(iterator)
                except StopAsyncIteration:return
                finally:tool_action_id.reset(token)
                chain=getattr(result,'message_chain',None)
                if chain is not None and chain.type=='tool_call':
                    components=chain.chain
                    data=getattr(components[0],'data',None) if len(components)==1 else None
                    action=data.get('id') if isinstance(data,dict) else None
                yield result
        finally:
            await iterator.aclose()
    ToolLoopAgentRunner._handle_function_tools=handle_tools

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
    _install_tool_catalog(active)
    install.installed=True
