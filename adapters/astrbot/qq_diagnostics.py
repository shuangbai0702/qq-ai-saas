"""Public factual diagnostics; exception messages and secrets never cross here."""
import hashlib


def failure_notice(error_type=None, *, stage='model', reference='', partial=False):
    phases={'model':'模型回复','context':'上下文恢复','delegation':'委托执行','tool':'工具执行','worker':'后台任务','send':'QQ回复发送','compression':'上下文压缩','database':'资料读取'}
    phase=phases.get(stage,'本次处理')
    if error_type in ('TimeoutError','APITimeoutError','ReadTimeout','ConnectTimeout','ServerTimeoutError'):
        detail='等待响应超时，未取得完整结果。'
    elif error_type=='RateLimitError':detail='模型服务暂时限流，未取得完整回复。'
    elif error_type in ('AuthenticationError','PermissionDeniedError'):detail='模型服务拒绝了当前认证或权限，需要管理员检查配置。'
    elif stage=='context':detail='现有上下文尚未能安全接续，已保留历史，需要管理员检查恢复状态。'
    else:detail='没有正常完成，当前没有可确认的完整结果。'
    text=phase+'未完成。'+detail
    if partial:text+='已确认发送的内容保留，其余部分未确认；不会自动重发已确认部分。'
    else:text+='本次尚未生成完整答复。'
    text+='如果涉及已提交的操作，请先核对回执，不要重复提交；单纯查询可稍后再试。'
    if reference:text+=' 排查编号：'+hashlib.sha256(str(reference).encode()).hexdigest()[:10]
    return text
