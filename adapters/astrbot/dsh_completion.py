"""Resume a claimed original media completion in its existing DSH session."""
import asyncio,json
import aiohttp
from dsh_bridge import manifest,bridge_current
from pipeline_tools import validate_parent


def final_text(value):
    if not isinstance(value,str) or not value.strip() or len(value.encode('utf8'))>65536:
        raise ValueError('dsh_final_contract_invalid')
    try:parsed=json.loads(value)
    except (ValueError,TypeError):parsed=None
    if isinstance(parsed,(dict,list)):raise ValueError('dsh_final_contract_invalid')
    return value.strip()


async def resume(settings,item,current,db):
    bridge=manifest(settings)
    if not bridge:raise ValueError('dsh_completion_bridge_unavailable')
    dsh=item.get('continuation',{}).get('dsh')
    if not isinstance(dsh,dict) or not isinstance(dsh.get('delegationId'),str) or not dsh['delegationId']:
        raise ValueError('dsh_completion_identity_invalid')
    parent={'bot_id':settings['botId'],'tenant_id':settings['tenantId'],'generation':item['generation'],
        'config_revision':item['revision'],'work_id':item['workId'],'authority':'foreground',
        'origin_scope':item['scope'],'origin_event':item['eventId'],'sender_id':str(item['senderId']),
        'continuation':{'sourceActionId':item['id'],'sourceOwner':item['owner']},
        **{key:item.get(key) for key in ('settings_revision','account_revision','account_uid')}}
    def check():
        if not current():raise asyncio.CancelledError('stale DSH completion')
        if not bridge_current(settings,bridge):raise ValueError('dsh_configuration_changed')
        validate_parent(db,settings,parent)
    check()
    payload={'protocol':2,'parent':parent,'delegationId':dsh['delegationId'],
        'completion':{'id':item['id'],'owner':item['owner']}}
    # Stay below the existing 120-second completion lease; this owns no timer
    # or scheduler. Node resumes the already persisted original session.
    terminal=False;result=None;size=0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=100),trust_env=False) as client:
        async with client.post(bridge['url'],json=payload,headers={'Authorization':'Bearer '+bridge['token']},allow_redirects=False) as response:
            if response.status!=200:raise ValueError('dsh_completion_rejected')
            async for line in response.content:
                size+=len(line)
                if len(line)>131072 or size>131072:raise ValueError('dsh_result_too_large')
                check()
                frame=json.loads(line)
                if terminal or not isinstance(frame,dict):raise ValueError('dsh_completion_protocol_invalid')
                if set(frame)=={'type','text'} and frame['type']=='final':result=final_text(frame['text'])
                elif frame=={'type':'pending'}:pass
                else:raise ValueError('dsh_completion_protocol_invalid')
                terminal=True
    check()
    if not terminal:raise ValueError('dsh_final_missing')
    return result
