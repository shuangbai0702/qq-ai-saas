"""Preserve OneBot custom emoji images through the pinned native converter."""
import copy
from urllib.parse import urlsplit


def qq_image_url(value):
    if not isinstance(value,str) or len(value)>8192:return False
    try:
        u=urlsplit(value)
        host=(u.hostname or '').lower()
        return (u.scheme in ('http','https') and not u.username and not u.password
                and u.port in (None,80,443)
                and any(host==suffix or host.endswith('.'+suffix) for suffix in ('qq.com','qq.com.cn','qpic.cn','gtimg.cn')))
    except ValueError:return False


def normalize(event):
    """Copy changed payloads only; identity and the original raw event survive.

    QQ market/favourite emoji can arrive as mface instead of image. Upstream
    deliberately drops mface. Do not manufacture a CDN address from an emoji ID
    or allow an arbitrary/local URL to become a new download route.
    """
    messages=event.get('message')
    if not isinstance(messages,list):return event
    result=[];changed=False
    for segment in messages:
        data=segment.get('data') or {}
        if segment.get('type')=='mface':
            url=data.get('url')
            result.append({'type':'image','data':{'file':url,'url':url,'summary':data.get('summary') or '[QQ自定义表情]','sub_type':1}}
                          if qq_image_url(url) else {'type':'text','data':{'text':'[QQ自定义表情图片不可用]'}})
            changed=True
        elif segment.get('type')=='image' and not data.get('file') and qq_image_url(data.get('url')):
            result.append({**segment,'data':{**data,'file':data['url']}});changed=True
        else:result.append(segment)
    if not changed:return event
    converted=copy.copy(event);converted['message']=result
    return converted


def install(adapter):
    original=adapter._convert_handle_message_event
    async def convert(self,event,*args,**kwargs):
        message=await original(self,normalize(event),*args,**kwargs)
        message.raw_message=event
        return message
    adapter._convert_handle_message_event=convert
