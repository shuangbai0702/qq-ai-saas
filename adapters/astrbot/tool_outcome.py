"""Common projection of existing tool receipts; never infer writes from prose."""
import json

STATES={'confirmed','rejected','partial','unknown'}

def project(value,*,write=False,evidence=None):
    try:data=json.loads(value) if isinstance(value,str) else value
    except (ValueError,TypeError):data=None
    if isinstance(data,dict):
        receipt=data.get('receipt')
        status=receipt.get('status') if isinstance(receipt,dict) else data.get('status')
        if status in ('blocked','unavailable','invalid_favorite','disabled_favorite'):
            status='rejected'
        if status in STATES:
            return {**data,'receipt':{**(receipt if isinstance(receipt,dict) else {}),'status':status},'evidence':data.get('evidence',evidence or [])}
    return {'receipt':{'status':'unknown' if write else 'confirmed'},'data':data if data is not None else value,'evidence':evidence or [],'meaning':'Only explicit receipts prove external writes; read completion does not verify source claims.'}


def structured(tool):
    """Project an existing tool's result, without changing its authorization.

    The original object still owns execution, identity and receipts. This is a
    wire envelope for both foreground and internal ToolLoop callers.
    """
    from astrbot.core.agent.tool import FunctionTool
    class ReceiptTool(FunctionTool):
        @property
        def hosted_effect(self):
            return bool(getattr(tool,'hosted_effect',False) or tool.name in ('hosted_qq_group_send','hosted_qq_favorites'))
        async def call(self,context,**kwargs):
            write=self.hosted_effect
            if tool.name=='hosted_qq_favorites':write=kwargs.get('operation')=='save'
            try:value=await tool.call(context,**kwargs)
            except Exception as error:
                # A thrown transport error cannot prove a write was rejected.
                return json.dumps({'receipt':{'status':'unknown' if write else 'rejected'},
                                   'code':'tool_exception','error_type':type(error).__name__,
                                   'evidence':[],'meaning':'执行未完成；未知写入先核对原账本与平台状态，不得盲目重发。'},ensure_ascii=False)
            return json.dumps(project(value,write=write),ensure_ascii=False)
    return ReceiptTool(name=tool.name,description=tool.description,parameters=tool.parameters,
                       active=tool.active,is_background_task=tool.is_background_task)
