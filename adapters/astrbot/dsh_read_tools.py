"""DSH reads through the original tools and the original foreground Work.

No independent memory, collection inventory, provider or execution ledger lives
here. QQ list retains its existing verified readback reconciliation; DSH cannot
submit a write or choose a receipt to settle.
"""
import asyncio,copy,hashlib,json
from pathlib import Path


def _validate(settings,event,current,db,context):
    if not current():raise asyncio.CancelledError('stale DSH read Work')
    if getattr(getattr(context,'context',None),'event',None) is not event:
        raise ValueError('dsh_read_event_mismatch')
    work=event.get_extra('_hosted_pipeline')
    if work is None or event.get_extra('_hosted_internal_work') or event.get_extra('_hosted_tool_completion'):
        raise ValueError('dsh_foreground_work_required')
    if work.bot_id!=settings['botId'] or str(event.get_sender_id())!=str(work.subject):
        raise ValueError('dsh_read_identity_mismatch')
    record={'bot_id':settings['botId'],'tenant_id':settings['tenantId'],
            'generation':settings['generation'],'config_revision':settings['revision'],
            'authority':'foreground','origin_scope':work.scope,'origin_event':work.event_id,'sender_id':work.subject,
            'work_id':'foreground:'+hashlib.sha256(json.dumps([work.bot_id,work.scope,work.subject,work.event_id],ensure_ascii=False).encode()).hexdigest()}
    from pipeline_tools import validate_parent
    validate_parent(db,settings,record)
    return work.scope


def _material_tool(settings,event,db,social,scope):
    """Project the existing expression catalog, never the admin inbox/gallery."""
    behavior=settings['config'].get('behavior') or {}
    if social is None or not behavior.get('memeEnabled',True) or social.closed:return None
    if social.db is not db or Path(social.root).resolve()!=Path(settings['root']).resolve():
        raise ValueError('dsh_material_owner_mismatch')
    from astrbot.core.agent.tool import FunctionTool
    class Materials(FunctionTool):
        async def call(self,context):
            if social.closed:raise ValueError('dsh_material_unavailable')
            rows=social.catalog(scope,packs=behavior.get('memePacks'),favorite_account=str(event.get_self_id()))
            fields={'id','meaning','avoid','category','source','source_scope','pack','sticker'}
            return {'scope':scope,'items':[{key:value for key,value in row.items() if key in fields} for row in rows],
                    'review_inbox':{'available':False,'reason':'administrator_review_not_granted'},
                    'meaning':'仅复用当前配置可用素材和当前来源会话已审核表情的既有语义元数据，未查看图片、未调用图片模型；不包含待审核或其他会话收集素材。'}
    return Materials(name='hosted_material_catalog',description='只读盘点当前Work可用素材的既有语义标签、避用场景与来源：启用的本地反应图、平台包、当前QQ收藏缓存、当前会话已审核收集表情。仅元数据，未看图；不访问管理员审核收件箱，不提供审批或发送操作。',
                     parameters={'type':'object','properties':{},'additionalProperties':False})


def _bindings(settings,event,current,db,context,social=None):
    scope=_validate(settings,event,current,db,context)
    import execution_memory,qq_favorite_tools
    def authorized():
        # Original readers check this after each await, before readback may
        # reconcile an existing unknown receipt in the canonical ledger.
        try:_validate(settings,event,current,db,context);return True
        except ValueError:return False
    result=[]
    for tool in execution_memory.tools(settings,event,scope,authorized):
        if tool.name=='hosted_recall_experience' and tool.active:result.append((tool.name,'execution_review',tool,None))
    for tool in qq_favorite_tools.tools(event,settings['config'],db,settings['root'],authorized):
        if tool.name=='hosted_qq_favorites' and tool.active:
            result.extend((tool.name+'.'+operation,'favorite_audit',tool,operation) for operation in ('sources','list'))
    material=_material_tool(settings,event,db,social,scope)
    if material is not None:result.append((material.name,'favorite_audit',material,None))
    return result


