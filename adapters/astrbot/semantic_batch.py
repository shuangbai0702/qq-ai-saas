"""A successful semantic batch replaces hot material, without per-event Coverage.

Members are opaque scoped hashes. They prove which transport blocks may leave,
not which facts a model must remember. No model-authored retirement whitelist.
"""
import copy,hashlib,json,time
from context_protocol import model_json


def event_key(scope,event):return hashlib.sha256(model_json([scope,str(event)]).encode()).hexdigest()


def initialize(db):
    db.execute('PRAGMA secure_delete=ON')
    db.execute('''CREATE TABLE IF NOT EXISTS memory_semantic_batches(
      checkpoint_id INTEGER PRIMARY KEY,scope TEXT NOT NULL,digest TEXT NOT NULL,
      members TEXT NOT NULL,cutoff REAL NOT NULL,state TEXT NOT NULL,
      created REAL NOT NULL,completed REAL)''')


def permit(db,checkpoint_id):
    initialize(db)
    row=db.execute("SELECT digest,members,cutoff FROM memory_semantic_batches WHERE checkpoint_id=? AND state IN ('committed','retired')",(checkpoint_id,)).fetchone()
    return {'semanticBatch':True,'batchDigest':row[0],'batchMembers':json.loads(row[1]),'batchCutoff':row[2]} if row else {}


def discard_failed_service_inputs(db,scope):
    """Remove proven unsubmitted internal control inputs, never chat material.

    The caller owns the transaction. Missing or conflicting evidence protects
    the row; no material, round, checkpoint or delivery ledger is fabricated.
    """
    from context_protocol import source
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required={'memory_pending','memory_message_sources','pipeline_events','memory_rounds',
              'memory_round_material','pipeline_scope_work','pipeline_work_inputs'}
    if not required<=tables:return 0
    removed=0
    for event,subject,question,answer in db.execute('SELECT event,subject,question,answer FROM memory_pending WHERE scope=?',(scope,)).fetchall():
        if answer:continue
        origin_source=source({'role':'user','content':question})
        channel,identifier=origin(scope,event)
        if not origin_source or origin_source.get('kind')!='platform_service_not_user' or any(
            str(origin_source.get(key,''))!=str(value) for key,value in
            (('scope',channel),('event_id',identifier),('sender_id',subject))):continue
        shared=model_json([channel,identifier]);identities={(channel,identifier),('bot_shared',shared)}
        if (scope,str(event)) not in identities:continue
        # The service input speaks as the bot; the owner-contact Work is
        # addressed to the contact. Those principals need not be identical.
        ledgers=db.execute('SELECT source,status,attempted,confirmed FROM pipeline_events WHERE scope=? AND event=?',(channel,identifier)).fetchall()
        if ledgers!=[('owner_contact','error',0,0)]:continue
        bindings=db.execute("SELECT data FROM memory_message_sources WHERE scope=? AND event=? AND direction='model'",(scope,event)).fetchall()
        if len(bindings)!=1:continue
        try:binding=json.loads(bindings[0][0])
        except (TypeError,ValueError):continue
        if not isinstance(binding,dict) or 'input_digest' in binding or binding.get('outputs')!=[]:continue
        if any(str(binding.get(key,''))!=str(value) for key,value in
               (('source_scope',channel),('event_id',identifier),('sender_id',subject))):continue
        settlement=binding.get('settlement')
        if not isinstance(settlement,dict) or settlement.get('status')!='error' or any(
            type(settlement.get(key)) is not int or settlement[key]!=0
            for key in ('planned','attempted','confirmed','confirmed_messages')):continue
        protected=False
        for logical,key in identities:
            for other_binding, in db.execute("SELECT data FROM memory_message_sources WHERE scope=? AND event=? AND direction='model'",(logical,key)):
                try:
                    if json.loads(other_binding)!=binding:protected=True
                except (TypeError,ValueError):protected=True
            for table in ('memory_rounds','memory_round_material','memory_pending_rounds','memory_hold_rounds','pipeline_scope_work'):
                if table in tables and db.execute('SELECT 1 FROM '+table+' WHERE scope=? AND event=?',(logical,key)).fetchone():protected=True
            if db.execute("SELECT 1 FROM pipeline_work_inputs WHERE scope=? AND event=? AND status='owned'",(logical,key)).fetchone():protected=True
            # A real input at either identity must never be removed along with
            # a service duplicate that happens to reuse its transport key.
            for other_subject,other_question,other_answer in db.execute('SELECT subject,question,answer FROM memory_pending WHERE scope=? AND event=?',(logical,key)):
                other=source({'role':'user','content':other_question})
                if other_answer or str(other_subject)!=str(subject) or not other or other.get('kind')!='platform_service_not_user' or any(
                    str(other.get(field,''))!=str(value) for field,value in
                    (('scope',channel),('event_id',identifier),('sender_id',subject))):protected=True
        if protected:continue
        for logical,key in identities:
            removed+=db.execute('DELETE FROM memory_pending WHERE scope=? AND event=?',(logical,key)).rowcount
            db.execute("DELETE FROM memory_message_sources WHERE scope=? AND event=? AND direction IN ('model','incoming')",(logical,key))
    return removed


