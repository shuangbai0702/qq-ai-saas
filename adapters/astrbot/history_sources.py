"""On-demand views of the live Journal; references never own another history.

DSH persists only these references. Text and model images are resolved at its
request boundary; publication resolves the selected native QQ original. There
is no age window, nearest-image fallback, raw archive or write in this reader.
"""
import asyncio,base64,hashlib,io,re
from collections import Counter
from context_protocol import EVENT_CONTEXT_CURRENT,READONLY_CURRENT,image_notice,model_json,source


def _hash(value):return hashlib.sha256(model_json(value).encode()).hexdigest()


def _scope(event):
    work=event.get_extra('_hosted_pipeline')
    if work is None or not re.fullmatch(r'(private|group):\d{4,20}',str(work.scope)):
        raise ValueError('history_work_scope_required')
    internal=event.get_extra('_hosted_internal_work') or {}
    service=internal.get('principal')=='autonomous-service' and work.subject=='service:'+work.bot_id
    actual='group:'+str(event.get_group_id()) if event.get_group_id() else work.scope if service else 'private:'+str(event.get_sender_id())
    if work.scope!=actual:raise ValueError('history_event_scope_mismatch')
    return work.scope


def _parts(message):
    value=message.get('content')
    return [{'type':'text','text':value}] if isinstance(value,str) else value if isinstance(value,list) else []


def _rows(db,event):
    from journal_storage import read_snapshot
    scope=_scope(event);journal=event.get_extra('_hosted_journal_scope') or scope
    if journal not in (scope,'bot_shared'):raise ValueError('history_journal_scope_mismatch')
    key=model_json(journal)
    snapshot=read_snapshot(db,key,ready=True);result=[];active=None
    for message in (snapshot or {}).get('wire',[]):
        origin=source(message)
        if origin:
            active=None
            if origin['scope']!=scope:continue
            parts=_parts(message)
            images=[part['image_url']['url'] for part in parts if isinstance(part,dict) and part.get('type')=='image_url'
                    and isinstance(part.get('image_url'),dict) and isinstance(part['image_url'].get('url'),str)]
            # Quote images have different native message positions. Do not
            # silently select them as this author's direct attachment.
            quoted=set()
            for part in parts:
                notice=image_notice(part.get('text')) if isinstance(part,dict) else None
                match=re.fullmatch(r'\[Image (\d+) in quoted message(?:; animation converted to a 3x3 frame montage)?\]',notice) if isinstance(notice,str) else None
                if match:quoted.add(int(match[1]))
            texts=[p['text'] for p in parts if isinstance(p,dict) and p.get('type')=='text' and isinstance(p.get('text'),str)]
            texts[0]=texts[0].partition('\n')[2]
            text='\n'.join(texts)
            active={'event_id':str(origin['event_id']),'sender_id':str(origin['sender_id']),
                    'at':origin.get('at'),'text':text,'replies':[], 'images':images,'quoted':quoted,
                    'messages':[message]}
            result.append(active)
        elif message.get('role')=='user':
            text=message.get('content')
            if not isinstance(text,str) or not text.startswith((READONLY_CURRENT+'\n',EVENT_CONTEXT_CURRENT+'\n')):active=None
        elif active is not None and message.get('role')=='assistant':
            text='\n'.join(p['text'] for p in _parts(message) if isinstance(p,dict) and p.get('type')=='text' and isinstance(p.get('text'),str))
            if text:active['replies'].append(text)
            active['messages'].append(message)
    counts=Counter(row['event_id'] for row in result)
    # Ambiguous provenance cannot authorize a later get_msg or image position.
    return [dict(row,digest=_hash(row['messages'])) for row in result if counts[row['event_id']]==1]


def _image_id(settings,event,row,position):
    return 'qh_'+_hash([settings['botId'],str(event.get_self_id()),settings['generation'],_scope(event),row['event_id'],position,_hash(row['images'][position-1])])[:24]


def _reference(settings,event,row):
    # Opaque proof, not an event ID/URL index capable of reviving retired QQ
    # messages. Native Session files retain only this non-resolvable digest.
    return _hash([settings['botId'],str(event.get_self_id()),settings['generation'],_scope(event),row['digest']])


def _metadata(settings,event,row):
    return {'event_id':row['event_id'],'sender_id':row['sender_id'],'at':row['at'],
            'images':[{'source_id':_image_id(settings,event,row,i),'position':i}
                      for i in range(1,len(row['images'])+1) if i not in row['quoted']]}


def search(settings,event,db,*,limit=5,before=None,keyword='',sender_id=None,images_only=False):
    rows=_rows(db,event)
    if before is not None:
        index=next((i for i,row in enumerate(rows) if _reference(settings,event,row)==before),None)
        if index is None:raise ValueError('history_cursor_unavailable')
        rows=rows[:index]
    rows=[row for row in rows if (not sender_id or row['sender_id']==sender_id)
          and (not images_only or len(row['images'])>len(row['quoted']))
          and (not keyword or keyword.casefold() in '\n'.join([row['text'],*row['replies']]).casefold())]
    selected=rows[-limit:]
    return {'kind':'qq_history_page','scope':_scope(event),'refs':[_reference(settings,event,row) for row in selected],
            'more':len(rows)>limit,'before':_reference(settings,event,selected[0]) if len(rows)>limit else None,
            'coverage':'当前会话仍有效的历史；已压缩退休的原文与图片不可恢复。每条最多展示2000字符，可用关键词缩小范围。'}


