"""Trusted local DSH bridge manifest and parent-record authority."""
import asyncio,hashlib,json
from pathlib import Path
from urllib.parse import urlsplit


def manifest(settings,allow_newer=False):
    file=Path(settings['root'])/'ai'/'dsh-bridge.json'
    if not file.exists():return None
    if file.stat().st_size>8192:raise ValueError('dsh_manifest_too_large')
    value=json.loads(file.read_text(encoding='utf8'))
    url=urlsplit(value.get('url',''))
    if (value.get('version')!=2 or value.get('mode') not in ('local','production')
        or any(value.get(k)!=settings.get(k) for k in ('botId','tenantId','generation'))
        or (type(value.get('revision')) is not int or (value['revision']<settings['revision'] if allow_newer else value['revision']!=settings['revision']))
        or url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port
        or url.username or url.password or url.query or url.fragment or url.path!='/delegate'
        or not isinstance(value.get('token'),str) or len(value['token'])!=64):
        raise ValueError('dsh_manifest_invalid')
    return value


def bridge_current(settings,bridge):
    latest=manifest(settings,allow_newer=True)
    # Token/revision refresh is transport metadata; Node independently checks
    # every config revision and active Work. Capability revocation stays a fence.
    if not latest or latest['revision']<bridge['revision']:return False
    if latest['revision']==bridge['revision']:return latest==bridge
    return bool(isinstance(bridge.get('authorityHash'),str) and len(bridge['authorityHash'])==64
        and latest.get('authorityHash')==bridge['authorityHash'] and latest['token']!=bridge['token']
        and all(latest.get(k)==bridge.get(k) for k in ('version','mode','botId','tenantId','generation','url','capabilities')))


def parent_record(settings,event,current,db,context):
    if context.context.event is not event or not current():raise asyncio.CancelledError('stale DSH Work')
    work=event.get_extra('_hosted_pipeline');identity=event.get_extra('_hosted_internal_work')
    if work is None or event.get_extra('_hosted_tool_completion'):raise ValueError('dsh_foreground_work_required')
    if work.bot_id!=settings['botId']:raise ValueError('dsh_read_identity_mismatch')
    record={'bot_id':settings['botId'],'tenant_id':settings['tenantId'],'generation':settings['generation'],'config_revision':settings['revision'],
            'origin_scope':work.scope,'origin_event':work.event_id,'sender_id':work.subject}
    if identity:
        if not event.get_extra('_hosted_main_activation') or identity.get('principal')!='autonomous-service':raise ValueError('dsh_main_work_required')
        if any(identity.get(k)!=settings.get(k) for k in ('botId','tenantId','generation','revision')) or identity.get('id')!=work.event_id or work.subject!='service:'+settings['botId']:
            raise ValueError('dsh_autonomous_identity_mismatch')
        record.update(authority='autonomous',work_id=identity['id'],mode_epoch=identity['epoch'])
    else:
        if work.bot_id!=settings['botId'] or str(event.get_sender_id())!=str(work.subject):raise ValueError('dsh_read_identity_mismatch')
        from tool_outcome import foreground_work_id
        record.update(authority='foreground',work_id=foreground_work_id(work.bot_id,work.scope,work.subject,work.event_id))
    from pipeline_tools import validate_parent
    validate_parent(db,settings,record)
    return record
