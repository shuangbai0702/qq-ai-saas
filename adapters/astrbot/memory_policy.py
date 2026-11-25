"""Shared summary policy constants and pure text/version helpers."""
import hashlib,json,re
from pathlib import Path


_memory_defaults=json.loads((Path(__file__).resolve().parents[2]/'packages/contracts/memory-defaults.json').read_text(encoding='utf-8'))
DEFAULT_SUMMARY_ROUNDS=_memory_defaults['summaryRounds']
COMPRESSION_DEADLINE_SECONDS=600
COMPRESSION_DRAIN_SECONDS=180
COMPRESSION_CANCEL_SECONDS=30
CONTINUITY_MAX_CHARS=500
FACT_NAME_CHARS=40
FACT_VALUE_CHARS=200
IMPRESSION_CHARS=1000

STOP_WORDS={'什么','么了','是什','我的','你的','记得','记住','一下','这个','那个','你好','知道','告诉','最近','现在','还是','之前','我们','可以','是不是','有什'}

def tokens(text):
    result=set(re.findall(r'[a-z0-9_]{2,}',text.lower()))
    for run in re.findall(r'[\u4e00-\u9fff]+',text):
        result.update(run[i:i+2] for i in range(len(run)-1))
    return result-STOP_WORDS

def version(row):
    return hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:32]

def safe(text,maximum):
    return isinstance(text,str) and 1<=len(text.strip())<=maximum and not re.search(r'(?i)sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+|密钥|密码|系统指令|管理员权限',text)

def scrub(value):
    from credential_redaction import redact
    return redact(value)[:800]

def origin(scope,event):
    if scope=='bot_shared':
        try:
            channel,identifier=json.loads(event)
            if isinstance(channel,str) and (channel=='self' or channel.startswith(('private:','group:'))):return channel,str(identifier)
        except (ValueError,TypeError):pass
    return scope,str(event)

def shared_person(scope,subject):
    return bool(re.fullmatch(r'\d{5,20}',str(subject)) and
                (scope=='bot_shared' or scope=='private:'+str(subject) or re.fullmatch(r'group:\d{4,20}',scope)))

def person_filter(scope,subject,alias=''):
    prefix=alias+'.' if alias else ''
    if shared_person(scope,subject):
        return prefix+"subject=? AND ("+prefix+"scope LIKE 'group:%' OR "+prefix+"scope='bot_shared' OR "+prefix+"scope=?)",(subject,'private:'+subject)
    return prefix+'scope=? AND '+prefix+'subject=?',(scope,subject)

def valid_scope(scope):
    return isinstance(scope,str) and bool(re.fullmatch(r'(?:self|bot_shared|private:\d{5,20}|group:\d{4,20})',scope))

def core_note_visible(scopes,scope,owner=''):
    """Internal read of the single core-note region, with known sources.

    Unknown provenance cannot establish trusted reference data. Such rows remain
    available to the internal/admin path and the owner's private briefing.
    """
    private_owner=bool(owner) and scope=='private:'+str(owner)
    if not isinstance(scopes,list) or not scopes or '*' in scopes:return scope=='self' or private_owner
    if scopes==[scope] and isinstance(scope,str) and scope:return True
    return valid_scope(scope) and all(valid_scope(x) for x in scopes)

def core_note_legacy(scopes):
    return not isinstance(scopes,list) or not scopes or '*' in scopes