def selected(settings,event,db,source_id):
    if not isinstance(source_id,str) or not re.fullmatch(r'qh_[a-f0-9]{24}',source_id):raise ValueError('history_image_source_required')
    matches=[(row,i) for row in _rows(db,event) for i in range(1,len(row['images'])+1)
             if i not in row['quoted'] and _image_id(settings,event,row,i)==source_id]
    if len(matches)!=1:raise ValueError('history_image_unavailable_or_retired')
    return matches[0]


def image_reference(settings,event,db,source_id):
    selected(settings,event,db,source_id)
    return {'kind':'qq_history_image','scope':_scope(event),'source_id':source_id}


def _image_copy(value):
    # Only a Journal-owned inline recognition copy, never a model-supplied URL
    # or a path. Original publication follows a separate native QQ read below.
    match=re.fullmatch(r'data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=]+)',value)
    if not match:raise ValueError('history_model_image_unavailable')
    raw=base64.b64decode(match[2],validate=True)
    if len(raw)>4*1024*1024:raise ValueError('history_image_budget')
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as picture:
        if picture.width*picture.height>16777216:raise ValueError('history_image_pixels')
        picture.load()
        # Journal images normally already fit. Constrain only this separate
        # DSH request copy; the immutable Journal bytes remain untouched.
        if max(picture.size)>512 or len(raw)>512*1024:
            picture=picture.convert('RGB');picture.thumbnail((512,512))
            output=io.BytesIO();picture.save(output,'JPEG',quality=80);raw=output.getvalue();mime='image/jpeg'
        else:mime=match[1]
        return {'mimeType':mime,'width':picture.width,'height':picture.height,'data':base64.b64encode(raw).decode()}


async def materialize(settings,event,db,reference,current):
    if not current():raise asyncio.CancelledError('stale history read')
    if not isinstance(reference,dict) or reference.get('scope')!=_scope(event):raise ValueError('history_reference_scope_mismatch')
    if reference.get('kind')=='qq_history_page':
        entries=reference.get('refs')
        if not isinstance(entries,list) or len(entries)>10:raise ValueError('history_reference_invalid')
        rows={_reference(settings,event,row):row for row in _rows(db,event)};items=[]
        remaining=32000
        for ref in entries:
            row=rows.get(ref) if isinstance(ref,str) else None
            if row is None:raise ValueError('history_reference_changed_or_retired')
            text=row['text'][:min(2000,remaining)];remaining-=len(text);replies=[]
            for value in row['replies']:
                part=value[:min(2000,remaining)];remaining-=len(part)
                replies.append({'text':part,'truncated':len(value)>len(part)})
                if not remaining:break
            items.append({**_metadata(settings,event,row),'text':text,'text_truncated':len(row['text'])>len(text),'replies':replies,
                          'replies_omitted':max(0,len(row['replies'])-len(replies))})
        return {'text':model_json({key:value for key,value in {**reference,'entries':items}.items() if key!='refs'})}
    if reference.get('kind')!='qq_history_image':raise ValueError('history_reference_invalid')
    expected=image_reference(settings,event,db,reference.get('source_id'))
    if reference!=expected:raise ValueError('history_reference_changed_or_retired')
    row,position=selected(settings,event,db,reference['source_id'])
    from image_codec import _run_codec
    image=await _run_codec(_image_copy,row['images'][position-1])
    if not current():raise asyncio.CancelledError('stale history image')
    if image_reference(settings,event,db,reference['source_id'])!=expected:raise ValueError('history_reference_changed_or_retired')
    return {'text':model_json({**expected,'event_id':row['event_id'],'sender_id':row['sender_id'],'at':row['at'],'position':position}),'images':[image]}


async def original_url(settings,event,db,source_id,current):
    if not current():raise asyncio.CancelledError('stale history original')
    row,position=selected(settings,event,db,source_id);proof=row['digest']
    # The native position excludes quoted images. Verify the complete direct
    # image vector so protocol and Journal cannot disagree on its numbering.
    direct=[i for i in range(1,len(row['images'])+1) if i not in row['quoted']]
    value=await asyncio.wait_for(event.bot.get_msg(message_id=int(row['event_id'])),5)
    if not current():raise asyncio.CancelledError('stale history original')
    latest,index=selected(settings,event,db,source_id)
    if latest['digest']!=proof or index!=position:raise ValueError('history_source_changed_or_retired')
    if isinstance(value,dict) and isinstance(value.get('data'),dict):value=value['data']
    group=str(event.get_group_id() or '')
    if (not isinstance(value,dict) or str(value.get('message_id'))!=row['event_id']
        or str((value.get('sender') or {}).get('user_id'))!=row['sender_id']
        or str(value.get('group_id') or '')!=group):raise ValueError('history_native_source_mismatch')
    segments=value.get('message')
    if not isinstance(segments,list):raise ValueError('history_native_images_unavailable')
    images=[p['data'].get('url') or p['data'].get('file') for p in segments if isinstance(p,dict)
            and p.get('type') in ('image','mface') and isinstance(p.get('data'),dict)]
    if len(images)!=len(direct):raise ValueError('history_native_image_positions_changed')
    return images[direct.index(position)]