def load(db,scope,payload):
    """Complete exact material, all images in the SAME summary model request."""
    from summary_material import valid_material
    materials={};images={};bindings={}
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for row in payload:
        event=str(row['event'])
        origin_scope,origin_event=origin(scope,event)
        if 'pipeline_scope_work' in tables and db.execute('SELECT 1 FROM pipeline_scope_work WHERE scope=? AND event=?',(origin_scope,origin_event)).fetchone():
            raise ValueError('semantic_batch_work_still_active')
        if 'pipeline_work_inputs' in tables and db.execute("SELECT 1 FROM pipeline_work_inputs WHERE scope=? AND event=? AND status='owned'",(origin_scope,origin_event)).fetchone():
            raise ValueError('semantic_batch_input_still_owned')
        saved=db.execute('SELECT material FROM memory_round_material WHERE scope=? AND event=?',(scope,event)).fetchone()
        if not saved:raise ValueError('semantic_batch_material_missing')
        item=json.loads(saved[0])
        if not valid_material(item,scope) or str(item['event'])!=event or not item.get('complete'):
            raise ValueError('semantic_batch_material_incomplete')
        materials[event]=item
        for image in item.get('images',[]):
            key=hashlib.sha256(image.encode()).hexdigest();images.setdefault(key,image);bindings.setdefault(key,[]).append(event)
    if not materials:raise ValueError('semantic_batch_empty')
    return materials,list(images.values()),[{'id':key,'position':i+1,'events':bindings[key]} for i,key in enumerate(images)]


def origin(scope,event):
    if scope=='bot_shared':
        try:
            channel,identifier=json.loads(event)
            if isinstance(channel,str) and channel.startswith(('private:','group:')):return channel,str(identifier)
        except (ValueError,TypeError):pass
    return scope,str(event)


def verify(db,scope,materials):
    for event,item in materials.items():
        row=db.execute('SELECT material FROM memory_round_material WHERE scope=? AND event=?',(scope,event)).fetchone()
        if not row or json.loads(row[0])!=item:raise ValueError('semantic_batch_material_changed')


def semantic_claims(claims):
    """Selected semantic memory may retain scope/speaker, never original IDs."""
    result=copy.deepcopy(claims)
    for claim in result:
        for ref in claim.get('sourceRefs',[]):
            if ref.get('scope')=='bot_shared':
                try:
                    source_scope,original=json.loads(ref['event'])
                    if isinstance(source_scope,str) and source_scope.startswith(('private:','group:')):ref['scope']=source_scope
                except (ValueError,TypeError,KeyError):pass
            if not str(ref['event']).startswith('semantic:'):ref['event']='semantic:'+hashlib.sha256(str(ref['event']).encode()).hexdigest()
    return result


