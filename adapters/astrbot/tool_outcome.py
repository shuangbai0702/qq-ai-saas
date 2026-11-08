"""Shared model-facing outcomes; producers retain authorization and receipts."""
import json
STATES={'confirmed','rejected','partial','unknown','not_applicable'}
EXECUTIONS={'completed','failed','canceled','pending'}
REJECTED={'blocked','unavailable','invalid_favorite','disabled_favorite','not_sent','invalid_reference','invalid_source'}

def rejected(code,message):
    return json.dumps({'execution':'failed','receipt':{'status':'rejected'},'code':code,'message':message,'evidence':[]},ensure_ascii=False)

def writes(tool,arguments=None):
    operations=getattr(tool,'hosted_write_operations',None)
    return (arguments or {}).get('operation') in operations if operations is not None else bool(getattr(tool,'hosted_effect',False))

def project(value,*,write=False,evidence=None):
    # MCP media is a real result, not an arbitrary JSON-serializable value.
    from_mcp=hasattr(value,'content') and hasattr(value,'isError')
    if from_mcp:
        failed=bool(value.isError)
        structured=getattr(value,'structuredContent',None)
        value=structured if isinstance(structured,dict) else {}
        if failed:
            receipt=value.get('receipt')
            if not isinstance(receipt,dict) or receipt.get('status') not in STATES:receipt={'status':'unknown' if write else 'rejected'}
            value={**value,'execution':'failed','receipt':receipt,'code':value.get('code') or 'tool_error'}
    try:data=json.loads(value) if isinstance(value,str) else value
    except (ValueError,TypeError):data=None
    if isinstance(data,dict):
        receipt=data.get('receipt');status=receipt.get('status') if isinstance(receipt,dict) else data.get('status')
        if status in REJECTED:status='rejected'
        execution=data.get('execution')
        if status not in STATES and (data.get('ok') is False or data.get('queued') is False or data.get('available') is False):
            status='rejected';execution='failed'
        if status not in STATES and data.get('queued') is True:
            status='not_applicable';execution='pending' if data.get('state')=='pending' else 'completed'
        if status in STATES:
            if execution not in EXECUTIONS:execution='failed' if status in ('rejected','unknown') else 'completed'
            return {**data,'execution':execution,'receipt':{**(receipt if isinstance(receipt,dict) else {}),'status':status},'evidence':data.get('evidence',evidence or [])}
    return {'execution':'failed' if write else 'completed','receipt':{'status':'unknown' if write else 'not_applicable'},'data':data if data is not None else value,'evidence':evidence or [],'meaning':'Only explicit receipts prove external writes; read completion does not verify source claims.'}

def structured(tool):
    """One envelope for JSON and MCP media, with declarative side effects."""
    if getattr(tool,'hosted_receipt_wrapped',False):return tool
    from astrbot.core.agent.tool import FunctionTool
    from mcp.types import CallToolResult,TextContent
    class ReceiptTool(FunctionTool):
        @property
        def hosted_receipt_wrapped(self):return True
        @property
        def hosted_effect(self):return bool(getattr(tool,'hosted_effect',False) or getattr(tool,'hosted_write_operations',()))
        @property
        def hosted_write_operations(self):return getattr(tool,'hosted_write_operations',None)
        @property
        def hosted_result_detail(self):return bool(getattr(tool,'hosted_result_detail',False))
        async def call(self,context,**kwargs):
            write=writes(tool,kwargs)
            try:
                value=await tool.call(context,**kwargs)
                if isinstance(value,CallToolResult):
                    texts=[part.text for part in value.content if isinstance(part,TextContent)]
                    original=getattr(value,'structuredContent',None)
                    if original is None and len(texts)==1:
                        try:original=json.loads(texts[0])
                        except (TypeError,ValueError):pass
                    if not isinstance(original,dict):original={'data':{'messages':texts}}
                    outcome=project(value.model_copy(update={'structuredContent':original}),write=write)
                    # Keep image/audio/resource blocks verbatim, with one text envelope.
                    return value.model_copy(update={'content':[TextContent(type='text',text=json.dumps(outcome,ensure_ascii=False))]+[part for part in value.content if not isinstance(part,TextContent)],'structuredContent':outcome})
                return json.dumps(project(value,write=write),ensure_ascii=False)
            except Exception as error:
                from native_internal import WorkStopped,WorkBudgetExceeded
                if isinstance(error,(WorkStopped,WorkBudgetExceeded)):raise
                return json.dumps({'execution':'failed','receipt':{'status':'unknown' if write else 'rejected'},'code':'tool_exception','error_type':type(error).__name__,'evidence':[],'meaning':'执行未完成；未知写入先核对原账本与平台状态，不得盲目重发。'},ensure_ascii=False)
    return ReceiptTool(name=tool.name,description=tool.description,parameters=tool.parameters,active=tool.active,is_background_task=tool.is_background_task)
