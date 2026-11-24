"""New-response sanitization and separate-reasoning suppression boundaries."""
import re
from output_clean import clean as clean_internal


def is_no_reply_marker(text):
    return isinstance(text,str) and text.strip()=='[NO_REPLY]'


def clean_text(text,max_chars=None):
    text=clean_internal(text)
    if is_no_reply_marker(text):return []
    text=re.sub(r'\[(?:TOOL|INTERNAL|SYSTEM)[^\]]*\]','',text,flags=re.I)
    parts=[];offset=0
    for m in re.finditer(r'\[QQ_FACE:(\d{1,3})\]',text):
        if m.start()>offset:parts.append(('text',text[offset:m.start()]))
        if int(m[1]) in (14,4,5,32):parts.append(('face',int(m[1])))
        offset=m.end()
    if offset<len(text):parts.append(('text',text[offset:]))
    count=0;faces=0;out=[]
    for kind,value in parts:
        if kind=='face':
            if faces:continue
            faces+=1;out.append((kind,value))
        else:
            value=value if max_chars is None else value[:max(0,max_chars-count)];count+=len(value)
            if value.strip():out.append((kind,value))
    return out


def suppress_separate_reasoning(response,event):
    """Hide only a typed reasoning-only response; never guess from prose."""
    raw=getattr(response,'raw_completion',None)
    if raw is None:return False
    from model_cache import response_shape
    shape=response_shape(raw)
    if not shape.get('reasoningReported') or not (shape.get('reasoningChars') or 0):return False
    if shape.get('contentChars') not in (None,0):return False
    choices=raw.get('choices') if isinstance(raw,dict) else getattr(raw,'choices',None)
    choice=choices[0] if isinstance(choices,(list,tuple)) and choices else None
    message=(choice.get('message') if isinstance(choice,dict) else getattr(choice,'message',None)) if choice is not None else None
    tool_calls=message.get('tool_calls') if isinstance(message,dict) else getattr(message,'tool_calls',None)
    if tool_calls or getattr(response,'tools_call',None):return False
    if isinstance(getattr(response,'completion_text',None),str):response.completion_text=''
    if event:event.set_extra('_hosted_reply_withheld',True)
    return True


def sanitize_new_response(response,event=None):
    """The sole new-response boundary before native tool/history processing."""
    if getattr(response,'role',None)=='err':return
    value=getattr(response,'completion_text',None)
    if not isinstance(value,str):return
    cleaned=clean_internal(value)
    if cleaned!=value and event:
        event.set_extra('_hosted_output_controls_removed',event.get_extra('_hosted_output_controls_removed',0)+1)
    # A tool-call response is an execution step, not a public reply bubble.
    # Calls/arguments/IDs remain unchanged, and the terminal answer owns speech.
    if getattr(response,'tools_call_name',None):
        if hasattr(response,'result_chain'):response.result_chain=None
        response.completion_text=''
    elif cleaned!=value:
        chain=getattr(response,'result_chain',None)
        if chain is None:response.completion_text=cleaned
        else:
            # The SDK completion_text setter mutates the original chain.
            response.result_chain=chain.derive()
            if cleaned:response.result_chain.message(cleaned)
    # Typed reasoning/signatures remain private protocol fields where providers
    # require them for tool continuation. The runner's public channel excludes
    # reasoning chains; never relabel private fields as ordinary answer text.
