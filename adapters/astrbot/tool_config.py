"""User-owned tool base updates. Never edits Persona, cognition or Journal."""
import copy
import hashlib
import json

CAPABILITIES=('webSearch','groupHistory','executionReview','favoriteAudit')
TOOL_FIELDS=('toolPrompts','delegatedCapabilities')

def tool_hash(config):
    prompt=config.get('toolPrompts',{}).get('dsh','')
    capabilities=config.get('delegatedCapabilities',{})
    return hashlib.sha256(json.dumps([prompt]+[capabilities.get(k) is not False for k in CAPABILITIES],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def apply_user_tools(current,body):
    """Caller authenticates local Agent; its audit proof cannot come from the model."""
    config=body.get('config');proof=body.get('toolsEdit')
    if (not isinstance(config,dict) or not isinstance(proof,dict)
        or not isinstance(proof.get('permitId'),str) or not proof['permitId'].isdecimal()
        or type(body.get('revision')) is not int or body['revision']<current['revision']
        or any(body.get(k)!=current.get(k) for k in ('botId','tenantId','generation'))):
        raise ValueError('tool_edit_identity_conflict')
    prompts=config.get('toolPrompts',{})
    caps=config.get('delegatedCapabilities',{})
    if (not isinstance(prompts,dict) or set(prompts)-{'dsh'} or not isinstance(prompts.get('dsh',''),str)
        or len(prompts.get('dsh',''))>12000 or not isinstance(caps,dict) or set(caps)-set(CAPABILITIES)
        or any(type(v) is not bool for v in caps.values()) or proof.get('target')!=tool_hash(config)):
        raise ValueError('tool_edit_proof_invalid')
    other=lambda value:{k:v for k,v in value.items() if k not in TOOL_FIELDS}
    if other(config)!=other(current['config']):raise ValueError('tool_edit_unrelated_change')
    if body['revision']==current['revision'] and config!=current['config']:raise ValueError('tool_edit_revision_conflict')
    return {**current,'config':copy.deepcopy(config),'revision':body['revision'],'toolsEdit':copy.deepcopy(proof)}

def member_shape(body):
    # A separate tool-region fingerprint: Persona and credentials still match.
    value={k:copy.deepcopy(v) for k,v in body.items() if k not in ('revision','personaEdit','toolsEdit','characterCardsEdit','systemChangePermitId')}
    for key in TOOL_FIELDS:value.get('behaviorConfig',{}).pop(key,None)
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
