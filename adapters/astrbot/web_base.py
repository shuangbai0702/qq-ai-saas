"""Project an authenticated web Persona edit onto its existing base region.

No history is reconstructed. The caller owns the trusted lease and the
SystemContract permit; neither model text nor Journal content grants authority.
Hashes refer to the existing configuration revisions, never a second history.
"""
import copy
import hashlib
import json
from context_protocol import request_lease,pin_request,release_request,system_head,ContextPrefixViolation,AUTHORITY_GUIDANCE


def same_owner(left,right):
    return left is right or bool(left and right and left.get('_base_owner') is not None and left.get('_base_owner') is right.get('_base_owner'))


def member_shape(body):
    """All non-Persona fields, including credentials and generation, must match.

    Only a hash lives beside the existing runtime member; no credentials or
    configuration history are retained here and this never enters model input.
    """
    value={k:copy.deepcopy(v) for k,v in body.items() if k not in ('revision','prompt','personaEdit','toolsEdit','characterCardsEdit','systemChangePermitId')}
    value.get('behaviorConfig',{}).pop('systemPrompt',None)
    return sha(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')))


def replace_cached_persona(manager,identifier,record):
    # Copy just the changed cached value. In-flight callers may still hold the
    # previous object; unrelated Personas and processed begin-dialogs stay put.
    manager.personas=[record if p.persona_id==identifier else p for p in manager.personas]
    for name in ('personas_v3','persona_v3_config'):
        rows=getattr(manager,name)
        setattr(manager,name,[{**row,'prompt':record.system_prompt} if row.get('name')==identifier else row for row in rows])
    selected=manager.selected_default_persona
    if selected and selected.persona_id==identifier:manager.selected_default_persona=record
    selected=manager.selected_default_persona_v3
    if selected and selected.get('name')==identifier:manager.selected_default_persona_v3={**selected,'prompt':record.system_prompt}


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


SDK_PERSONA_MARKER='\n# Persona Instructions\n\n'
SDK_PERSONA_XML_MARKER='<persona_instructions>\n'


def without_sdk_persona(text,persona):
    """Remove the exact duplicate inserted by AstrBot's Persona builder.

    Only fresh SDK configuration uses this helper. Unrecognized instructions
    and all text before/after the known insertion remain byte-faithful.
    """
    if not persona:return text
    stripped=legacy_sdk_persona(text,[sha(persona)],persona)
    return stripped if stripped is not None else text


def legacy_sdk_persona(text,origins,persona,*,replace=False):
    # Match the entire embedded Persona against authoritative config hashes.
    # A marker alone never authorizes editing arbitrary reference material.
    matches=[]
    for marker,ending in ((SDK_PERSONA_MARKER,'\n'),(SDK_PERSONA_XML_MARKER,'\n</persona_instructions>')):
        offset=0
        while (start:=text.find(marker,offset))>=0:
            begin=start+len(marker);offset=begin
            end=begin
            while (end:=text.find(ending,end))>=0:
                value=text[begin:end]
                if sha(value) in origins or value==persona:
                    matches.append((start,end+len(ending),marker,ending))
                end+=len(ending)
    if len(matches)!=1:return None
    start,end,marker,ending=matches[0]
    return text[:start]+(marker+persona+ending if replace else '')+text[end:]


def persona_view(previous, incoming, proof, persona):
    from context_protocol import is_persona_reference, persona_reference, persona_prompt
    if not proof:return incoming
    if not isinstance(proof,dict) or proof.get('target')!=sha(persona):
        raise ContextPrefixViolation('web_persona_revision_mismatch')
    origins=proof.get('origins')
    if not isinstance(origins,list) or not origins or any(not isinstance(v,str) or len(v)!=64 for v in origins):
        raise ContextPrefixViolation('web_persona_origin_missing')
    head=copy.deepcopy(previous[:system_head(previous)])
    matches=[];duplicates=[]
    for index,message in enumerate(head):
        if is_persona_reference(message):
            value=json.loads(message['content'].split('\n',1)[1])['persona']
            if sha(value) in origins or value==persona:
                matches.append((index,persona_reference(persona)))
            elif SDK_PERSONA_MARKER in value or SDK_PERSONA_XML_MARKER in value:
                stripped=legacy_sdk_persona(value,origins,persona)
                if stripped is not None:duplicates.append((index,persona_reference(stripped)))
            continue
        if message.get('role')!='system' or not isinstance(message.get('content'),str):continue
        # Old layouts put the Persona before the exact platform guidance. Use
        # configuration provenance AND an exact rendered prefix, not role or
        # arbitrary text matching. Everything after that prefix stays byte-faithful.
        content=message['content'];prefix,separator,tail=content.partition('\n'+AUTHORITY_GUIDANCE)
        sdk_replacement=legacy_sdk_persona(prefix,origins,persona,replace=True)
        if sdk_replacement is not None:
            matches.append((index,{**message,'content':sdk_replacement+(separator+tail if separator else '')}))
            continue
        variants=[(prefix,persona)]
        for images,chat in ((False,False),(True,False),(False,True),(True,True)):
            rendered=persona_prompt('',images=images,chat=chat)
            if prefix.startswith(rendered):
                variants.append((prefix[len(rendered):].lstrip('\n'),persona_prompt(persona,images=images,chat=chat)))
        valid=[replacement for raw,replacement in variants if sha(raw) in origins or raw==persona]
        if valid:
            replacement=valid[-1]+(separator+tail if separator else '')
            matches.append((index,{**message,'content':replacement}))
    # The former shared current_view accidentally omitted the independent
    # Persona. Only its recognizable fixed-platform-only head can acquire
    # this missing, user-authorized region; an unknown combined System cannot.
    if not matches and len(head)==1 and head[0].get('role')=='system' and isinstance(head[0].get('content'),str) and head[0]['content'].startswith('[FishBot 平台基础规则]\n'):
        return head+[persona_reference(persona)]+copy.deepcopy(incoming[system_head(incoming):])
    if len(matches)!=1:
        raise ContextPrefixViolation('web_persona_region_ambiguous_or_unproved')
    index,replacement=matches[0];head[index]=replacement
    for index,replacement in duplicates:head[index]=replacement
    return head+copy.deepcopy(incoming[system_head(incoming):])