def commit(db,scope,checkpoint_id,materials,cutoff):
    """Called in the successful memory transaction; failures roll back all rows."""
    initialize(db);verify(db,scope,materials)
    members=sorted(event_key(scope,event) for event in materials)
    signature=hashlib.sha256(model_json({'scope':scope,'members':members,'materials':[materials[e]['digest'] for e in sorted(materials)]}).encode()).hexdigest()
    db.execute('INSERT INTO memory_semantic_batches VALUES(?,?,?,?,?,?,?,NULL)',(checkpoint_id,scope,signature,model_json(members),cutoff,'committed',time.time()))
    import semantic_media
    media=semantic_media.retire_batch(db,[image for m in materials.values() for image in m.get('images',[])],checkpoint_id,members,scope=scope)
    return media


def purge(db,checkpoint_id):
    """After the replacement Journal commits, release exact batch rows."""
    row=db.execute("SELECT scope,members FROM memory_semantic_batches WHERE checkpoint_id=? AND state='retired'",(checkpoint_id,)).fetchone()
    if not row:return False
    scope,members=row[0],set(json.loads(row[1]))
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    contact_columns={r[1] for r in db.execute('PRAGMA table_info(contacts)')} if 'contacts' in tables else set()
    events=set()
    targets=('memory_pending','memory_round_material','memory_pending_rounds','memory_hold_rounds','memory_summary_coverage','memory_summary_unresolved','memory_media_retirement_choices','memory_rounds','memory_message_sources')
    for table in targets:
        if table in tables:
            events.update(str(r[0]) for r in db.execute('SELECT event FROM '+table+' WHERE scope=?',(scope,)) if event_key(scope,r[0]) in members)
    # These exact source copies may outlive the hot memory rows (including a
    # retry after an older version purged them). Recover only hashed members.
    for table,column in (('relation_evidence','event'),('relation_events','event'),('companion_seen','event'),
                         ('companion_signals','event'),('pipeline_quote_sources','message'),('fishbot_shared_inputs','event')):
        if table not in tables:continue
        query='SELECT scope,'+column+' FROM '+table
        for channel,identifier in db.execute(query+(' WHERE scope=?' if scope!='bot_shared' else ''),(scope,) if scope!='bot_shared' else ()):
            event=model_json([channel,str(identifier)]) if scope=='bot_shared' else str(identifier)
            if event_key(scope,event) in members:events.add(event)
    if 'companion_signals' in tables:
        for channel,quote in db.execute("SELECT scope,quote FROM companion_signals WHERE kind='dialogue_feedback'"+(' AND scope=?' if scope!='bot_shared' else ''),(scope,) if scope!='bot_shared' else ()):
            try:feedback=json.loads(quote)
            except (ValueError,TypeError):continue
            if not isinstance(feedback,dict) or not feedback.get('reaction_event'):continue
            identifier=str(feedback['reaction_event'])
            event=model_json([channel,identifier]) if scope=='bot_shared' else identifier
            if event_key(scope,event) in members:events.add(event)
    for column in ('last_user_event','last_bot_event'):
        if column not in contact_columns:continue
        for channel,identifier in db.execute('SELECT scope,'+column+' FROM contacts WHERE '+column+' IS NOT NULL'+(' AND scope=?' if scope!='bot_shared' else ''),(scope,) if scope!='bot_shared' else ()):
            event=model_json([channel,str(identifier)]) if scope=='bot_shared' else str(identifier)
            if event_key(scope,event) in members:events.add(event)
    # Snapshot uniqueness before any pending rows leave; iteration order must
    # not turn an ambiguous shared timestamp into apparently unique evidence.
    legacy_contacts=set()
    if 'last_user_event' in contact_columns and 'memory_pending' in tables:
        legacy_contacts=set(db.execute('''SELECT c.scope,p.event FROM contacts c JOIN memory_pending p
          ON p.scope=c.scope AND p.subject=c.subject AND p.at=c.last_user
          WHERE c.last_user_event IS NULL AND
          (SELECT count(*) FROM memory_pending m WHERE m.scope=c.scope AND m.subject=c.subject AND m.at=c.last_user)=1'''))
    for event in events:
        channel,identifier=origin(scope,event)
        if {'last_user_event','last_bot_event'}<=contact_columns:
            # Legacy incoming uses the same observation timestamp and author
            # as memory_pending. Never infer identity from matching text.
            if (channel,identifier) in legacy_contacts:
                db.execute('UPDATE contacts SET last_user_event=? WHERE scope=? AND last_user_event IS NULL',(identifier,channel))
            db.execute("UPDATE contacts SET last_text='',last_user_event=NULL WHERE scope=? AND last_user_event=?",(channel,identifier))
            db.execute("UPDATE contacts SET last_output='',last_bot_event=NULL WHERE scope=? AND last_bot_event=?",(channel,identifier))
        for table in targets:
            if table in tables:db.execute('DELETE FROM '+table+' WHERE scope=? AND event=?',(scope,event))
        if 'memory_message_sources' in tables:db.execute('DELETE FROM memory_message_sources WHERE scope=? AND event=?',(scope,event))
        channel,identifier=origin(scope,event)
        # Exact hot copies in their original conversation, never a TTL sweep.
        for table in ('dialogue_feedback','social_samples','memory_pending','relation_evidence','relation_events','companion_seen'):
            if table in tables:db.execute('DELETE FROM '+table+' WHERE scope=? AND event=?',(channel,identifier))
        if 'pipeline_quote_sources' in tables:
            db.execute('DELETE FROM pipeline_quote_sources WHERE scope=? AND message=?',(channel,identifier))
        active=bool('pipeline_work_inputs' in tables and db.execute("SELECT 1 FROM pipeline_work_inputs WHERE scope=? AND event=? AND status='owned'",(channel,identifier)).fetchone())
        active=active or bool('pipeline_scope_work' in tables and db.execute('SELECT 1 FROM pipeline_scope_work WHERE scope=? AND event=?',(channel,identifier)).fetchone())
        if 'fishbot_shared_inputs' in tables and not active:
            db.execute('DELETE FROM fishbot_shared_inputs WHERE scope=? AND event=?',(channel,identifier))
        if 'companion_signals' in tables:
            # Signals are quote evidence, not the derived companion state.
            db.execute('DELETE FROM companion_signals WHERE scope=? AND event=?',(channel,identifier))
            semantic='semantic:'+event_key(channel,identifier)
            for signal_event,quote in db.execute("SELECT event,quote FROM companion_signals WHERE scope=? AND kind='dialogue_feedback'",(channel,)).fetchall():
                try:feedback=json.loads(quote)
                except (ValueError,TypeError):continue
                if isinstance(feedback,dict) and str(feedback.get('reaction_event',''))==identifier:
                    feedback['reaction_event']=semantic
                    feedback.pop('quote',None)
                    db.execute("UPDATE companion_signals SET quote=? WHERE scope=? AND event=? AND kind='dialogue_feedback'",(model_json(feedback),channel,signal_event))
        if 'memory_message_sources' in tables:db.execute('DELETE FROM memory_message_sources WHERE scope=? AND event=?',(channel,identifier))
        if 'pipeline_work_inputs' in tables:
            # Minimal identity/status/receipt fencing remains a system ledger;
            # it is not model memory and no longer contains the raw request.
            db.execute("UPDATE pipeline_work_inputs SET payload=NULL WHERE scope=? AND event=? AND status<>'owned'",(channel,identifier))
        if 'facts' in tables:
            db.execute('UPDATE facts SET event=? WHERE scope=? AND event=?',('semantic:'+event_key(channel,identifier),channel,identifier))
        if 'memory_agreements' in tables:
            db.execute('UPDATE memory_agreements SET source_event=? WHERE source_scope=? AND source_event=?',('semantic:'+event_key(channel,identifier),channel,identifier))
    # Original ID lists are no longer model memory or checkpoint dependencies.
    db.execute("UPDATE memory_summaries SET sources='[]' WHERE scope=?",(scope,))
    db.execute("UPDATE memory_core_notes SET sources='[]'")
    # Recovery archives obey the same successful exact-batch retirement.
    # They may keep uncompressed evidence and System, never a retired copy.
    import journal_storage
    journal_storage.retire_archived_members(db,checkpoint_id)
    return True
