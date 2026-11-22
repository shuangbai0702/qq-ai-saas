"""User-owned System region. Pure rendering and precise, authorized projection.

Config revisions own cards; this module owns no database or second history.
"""
import copy,hashlib,json,re

MARKER='[FishBot 角色卡] '
JS_WHITESPACE='\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
def encoded(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))
def sha(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def utf16(value):return len(value.encode('utf-16-le'))//2
def validate(cards):
    if not isinstance(cards,list) or len(cards)>5:raise ValueError('character_cards_limit')
    ids=set()
    for c in cards:
        if (not isinstance(c,dict) or set(c)!={'id','name','kind','content','enabled'}
            or not isinstance(c['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',c['id']) or c['id'] in ids
            or c['kind'] not in ('meme','speech','world','custom') or type(c['enabled']) is not bool
            or any(not isinstance(c[k],str) or '\0' in c[k] or not c[k].strip(JS_WHITESPACE) or utf16(c[k])>limit for k,limit in [('name',80),('content',4000)])):
            raise ValueError('character_cards_invalid')
        ids.add(c['id'])
    return cards
def messages(cards):
    return [{'role':'system','content':MARKER+c['name']+'\n'+c['content']} for c in validate(cards) if c['enabled']]
def identity(cards):return sha([[c[k] for k in ('id','name','kind','content','enabled')] for c in validate(cards)])
def is_card(m):return m.get('role')=='system' and isinstance(m.get('content'),str) and m['content'].startswith(MARKER)
def is_note(m):
    from context_foundation import FOUNDATION_CURRENT
    content=m.get('content')
    if m.get('role')!='user' or not isinstance(content,str) or not content.startswith(FOUNDATION_CURRENT+'\n'):return False
    try:return json.loads(content.split('\n',1)[1]).get('region')=='core_note'
    except (ValueError,AttributeError):return False
def head_note(messages,index):
    from context_protocol import is_persona_reference
    return is_note(messages[index]) and index+1<len(messages) and (is_card(messages[index+1]) or is_persona_reference(messages[index+1]))

def retain_note_head(head,previous):
    from context_protocol import is_persona_reference
    result=copy.deepcopy(head)
    notes=[m for m in previous if is_note(m)]
    if notes and not any(is_note(m) for m in head):
        position=next((i for i,m in enumerate(result) if is_card(m) or is_persona_reference(m)),len(result))
        result[position:position]=copy.deepcopy(notes)
    return result

def layout(wire,cards,*,expected_note=None,origins=None,require_note=False):
    """Move only proven base Note/cards/Persona. Every other message is exact."""
    from context_protocol import is_persona_reference
    from readonly_journal import system_head,ContextPrefixViolation
    result=wire;end=system_head(result)
    # Legacy Note is immediately after the fixed head; never search history.
    if end<len(result) and is_note(result[end]):end+=1
    head=result[:end];tail=result[end:]
    old_cards=[m for m in head if is_card(m)]
    if origins is not None and sha(old_cards) not in origins:raise ContextPrefixViolation('character_cards_origin_mismatch')
    personas=[m for m in head if is_persona_reference(m)]
    notes=[m for m in head if is_note(m)]
    if require_note and not notes:raise ContextPrefixViolation('character_cards_note_missing')
    # The SDK's residual preamble shares Persona's reference encoding. Preserve
    # those distinct, already-owned slots instead of treating them as duplicates.
    if len(notes)>1 or not personas:raise ContextPrefixViolation('character_cards_base_ambiguous')
    if notes and expected_note is not None and json.loads(notes[0]['content'].split('\n',1)[1]).get('core_note')!=expected_note:
        raise ContextPrefixViolation('character_cards_note_mismatch')
    platform=[m for m in head if not (is_card(m) or is_note(m) or is_persona_reference(m))]
    if not platform or platform!=head[:len(platform)] or any(m.get('role') not in ('system','developer') for m in platform):raise ContextPrefixViolation('character_cards_platform_missing')
    target=platform+notes+messages(cards)+personas
    return wire if target==head else copy.deepcopy(target+tail)

def project(snapshot,change):
    if snapshot is None or change is None or not snapshot.base:return snapshot
    from readonly_journal import ContextPrefixViolation
    cards=validate(change['cards']);proof=change['proof']
    if not str(proof.get('permitId','')).isdecimal() or proof.get('target')!=identity(cards):raise ContextPrefixViolation('character_cards_proof_invalid')
    origins=proof.get('origins',[])
    if not isinstance(origins,list) or not origins or any(not isinstance(x,str) or not re.fullmatch(r'[0-9a-f]{64}',x) for x in origins):raise ContextPrefixViolation('character_cards_origins_invalid')
    note=snapshot.state.get('foundation_core_note',snapshot.state.get('memory_foundation',{}).get('core_note'))
    from readonly_journal import system_head
    if not isinstance(note,dict) and any(is_note(m) for m in snapshot.wire[:system_head(snapshot.wire)+1]):raise ContextPrefixViolation('character_cards_note_owner_missing')
    current=copy.copy(snapshot)
    current.base=layout(snapshot.base,cards,expected_note=note,origins=origins)
    current.wire=layout(snapshot.wire,cards,expected_note=note,origins=origins,require_note=bool(note and note.get('text')))
    return snapshot if current.base is snapshot.base and current.wire is snapshot.wire else current

def apply_user_cards(current,body):
    config=body.get('config');proof=body.get('characterCardsEdit')
    if (not isinstance(config,dict) or not isinstance(proof,dict) or not str(proof.get('permitId','')).isdecimal()
        or type(body.get('revision')) is not int or body['revision']<current['revision']
        or any(body.get(k)!=current.get(k) for k in ('botId','tenantId','generation'))):raise ValueError('character_cards_identity_conflict')
    if proof.get('target')!=identity(config.get('characterCards',[])):raise ValueError('character_cards_proof_invalid')
    origins=proof.get('origins')
    if not isinstance(origins,list) or not origins or any(not isinstance(x,str) or not re.fullmatch(r'[0-9a-f]{64}',x) for x in origins):raise ValueError('character_cards_origins_invalid')
    if sha(messages(current['config'].get('characterCards',[]))) not in origins:raise ValueError('character_cards_origin_mismatch')
    other=lambda c:{k:v for k,v in c.items() if k!='characterCards'}
    if other(config)!=other(current['config']):raise ValueError('character_cards_unrelated_change')
    if body['revision']==current['revision'] and config!=current['config']:raise ValueError('character_cards_revision_conflict')
    return {**current,'config':copy.deepcopy(config),'revision':body['revision'],'characterCardsEdit':copy.deepcopy(proof),'systemChangePermitId':body.get('systemChangePermitId')}

def member_shape(body):
    value={k:copy.deepcopy(v) for k,v in body.items() if k not in ('revision','personaEdit','toolsEdit','characterCardsEdit','systemChangePermitId')}
    value.get('behaviorConfig',{}).pop('characterCards',None)
    return sha(value)
