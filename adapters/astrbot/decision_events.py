"""Request-local event ownership for stateless auxiliary decisions.

No persistence and no content-based identity. Producers retain authority over
selection, provenance, delivery and permissions. Only this request is merged.
"""
import copy

WINDOWS=('messages','recent_received','recent_private','recent_uncompressed',
         'latest_messages','recent_cross_session_messages','cross_scope_deliveries','current_scope_feedback')


def contact_messages(db,scope):
    """Reuse contacts' existing protocol references, not adjacent chat guesses."""
    from message_provenance import incoming_item,own_item
    row=db.execute('SELECT subject,last_user,last_bot,last_text,last_output,last_user_event,last_bot_event FROM contacts WHERE scope=?',(scope,)).fetchone()
    if not row:return []
    person,user_at,bot_at,user_text,bot_text,user_event,bot_event=row
    result=[]
    if user_text:
        item=incoming_item(db,scope,person,user_event,user_text[:500],user_at) if user_event else {'role':'member','source_scope':scope,'sender_id':person,'at':user_at,'text':user_text[:500],'identity_status':'unproven'}
        result.append(item)
    if bot_text:
        recipient=db.execute('SELECT subject FROM pipeline_events WHERE scope=? AND event=?',(scope,str(bot_event))).fetchone() if bot_event else None
        item=own_item(db,scope,bot_event,recipient[0] if recipient else None,bot_at,bot_text[:500]) if bot_event else {'role':'self','source_scope':scope,'at':bot_at,'text':bot_text[:500],'identity_status':'unproven'}
        if item.get('receipt')!='confirmed':item['delivery']='unknown'
        result.append(item)
    return result


