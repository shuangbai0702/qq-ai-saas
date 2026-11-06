"""Shared feature-tool assembly for foreground, autonomous work and catalog.

Add hosted capabilities here. Catalogs inspect these very same tool objects;
factories retain identity/feature checks, calls retain live Work validation.
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
    return [structured(tool) for tool in result]


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
    return model_json({**value,'tool_context':tools})
