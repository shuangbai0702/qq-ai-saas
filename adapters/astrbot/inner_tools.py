"""Shared feature-tool assembly for foreground, autonomous work and catalog.

Add hosted capabilities here. Catalogs inspect these very same tool objects;
declarations follow bot configuration, calls retain live Work validation.
"""
def features(settings,event,db,scope,current,*,identity=None,evidence=None,memory_current=None,effect_current=None,on_sent=None,social=None,review=None):
    import qq_actions,group_actions,quote_reply,qq_favorite_tools,execution_memory
    from bilibili_tools import tools as bili_tools,manifest
    from research_tools import tools as research_tools
    from bilibili_images import tools as image_tools
    cfg=settings['config']
    effect_current=effect_current or current
    result=qq_actions.tools(event,cfg,db,effect_current,settings)
    result+=group_actions.tools(event,cfg,db,current,on_sent or (lambda *args:None),social=social,review=review,effect_current=effect_current)
    result+=quote_reply.tools(event,cfg,db,settings,effect_current)+qq_favorite_tools.tools(event,cfg,db,settings['root'],effect_current)
    result+=execution_memory.tools(settings,event,scope,memory_current or current,identity)
    result+=bili_tools(settings,event,current,evidence,identity,db=db)
    result+=image_tools(settings,event,db,current,identity)
    # R5: the one constrained commitment-state tool shares this same
    # identity/Work validation as every other capability.
    import agreement_state
    result+=agreement_state.tools(settings,event,db,scope,current)
    if manifest(settings) is None:
        enabled=(cfg.get('behavior') or {}).get('bilibiliSearchEnabled',False)
        result+=research_tools(lambda:current() and (cfg.get('behavior') or {}).get('bilibiliSearchEnabled',False),evidence,enabled=enabled)
    from tool_outcome import structured
    # Dynamic candidates are data, never part of function definitions. Shared
    # by foreground and internal Work; metadata-only catalog events store none.
    if hasattr(event,'set_extra'):
        names={tool.name for tool in result};data={}
        if 'hosted_quote_reply' in names:
            data['quote_reply']=quote_reply.catalog(db,event,settings) if hasattr(event,'get_self_id') else []
        if 'hosted_qq_favorites' in names:data['qq_favorites']=qq_favorite_tools.context(event,db)
        if names & {'hosted_qq_signature','hosted_qq_publish','hosted_qq_avatar','hosted_qq_nickname','hosted_qq_status'}:
            data['qq_profile_limits']=qq_actions.limit_context(db,cfg)
        event.set_extra('_hosted_tool_context',data)
    return sorted([structured(tool) for tool in result],key=lambda tool:tool.name)


def completion_guard(tool,event):
    """Keep declarations identical while synthetic results retain narrow rights.

    Continuations may inspect receipts and continue their authenticated Bili
    request. They cannot turn a synthetic result into a fresh QQ action, task,
    agreement, or native send. The Bili backend validates the original permit.
    """
    if not event.get_extra('_hosted_tool_completion'):return tool
    from astrbot.core.agent.tool import FunctionTool
    from tool_outcome import rejected
    class CompletionTool(FunctionTool):
        @property
        def hosted_effect(self):return bool(getattr(tool,'hosted_effect',False))
        @property
        def hosted_write_operations(self):return getattr(tool,'hosted_write_operations',None)
        @property
        def hosted_result_detail(self):return bool(getattr(tool,'hosted_result_detail',False))
        async def call(self,context,**kwargs):
            if context.context.event is not event:return rejected('tool_event_mismatch','工具不属于当前事件。')
            if tool.name=='hosted_recall_experience' or tool.name.startswith('bilibili_') and tool.name!='bilibili_analyze':
                return await tool.call(context,**kwargs)
            return rejected('completion_capability_denied','工具结果续接不能授予新操作权限。')
    return CompletionTool(name=tool.name,description=tool.description,parameters=tool.parameters,active=tool.active,is_background_task=tool.is_background_task)


def request_context(event):
    """Append fresh tool data at the same trusted request-context boundary."""
    import json
    from context_protocol import model_json
    raw=event.get_extra('_hosted_context','{}')
    tools=event.get_extra('_hosted_tool_context')
    if not tools:return raw
    try:value=json.loads(raw)
    except (TypeError,ValueError):return raw
    if not isinstance(value,dict):return raw
    return model_json({**value,'tool_context':tool_context_view(tools)})


def tool_context_view(tools):
    """Present source catalogs in stable chronological order, with no data loss.

    Storage/tools still rank newest first. Model snapshots instead append new
    candidates at the tail, which the existing readonly set/remove protocol
    can express without retransmitting the entire list on every inbound event.
    Handles, scope, timestamps and runtime authorization remain unchanged.
    """
    import copy
    result=copy.deepcopy(tools)
    def chronological(rows):
        if not isinstance(rows,list) or not all(isinstance(r,dict) and type(r.get('at')) in (int,float) and isinstance(r.get('handle'),str) for r in rows):return rows
        return sorted(rows,key=lambda r:(r['at'],r['handle']))
    if not isinstance(result,dict):return result
    if 'quote_reply' in result:result['quote_reply']=chronological(result['quote_reply'])
    favorites=result.get('qq_favorites')
    if isinstance(favorites,dict):
        if 'media' in favorites:favorites['media']=chronological(favorites['media'])
        ids=favorites.get('source_ids')
        if isinstance(ids,list) and all(isinstance(v,str) for v in ids):
            favorites['source_ids']=sorted(ids,key=lambda v:(v!='current',v))
    return result
