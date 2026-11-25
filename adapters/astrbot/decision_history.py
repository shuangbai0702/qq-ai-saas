"""Bounded request continuity, derived only from current authoritative input.

This is disposable model transport history, not memory or a task/permission
store. Restart, summary replacement and forgetting safely start a full request.
"""
import copy,difflib,json
from pathlib import Path

MAX_TURNS=json.loads((Path(__file__).resolve().parents[2]/'packages/contracts/memory-defaults.json').read_text(encoding='utf-8'))['summaryRounds']
# Permit normal growth up to the shared round boundary without repeatedly
# rebuilding a large baseline; absolute text/wire limits below still apply.
MAX_ADDED_CHARS=256000
# Active append journals budget text separately from encoded image transport.
# The baseline is authoritative; growth is bounded relative to that baseline,
# rather than giving a large baseline only a few remaining kilobytes.
# Keep bounded append-only headroom while a legal checkpoint is pending.
# This is a local transport limit, not a reconstruction permit or token limit.
MAX_RETAINED_TEXT_CHARS=1024000
MAX_RETAINED_WIRE_CHARS=4*1024*1024

def encode(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))

def history_usage(messages):
    """Serialized characters, never model tokens. Only known image URLs are media."""
    wire=sum(len(chunk) for chunk in json.JSONEncoder(ensure_ascii=False,separators=(',',':')).iterencode(messages))
    media=images=text=0
    for row in messages:
        content=row.get('content') if isinstance(row,dict) else None
        if isinstance(content,str):text+=len(content)
        elif isinstance(content,list):
            for part in content:
                if not isinstance(part,dict):continue
                if part.get('type')=='text' and isinstance(part.get('text'),str):text+=len(part['text'])
                elif part.get('type')=='image_url':
                    images+=1;url=(part.get('image_url') or {}).get('url') if isinstance(part.get('image_url'),dict) else None
                    if isinstance(url,str) and url.startswith('data:image/') and ';base64,' in url:media+=len(url)
    return {'wireChars':wire,'textChars':wire-media,'plainTextChars':text,'mediaChars':media,'imageCount':images}

def history_boundary(chars,base_chars,wire_chars=0,*,growth_limit=True):
    if wire_chars>MAX_RETAINED_WIRE_CHARS:return 'wire_size_limit'
    if chars>MAX_RETAINED_TEXT_CHARS:return 'text_size_limit'
    if growth_limit and chars-base_chars>MAX_ADDED_CHARS:return 'added_text_limit'
    return ''

def changes(before,after,path=()):
    """Send changed fields, never repeat a parent merely to shorten JSON."""
    replace=[['set',list(path),after]]
    if type(before)!=type(after):return replace
    if not isinstance(before,(dict,list)):return [] if before==after else replace
    result=[]
    if isinstance(before,dict):
        for key in before:
            if key not in after:result.append(['remove',list(path)+[key]])
        for key,value in after.items():
            result.extend(changes(before[key],value,(*path,key)) if key in before else [['set',list(path)+[key],value]])
    else:
        result=_index_changes(before,after,path)
        matched=_list_changes(before,after,path)
        if matched is not None and len(encode(matched))<len(encode(result)):result=matched
    return result

def _index_changes(before,after,path):
    result=[]
    for index in range(min(len(before),len(after))):result.extend(changes(before[index],after[index],(*path,index)))
    for index in reversed(range(len(after),len(before))):result.append(['remove',list(path)+[index]])
    for index in range(len(before),len(after)):result.append(['set',list(path)+[index],after[index]])
    return result

def _record_identity(row):
    """Stable transport identity: platform event provenance, else exact content.

    Rows carrying the same trusted source event stay the same record across a
    sliding-window shift; only its content may be updated in place. Versioned
    records expose their storage id the same way; identity only shapes which
    minimal operations are emitted, never the verified result.
    """
    if isinstance(row,dict):
        for key in ('source_event','event_id','round_event'):
            value=row.get(key)
            if value is not None:
                scope=row.get('source_scope')
                return ('event',str(scope),str(value))
        if row.get('id') is not None:
            return ('id',str(row['id']))
    return ('digest',encode(row))

def _list_changes(before,after,path):
    """Slide windows without resending shifted history (readonly delta P0).

    Records matched by stable identity emit only the head removals, in-place
    updates and tail appends the [set,path,value] protocol can express; any
    layout it cannot express (middle insertion) or oversized scans return None
    and the caller keeps whichever candidate encodes smaller. apply_changes
    stays the only verifier of either candidate.
    """
    if len(before)*len(after)>40000 or not before or not after:return None
    bi=[_record_identity(row) for row in before];ai=[_record_identity(row) for row in after]
    kept_before={};kept_after=set()
    for block in difflib.SequenceMatcher(None,bi,ai,autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            kept_before[block.a+offset]=block.b+offset;kept_after.add(block.b+offset)
    inserted=[index for index in range(len(after)) if index not in kept_after]
    if inserted and kept_after and min(inserted)<max(kept_after):return None
    if not inserted and not any(index not in kept_before for index in range(len(before))):return None
    result=[]
    for index in sorted(kept_before):
        if before[index]!=after[kept_before[index]]:result.extend(changes(before[index],after[kept_before[index]],(*path,index)))
    for index in sorted((index for index in range(len(before)) if index not in kept_before),reverse=True):
        result.append(['remove',list(path)+[index]])
    kept=len(kept_before)
    for offset,index in enumerate(inserted):result.append(['set',list(path)+[kept+offset],after[index]])
    return result

def apply_changes(snapshot,operations):
    """Verify the generated transport, never accept model-authored patches."""
    result=copy.deepcopy(snapshot)
    for operation in operations:
        path=operation[1]
        if not path:result=copy.deepcopy(operation[2]);continue
        node=result
        for key in path[:-1]:node=node[key]
        key=path[-1]
        if operation[0]=='remove':del node[key]
        elif isinstance(node,list) and key==len(node):node.append(copy.deepcopy(operation[2]))
        else:node[key]=copy.deepcopy(operation[2])
    return result

def summaries(value,path=()):
    """Summary replacement or memory disabling retires old request material."""
    if isinstance(value,list):return [row for i,item in enumerate(value) for row in summaries(item,(*path,i))]
    if not isinstance(value,dict):return []
    return [row for key,child in value.items() for row in
            ([((*path,key),child)] if key in ('conversation_summaries','memoryEnabled') else summaries(child,(*path,key)))]

