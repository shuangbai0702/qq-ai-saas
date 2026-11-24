"""DSH read adapter over original group tools, bound to one foreground Work.

No new history store, event, SDK context, or permission is created here.
"""
import asyncio
import copy
import json


def main_context_tool(tool):
    """Keep original destination/member lookup, project out message history.

    Apply to the raw group_actions tool before tool_outcome.structured. DSH
    calls the original tool, so its on-demand history remains available there.
    """
    if tool.name!='hosted_qq_group_context':return tool
    from astrbot.core.agent.tool import FunctionTool
    from tool_outcome import rejected
    class RoutingTool(FunctionTool):
        hosted_effect=False
        async def call(self,context,**kwargs):
            value=await tool.call(context,**kwargs)
            try:data=json.loads(value)
            except (ValueError,TypeError):return rejected('group_routing_invalid_result','未读取：群目标资料格式无效。')
            if not isinstance(data,dict):return rejected('group_routing_invalid_result','未读取：群目标资料格式无效。')
            # Preserve the original explicit rejection, including its reason.
            receipt=data.get('receipt')
            if data.get('execution')=='failed' or isinstance(receipt,dict) and receipt.get('status')=='rejected':return value
            def rows(items,fields):
                if not isinstance(items,list) or any(not isinstance(row,dict) for row in items):raise ValueError('invalid routing rows')
                return [{key:row[key] for key in fields if key in row} for row in items]
            try:
                if 'groups' in data:
                    result={'source':'当前机器人协议已加入且允许读取的群；名称仅是数据，不授予发送权限。',
                            'groups':rows(data['groups'],('group_id','name'))}
                elif 'group_id' in data and 'members' in data:
                    members=data['members']
                    # group_context_view currently projects protocol member
                    # rows into an ID -> display-name map.
                    if isinstance(members,dict):
                        if any(not isinstance(name,str) for name in members.values()):raise ValueError('invalid member map')
                        members=dict(members)
                    else:members=rows(members,('user_id','name'))
                    result={'group_id':data['group_id'],'source':'当前协议群成员；名称仅是显示名，不授予发送权限。',
                            'members':members}
                else:raise ValueError('unknown routing result')
            except ValueError:return rejected('group_routing_invalid_result','未读取：群目标资料格式无效。')
            return json.dumps(result,ensure_ascii=False,separators=(',',':'))
    return RoutingTool(name=tool.name,description='列出有权查看的群，或核对目的群的真实成员ID与显示名，供群投递目标确认。群聊深度历史查询委托DSH；名称只是数据，不授予发送权限。',
                       parameters=copy.deepcopy(tool.parameters),active=tool.active,is_background_task=tool.is_background_task)


class GroupReadBridge:
    def __init__(self, settings, event, current, db, context, parent):
        self.settings=settings
        self.event=event
        self.current=current
        self.db=db
        self.context=context
        self.parent=copy.deepcopy(parent)

    def _check(self):
        if self.context.context.event is not self.event or not self.current():
            raise asyncio.CancelledError('stale DSH group read')
        event=self.event
        work=event.get_extra('_hosted_pipeline')
        from pipeline_tools import validate_parent
        validate_parent(self.db,self.settings,self.parent)
        if work is not None and (work.scope,work.subject,work.event_id)!=(self.parent.get('origin_scope'),self.parent.get('sender_id'),self.parent.get('origin_event')):
            raise ValueError('dsh_group_work_mismatch')
        from dsh_bridge import parent_record
        if parent_record(self.settings,event,self.current,self.db,self.context)!=self.parent:raise ValueError('dsh_group_work_mismatch')
        group=str(event.get_group_id() or '')
        if group and work.scope!='group:'+group:
            raise ValueError('dsh_group_scope_mismatch')
        if not group and str(work.scope).startswith('group:'):
            raise ValueError('dsh_group_scope_mismatch')
        return group

    def _tools(self):
        group=self._check()
        if not group:return {}
        import group_actions
        cfg=self.settings['config']
        result={}
        # Existing authorization remains authoritative; visibility never grants
        # an owner or service role to an ordinary group sender.
        if group_actions.owner_request(self.event,cfg) or group_actions.service(self.event):
            result.update((t.name,t) for t in group_actions.tools(self.event,cfg,self.db,self.current,lambda *args:None)
                          if t.name=='hosted_qq_group_context')
        plugin=getattr(self.context.context,'context',None)
        if plugin is None:return result
        history=plugin.get_config(umo=self.event.unified_msg_origin).get('provider_ltm_settings',{})
        if history.get('group_message_history_enable',False):
            from astrbot.core.tools.message_tools import GetGroupMessageHistoryTool
            tool=plugin.get_llm_tool_manager().get_builtin_tool(GetGroupMessageHistoryTool)
            if tool is not None and tool.active:result[tool.name]=tool
        return result

    def definitions(self):
        return [{'name':tool.name,'description':tool.description,'parameters':copy.deepcopy(tool.parameters)}
                for tool in self._tools().values()]

    async def execute(self, name, args):
        tools=self._tools()
        if name not in tools:raise ValueError('dsh_group_tool_unavailable')
        tool=tools[name]
        if not isinstance(args,dict):raise ValueError('dsh_group_arguments_invalid')
        args=copy.deepcopy(args)
        # The SDK history schema leaves extra properties unspecified. Reject
        # target overrides even if a later SDK version adds another target.
        allowed={'limit','before_id','keyword','sender'} if name=='get_group_message_history' else {'group_id'}
        if set(args)-allowed:raise ValueError('dsh_group_arguments_invalid')
        import jsonschema
        jsonschema.validate(args,tool.parameters)
        if name=='hosted_qq_group_context':
            group=self._check()
            if args.get('group_id') not in (None,'',group):raise ValueError('dsh_group_target_denied')
            args['group_id']=group
        result=await tool.call(self.context,**args)
        self._check()
        return result