def _parameters(tool,operation):
    parameters=copy.deepcopy(tool.parameters)
    if operation is not None:
        # These bound read operations have no caller-controlled arguments. Keep
        # all other schema policy from the real tool, without save/prepare inputs.
        parameters['properties']={}
        parameters['required']=[]
    return parameters


def catalog(settings,event,current,db,context,social=None):
    result=[]
    for identifier,capability,tool,operation in _bindings(settings,event,current,db,context,social):
        description=tool.description
        if operation=='sources':
            description='只读查询当前Work授权的QQ图片来源、近期图片元数据及本会话收藏写入回执；不访问QQ网络，不提供其他平台素材库存。'
        elif operation=='list':
            description='只读查询当前登录QQ的真实收藏及既有语义索引：句柄、meaning、avoid、rawText、selection_disabled和本会话保存时间；未标注项不猜语义。原后端以真实QQ详情核查历史未知写入，不提交收藏或发送操作。'
        definition={'name':identifier.replace('.','_'),'description':description,
                    'parameters':_parameters(tool,operation),'output':{'schema':{}}}
        version=hashlib.sha256(json.dumps(definition,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        result.append({'id':identifier,'capability':capability,'effect':'read','available':True,
                       'version':version,'definition':definition})
    return result


async def execute(settings,event,current,db,context,item_id,args,social=None):
    bindings=_bindings(settings,event,current,db,context,social)
    selected=next((entry for entry in bindings if entry[0]==item_id),None)
    if selected is None:raise ValueError('dsh_read_operation_unavailable')
    _,_,tool,operation=selected
    import jsonschema
    jsonschema.validate(args,_parameters(tool,operation))
    arguments=dict(args)
    if operation is not None:arguments['operation']=operation
    result=await tool.call(context,**arguments)
    _validate(settings,event,current,db,context)
    # Use the original receipt projection: reading never proves a fresh write.
    from tool_outcome import project
    return project(result,write=False)


def main_favorite_tool(tool):
    """Keep expression/save on the main model while DSH owns audit reads."""
    if tool.name!='hosted_qq_favorites':return tool
    from astrbot.core.agent.tool import FunctionTool
    from tool_outcome import rejected
    parameters=copy.deepcopy(tool.parameters)
    operations=[operation for operation in parameters['properties']['operation']['enum'] if operation in ('prepare','save')]
    parameters['properties']['operation']['enum']=operations
    class MainFavorites(FunctionTool):
        @property
        def hosted_effect(self):return bool(getattr(tool,'hosted_effect',False))
        @property
        def hosted_write_operations(self):return frozenset(getattr(tool,'hosted_write_operations',()) or ()) & frozenset({'save'})
        @property
        def hosted_result_detail(self):return bool(getattr(tool,'hosted_result_detail',False))
        @property
        def hosted_receipt_wrapped(self):return bool(getattr(tool,'hosted_receipt_wrapped',False))
        async def call(self,context,**arguments):
            if arguments.get('operation') not in operations:
                return rejected('dsh_favorite_read_required','收藏资料查询由DSH处理；本入口仅接受prepare和save。')
            return await tool.call(context,**arguments)
    return MainFavorites(name=tool.name,description='当前QQ收藏表达：prepare用DSH查询或真实收藏回执返回的favorite_handle选择收藏随本轮回复发送，也可由群发工具将已选图发往授权群；准备尚未发送。save用可信source_id保存收到的原图；current取本条图片，无图时只取当前发送者最近15分钟最新消息中唯一的图片，多图需选可信句柄。source_id与favorite_handle不能混用，不能提供URL或路径。保存不是平台收图；confirmed才证明对应动作成功，unknown表示待确认，不重复提交。执行时校验账号、会话与真实来源。',
                         parameters=parameters,active=tool.active,is_background_task=tool.is_background_task)