def project(payload):
    value=copy.deepcopy(payload);scope=value.get('scope') or value.get('context',{}).get('scope','');candidates=[]
    def take(parent):
        for name in WINDOWS:
            rows=parent.pop(name,None)
            if isinstance(rows,list):
                for row in rows:
                    if isinstance(row,dict):candidates.append(row)
        for name in ('attention','conversation'):
            nested=parent.get(name)
            if isinstance(nested,dict):
                rows=nested.pop('timeline',None)
                if isinstance(rows,list):candidates.extend(r for r in rows if isinstance(r,dict))
        original=parent.pop('original_message',None)
        if isinstance(original,dict):candidates.append(original)
    roots=[value]+[value[key] for key in ('context','changes','review') if isinstance(value.get(key),dict)]
    for parent in roots:
        take(parent)
        for name in ('memory','owner_memory'):
            memory=parent.get(name)
            if isinstance(memory,dict):take(memory)
    # Unmigrated callers' text is not silently discarded or attributed by prose.
    for key,role,clock in (('last_user_text','member','last_user_at'),('last_bot_text','self','last_bot_at')):
        text=value.pop(key,None)
        if isinstance(text,str) and text:candidates.append({'role':role,'source_scope':scope,'text':text,'at':value.get(clock),'identity_status':'unproven'})
    merged={};unproven=[]
    for raw in candidates:
        row=copy.deepcopy(raw)
        for aliases in (('source_scope','scope'),('source_event','event_id','event')):
            if len({str(row[k]) for k in aliases if row.get(k) not in (None,'')})>1:raise ValueError('auxiliary_event_identity_conflict')
        row['source_scope']=str(row.pop('scope',row.get('source_scope',scope)))
        event=row.pop('event_id',row.pop('event',row.get('source_event',None)))
        if event is not None:row['source_event']=str(event)
        outgoing=row.get('role') in ('self','assistant') or not row.get('role') and any(k in row for k in ('delivery','receipt','confirmed_text','draft_only'))
        row['role']='self' if outgoing else ('member' if row.get('role','member') in ('member','user') else row['role'])
        row.pop('confirmed_text_ref',None)
        if 'confirmed_text' in row:row['text']=row.pop('confirmed_text')
        draft=row.get('delivery')=='model_output_not_send_receipt'
        if draft and 'text' in row:row['draft_text']=row.pop('text')
        if row.get('receipt')=='confirmed' and 'delivery' not in row:
            row['delivery']='confirmed' if row.get('complete') is True else 'partial'
        key=(row['source_scope'],row['role'],str(event)) if row['source_scope'] and event not in (None,'') else None
        if not outgoing and not row.get('sender_id'):key=None
        if key is None:
            row['identity_status']='unproven';unproven.append(row);continue
        if key not in merged:merged[key]=row;continue
        prior=merged[key]
        if not outgoing and str(prior.get('sender_id'))!=str(row.get('sender_id')):raise ValueError('auxiliary_event_author_conflict')
        for field,item in row.items():
            if field not in prior or prior[field] in (None,'',[],{}):prior[field]=item;continue
            old=prior[field]
            if old==item:continue
            if field in ('text','draft_text') and isinstance(old,str) and isinstance(item,str):
                # Length is only compared AFTER protocol identity is proven.
                if old.startswith(item):continue
                if item.startswith(old):prior[field]=item;continue
                variants=prior.setdefault('content_variants',[])
                entry={'field':field,'text':item}
                if entry not in variants:variants.append(entry)
            elif field=='delivery':
                if old=='model_output_not_send_receipt':prior[field]=item
                elif item!='model_output_not_send_receipt' and old!=item:
                    # Conflicting real receipts cannot be settled by precedence.
                    prior['delivery']='unknown';prior['delivery_conflict']=True
            elif field in ('attachments','images','media','content','parts','message_ids','mentions') and isinstance(old,list) and isinstance(item,list):
                prior[field]=old+[x for x in item if x not in old]
            elif field=='text_complete' and isinstance(old,bool) and isinstance(item,bool):
                # A zero-text feedback view is not proof that the fuller
                # matching observation was incomplete.
                prior[field]=old or item
            elif field=='at':
                # Producers mark protocol time; otherwise retain the first
                # observation instead of fabricating an average/new timestamp.
                if row.get('time_source')=='protocol' and prior.get('time_source')!='protocol':prior[field]=item
            elif field=='time_source':
                if item=='protocol':prior[field]=item
            else:
                conflict={'field':field,'value':item}
                if conflict not in prior.setdefault('metadata_conflicts',[]):prior['metadata_conflicts'].append(conflict)
    rows=[*merged.values(),*unproven]
    for row in rows:
        text=row.get('text');draft=row.get('draft_text')
        if isinstance(text,str) and isinstance(draft,str) and text and draft.startswith(text):
            row.pop('draft_text')
            if len(draft)>len(text):row['unconfirmed_suffix']=draft[len(text):]
    rows.sort(key=lambda r:(not isinstance(r.get('at'),(int,float)),r.get('at') if isinstance(r.get('at'),(int,float)) else 0))
    # Quote edges do not repeat a target already available in this request.
    incoming={(r['source_scope'],r.get('source_event')):r for r in rows if r['role']=='member' and r.get('source_event')}
    quotes={}
    for row in rows:
        quote=row.get('reply_to')
        if not isinstance(quote,dict):continue
        event=quote.get('event_id');origin=quote.get('source_scope',row['source_scope']);key=(origin,str(event))
        target=incoming.get(key)
        if target and str(target.get('sender_id'))==str(quote.get('sender_id')):
            quote.pop('text',None)
        elif event:
            # Keep one target observation, never invent its receive time.
            if key not in quotes:
                quotes[key]={'role':'quoted','source_scope':origin,'source_event':str(event),**copy.deepcopy(quote)}
                quotes[key].pop('event_id',None)
            elif str(quotes[key].get('sender_id'))!=str(quote.get('sender_id')):
                raise ValueError('auxiliary_quote_author_conflict')
            elif isinstance(quote.get('text'),str) and len(quote['text'])>len(quotes[key].get('text','')):
                quotes[key]['text']=quote['text']
            quote.pop('text',None)
    if rows or quotes:
        target=value.setdefault('changes',{}) if 'reference' in value and isinstance(value.get('context'),dict) else value
        target['messages']=[*rows,*quotes.values()]
    return value
