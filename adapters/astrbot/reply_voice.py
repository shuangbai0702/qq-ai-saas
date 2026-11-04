"""Approved per-bot voice assets, selected by the model and delivered by OutputPlan.

The service-owned catalog is bound to tenant, bot and persona prompt. Model text
can select an ID, never a URL or filesystem path. No background task or cache.
"""
import hashlib,json,re
from pathlib import Path

_CONTROL=re.compile(r'\[\s*V\s*O\s*I\s*C\s*E\s*:([^\]]{0,512})(?:\]|$)',re.I)


def without_markers(text):return _CONTROL.sub('',text)


def load(setting):
    root=Path(setting['root']).resolve();path=root/'reply-voice.json'
    try:
        if path.is_symlink() or path.stat().st_size>8192:return None
        data=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data,dict):return None
        if data.get('version')!=1 or data.get('botId')!=setting['botId'] or data.get('tenantId')!=setting['tenantId']:return None
        if data.get('promptSha256')!=hashlib.sha256(setting['config']['systemPrompt'].encode()).hexdigest():return None
        assets=data.get('assets');default=data.get('default')
        if not isinstance(assets,dict) or not 1<=len(assets)<=8 or default not in assets:return None
        if 'bodyLengths' in data and (not isinstance(data['bodyLengths'],list) or not data['bodyLengths'] or any(type(n) is not int or not 1<=n<=100 for n in data['bodyLengths'])):return None
        if any(not isinstance(data.get(k,''),str) or len(data.get(k,''))>8 for k in ('prefix','suffix')):return None
        for key,item in assets.items():
            if not re.fullmatch('[a-z0-9_-]{1,32}',key) or not isinstance(item,dict):return None
            if not re.fullmatch(r'[a-z0-9_-]{1,80}\.mp3',item.get('file','')) or not re.fullmatch('[a-f0-9]{64}',item.get('sha256','')):return None
        return {**data,'root':str(root)}
    except (OSError,ValueError,KeyError,TypeError):return None


def hint(catalog):
    if not catalog:return ''
    choices=[{'id':key,'description':str(value.get('description',''))[:120]} for key,value in catalog['assets'].items()]
    return '\n[平台语音投递合同，以此为准]正常输出本轮聊天正文，各条空行分隔；末尾只选一个[VOICE:id]，可选资源：'+json.dumps(choices,ensure_ascii=False)+'。平台将正文分条、再独立发送所选真实语音。不要调用发送工具来逐条发正文或语音，不输出URL、路径、文字哈哈哈或工具额度说明。控制标记不对用户展示。没有有效选择时平台使用默认短版，不随机。\n'


def render(catalog,text):
    selected=[re.sub(r'\s+','',m[1]).lower() for m in _CONTROL.finditer(text)]
    text=without_markers(text)
    if not catalog:return text,None
    parts=[p.strip() for p in re.split(r'\n\s*\n',text) if p.strip()]
    lengths=catalog.get('bodyLengths');prefix=catalog.get('prefix','');suffix=catalog.get('suffix','')
    if lengths:
        # An explicit persona envelope also keeps framework diagnostics out of
        # this short-form character. Invalid content is never rewritten as fact.
        parts=[p for p in parts if p.startswith(prefix) and p.endswith(suffix) and
               len(body:=p[len(prefix):len(p)-len(suffix) if suffix else None]) in lengths and re.fullmatch(r'[\u3400-\u9fff]+',body)]
    else:parts=[p for p in parts if not re.fullmatch(r'哈+[.。…!！]*',p)]
    choice=next((key for key in reversed(selected) if key in catalog['assets']),catalog['default'])
    return '\n\n'.join(parts),choice


def materialize(catalog,choice):
    item=catalog['assets'][choice];root=Path(catalog['root']).resolve();path=root/'reply-voice'/item['file']
    if not path.resolve().is_relative_to(root/'reply-voice') or path.is_symlink():raise ValueError('Invalid voice asset path')
    with path.open('rb') as stream:data=stream.read(262145)
    if len(data)>262144 or hashlib.sha256(data).hexdigest()!=item['sha256']:raise ValueError('Voice asset integrity failed')
    return data
