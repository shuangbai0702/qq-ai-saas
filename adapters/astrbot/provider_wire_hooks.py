"""Provider wire hooks: Journal projection, SystemContract pinning, and unified chat/stream lifecycle."""
import asyncio,copy,hashlib,inspect,time
from contextlib import aclosing
from contextvars import ContextVar
from pathlib import Path
import autonomy
import bot_pipeline
import message_provenance
from context_protocol import EVENT_CONTEXT_CURRENT,READONLY_CURRENT,model_json
from output_sanitize import sanitize_new_response,suppress_separate_reasoning


def install_provider_hooks(ProviderOpenAIOfficial,current_message_event,settings,stores,metrics,*,setting,current,delivery_current,provider_for,inject):
    prepare=ProviderOpenAIOfficial._prepare_chat_payload
    model_journal_pending=ContextVar('fishbot_model_journal_pending',default=None)

    def settle_journal(event,success):
        if not event:return
        pending=model_journal_pending.get()
        if not pending:return
        model_journal_pending.set(None)
        owned,key,journal,prepared,epoch=pending
        from readonly_journal import visible_summary_ids
        from model_cache import submission_stage,record_diagnostic
        stage=None if success else submission_stage()
        submitted_unknown=bool(stage and stage!='request_not_sent')
        retained=False;settled_state='abort'
        if event.get_extra('_hosted_internal_work') and not event.get_extra('_hosted_main_activation'):
            valid=(current(event) and delivery_current(event) and stores.get(event.get_platform_id()) is owned
                   and owned.model_journals.get(key) is journal and owned.reflection.epoch()==epoch)
            material_current=owned.reflection.context_dependency(visible_summary_ids(prepared.state))==prepared.retirement
            if success and valid:retained=journal.commit(prepared);settled_state='commit'
            elif valid and material_current and submitted_unknown:retained=journal.retain_submitted(prepared);settled_state='submitted_unknown_retained'
            elif valid and material_current:journal.abort_candidate(prepared)
            else:journal.retire_committed(prepared,'authority_changed');settled_state='retire'
        else:
            same_owner=(stores.get(event.get_platform_id()) is owned and owned.model_journals.get(key) is journal)
            latest=journal.serial==prepared.serial
            valid=False;material_current=True
            if same_owner:
                valid=current(event) and owned.reflection.epoch()==epoch
                material_current=owned.reflection.context_dependency(visible_summary_ids(prepared.state),key)==prepared.validation_dependency
            elif not owned.reflection.closed:
                if submitted_unknown:retained=journal.retain_submitted(prepared);settled_state='submitted_unknown_retained'
                else:journal.abort_candidate(prepared)
            if same_owner and latest:
                if owned.reflection.epoch()!=epoch:
                    journal.retire_committed(prepared,'memory_state_changed');settled_state='retire'
                elif submitted_unknown:
                    retained=journal.retain_submitted(prepared);settled_state='submitted_unknown_retained'
                elif success and valid:
                    retained=journal.commit(prepared);settled_state='commit'
                elif not material_current:
                    journal.abort_candidate(prepared)
                else:journal.abort_candidate(prepared)
        record_diagnostic(event.get_extra('_hosted_lease'),{'kind':'context_result','phase':'inner_execution' if event.get_extra('_hosted_internal_work') else 'foreground',
            'status':settled_state if retained else ('not_retained' if success else settled_state),
            'reason':None if retained and success else 'request_submitted_unknown' if retained else 'candidate_or_size_boundary' if success and valid and material_current else 'request_failed' if valid and material_current else 'summary_changed_during_request' if valid else 'authority_changed',
            'resetDetail':prepared.detail,'diagnosticDetail':(getattr(journal,'recovery_error',None) or prepared.retirement_deferred or stage or None),**(prepared.usage or {})})

    async def prepare_chat_payload(self,*args,**kwargs):
        image_owner=current_message_event.get()
        if image_owner and image_owner.get_extra('_hosted_lease') is not None:
            from semantic_media import collect_protocol_originals
            if not delivery_current(image_owner):raise asyncio.CancelledError('stale_model_image_request')
            media_store=stores.get(image_owner.get_platform_id())
            await collect_protocol_originals(image_owner,lambda:delivery_current(image_owner),
                                             media_store.db if media_store else None,shared=bool(media_store and media_store.shared_context))
        from model_cache import auxiliary_model,is_foreground_event,routing_key,observe_payload
        model=auxiliary_model(current_message_event.get(),self)
        if model is not None:
            bound=inspect.signature(prepare).bind_partial(self,*args,**kwargs)
            bound.arguments['model']=model
            args=bound.args[1:];kwargs=bound.kwargs
        payload,query=await prepare(self,*args,**kwargs)
        journal_reason=None
        from context_transport import model_messages
        projected=model_messages(payload.get('messages'))
        from model_images import constrain_messages,request_memo
        image_event=current_message_event.get()
        from summary_material import source,digest,snapshots
        owned=stores.get(image_event.get_platform_id()) if image_event else None
        work=image_event.get_extra('_hosted_pipeline') if image_event else None
        foreground=is_foreground_event(image_event)
        if foreground and owned is None:
            from context_protocol import ContextRecoveryUnavailable
            raise ContextRecoveryUnavailable('A foreground request has no bot-owned append-only journal.')
        memory_capture=bool(foreground and not owned.shared_context and (setting(image_event)['config'].get('behavior') or {}).get('memory',True))
        signatures={};native_view=None
        if memory_capture:
            native_view=projected
            signatures={str(origin['event_id']):digest(m) for m in projected if (origin:=source(m)) and origin['scope']==work.scope}
        if foreground and owned and owned.shared_context:
            import shared_context
            projected=shared_context.current_view(projected,work.scope,work.event_id,service=work.source not in ('message','deferred_group'))
        image_memo=request_memo(image_event)
        retained_media=None
        if foreground and owned and not owned.shared_context:
            existing=owned.model_journals.get(work.scope)
            if existing is not None and existing.last is not None:retained_media=existing.last.base
            elif owned.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fishbot_readonly_journals'").fetchone():
                from journal_storage import read_snapshot
                saved=read_snapshot(owned.db,model_json(work.scope))
                retained_media=saved.get('base',[]) if saved else None
        if owned and (memory_capture or foreground and owned.shared_context):
            if not delivery_current(image_event):raise asyncio.CancelledError('stale_model_image_request')
            if owned.db.in_transaction:raise ValueError('media_admission_requires_idle_transaction')
            from semantic_media import register_event
            staged_originals=list(image_event.get_extra('_hosted_media_originals') or [])
            with owned.db:register_event(owned.db,image_event,snapshots(projected,'bot_shared' if owned.shared_context else work.scope))
            image_event.set_extra('_hosted_media_originals',staged_originals)
        projected=await constrain_messages(projected,image_memo,retained_media)
        unprojected_for_journal=None
        if memory_capture and native_view is not None and len(native_view)>len(projected):
            unprojected_for_journal=await constrain_messages(native_view,image_memo,retained_media)
        fence=image_event.get_extra('_hosted_context_fence') if foreground else None
        if fence:
            owner,context_epoch,ids,scope,dependency=fence
            if (owner is not owned or owned.reflection.epoch()!=context_epoch or
                owned.reflection.context_dependency(ids,scope)!=dependency):
                from readonly_journal import ContextReconstructionDenied
                raise ContextReconstructionDenied('Memory dependency changed while preparing this request; stale context was not sent.')
        if image_event and image_event.get_extra('_hosted_lease') is not None and not delivery_current(image_event):
            raise asyncio.CancelledError('stale_model_image_request')
        if memory_capture:
            captured=snapshots(projected,work.scope,signatures)
            if owned and not (foreground and owned.shared_context):
                from semantic_media import register_event
                register_event(owned.db,image_event,captured)
                owned.db.commit()
                image_event.set_extra('_hosted_media_originals',[])
            image_event.set_extra('_hosted_summary_materials',captured)
            materials_acc=getattr(work,'summary_materials',None)
            if isinstance(materials_acc,list):materials_acc.extend(captured)
        internal_identity=image_event.get_extra('_hosted_internal_work') if image_event else None
        main_activation=bool(image_event and image_event.get_extra('_hosted_main_activation'))
        internal=bool(not main_activation and owned and work and work.source=='autonomous' and isinstance(internal_identity,dict) and internal_identity.get('id')==work.event_id)
        shared_mode=bool(foreground and owned)
        from web_cognition import read as read_cognition_edit
        cognition_edit=read_cognition_edit(owned.db) if shared_mode else None
        if shared_mode and setting(image_event).get('characterCardsEdit'):
            cognition_edit={**(cognition_edit or {}),'character_cards':{'cards':setting(image_event)['config'].get('characterCards',[]),'proof':setting(image_event)['characterCardsEdit'],'persona':setting(image_event)['config'].get('systemPrompt',''),'persona_proof':setting(image_event).get('personaEdit')}}
        if shared_mode:
            import shared_context
            image_event.set_extra('_hosted_journal_scope',shared_context.main_journal_scope(owned.db))
        if (foreground or internal) and isinstance(projected,list):
            from readonly_journal import ReadonlyJournal,visible_summary_ids,ContextCapacityExceeded,capacity_evidence
            from context_protocol import request_identity,system_head,ContextRecoveryUnavailable
            import shared_context
            key=shared_context.main_journal_scope(owned.db) if foreground or main_activation else ('autonomous',work.event_id)
            journal=owned.model_journals.pop(key,None)
            if journal is None:journal=ReadonlyJournal(owned.db,key if isinstance(key,str) else None)
            owned.model_journals[key]=journal
            if shared_mode:
                normalized={'messages':copy.deepcopy(projected)}
                self._sanitize_assistant_messages(normalized)
                projected=normalized['messages']
                current_native=shared_context.native_tail(projected,work.scope,work.event_id)[0] if work.source in ('message','deferred_group') else None
                already_current=bool(journal.last and any((o:=source(m)) and o['scope']==work.scope and str(o['event_id'])==str(work.event_id) for m in journal.last.base))
                additions=shared_context.queued(owned.db,setting(image_event)['config'],journal,owned.shared_group_limit,current=(work.scope,str(work.event_id),current_native) if current_native else None) if owned.shared_context else ([current_native] if current_native and not already_current else [])
                additions=await constrain_messages(additions,image_memo)
                projected=shared_context.refresh_deliveries(projected,owned.db,journal)
                if journal.last and setting(image_event).get('systemChangePermitId') and setting(image_event).get('personaEdit'):
                    from web_base import persona_view
                    projected=persona_view(journal.last.base,projected,setting(image_event)['personaEdit'],setting(image_event)['config'].get('systemPrompt',''))
                    if setting(image_event).get('characterCardsEdit'):
                        from character_cards import layout
                        projected=layout(projected,setting(image_event)['config'].get('characterCards',[]),origins=setting(image_event)['characterCardsEdit']['origins'],persona=setting(image_event)['config'].get('systemPrompt',''))
                system_adoption=bool(setting(image_event).get('personaEdit')) and owned.system_contract.transition_available(
                    projected,setting(image_event).get('systemChangePermitId'),request_identity(),scope=key)
                projected,tools_seen=shared_context.compose(journal,projected,work.scope,work.event_id,additions,image_event.get_extra('_hosted_shared_tools'),service=work.source not in ('message','deferred_group'),self_id=image_event.get_self_id(),input_digest=image_event.get_extra('_hosted_shared_input_digest'),system_transition_authorized=system_adoption,web_base_authorized=bool(setting(image_event).get('personaEdit')),cognition_edit=cognition_edit,regeneration=bool(image_event.get_extra('_hosted_output_regeneration',False)))
                image_event.set_extra('_hosted_shared_tools',tools_seen)
                native_shared=[m for m in projected if not (m.get('role')=='user' and isinstance(m.get('content'),str) and m['content'].startswith((READONLY_CURRENT+'\n',EVENT_CONTEXT_CURRENT+'\n')))]
                new_tools=shared_context.native_tail(projected,work.scope,work.event_id)[1:] if work.source in ('message','deferred_group') else []
                for index,message in enumerate(native_shared):
                    if index>=len(journal.last.base if journal.last else []) and message.get('role') in ('assistant','tool') and message in new_tools:
                        message_provenance.bind_model_output(owned.db,key,work.scope,work.event_id,work.subject,native_shared,index)
                signatures={shared_context.event_key(o):digest(m) for m in native_shared if (o:=source(m))}
                captured=snapshots(native_shared,key,signatures,db=owned.db)
                from semantic_media import register_event
                register_event(owned.db,image_event,captured)
                image_event.set_extra('_hosted_media_originals',[])
                work.summary_materials.extend(captured)
                for item in captured:
                    original=source({'role':'user','content':item['text']})
                    if original:
                        at=original.get('at')
                        if type(at) not in (int,float) or not 0<at<float('inf'):
                            owned.reflection.checkpoint_issue(key,0,'input_timestamp_missing',{'event':item['event'],'source_scope':original['scope']})
                            continue
                        owned.db.execute('INSERT OR IGNORE INTO memory_pending VALUES(?,?,?,?,?,?)',(key,item['subject'],item['event'],item['text'],'',float(at)))
                        message_provenance.put(owned.db,key,item['event'],'incoming',{'sender_id':item['subject'],'source_scope':original['scope']})
                owned.db.commit()
                unprojected_for_journal=projected
                native_view=None
            chat_keys=[k for k in owned.model_journals if isinstance(k,str)]
            for old_key in chat_keys[:-8]:owned.model_journals.pop(old_key)
            epoch=owned.reflection.epoch()
            dependency=owned.reflection.context_dependency(visible_summary_ids(projected),key if foreground else None)
            stamp=(id(self),setting(image_event).get('generation'),setting(image_event).get('revision'),epoch,
                   hashlib.sha256(model_json({k:payload.get(k) for k in ('model','tools','tool_choice','reasoning_effort')}).encode()).hexdigest())
            group_turn=(work.scope,str(work.event_id),str(work.subject)) if foreground and image_event.get_extra('_hosted_group_round_history') and work.scope.startswith('group:') else None
            lease=setting(image_event)
            try:
                from memory_scope import retained_context_visible
                system_transition_authorized=False
                if foreground and journal.last is not None:
                    system_transition_authorized=bool(lease.get('personaEdit') or lease.get('characterCardsEdit')) and owned.system_contract.transition_available(projected,lease.get('systemChangePermitId'),request_identity(),scope=key)
                    if not system_transition_authorized:
                        from web_cognition import project as project_base
                        fixed=project_base(journal.last,cognition_edit)
                        fixed_system=fixed.base[:system_head(fixed.base)]
                        projected=fixed_system+projected[system_head(projected):]
                        if isinstance(unprojected_for_journal,list):
                            unprojected_for_journal=fixed_system+unprojected_for_journal[system_head(unprojected_for_journal):]
                if image_event.get_extra('_hosted_output_regeneration',False):
                    from public_output import REGENERATE
                    correction=image_event.get_extra('_hosted_output_correction') or REGENERATE
                    projected=[*projected,{'role':'user','content':correction}]
                    if isinstance(unprojected_for_journal,list):unprojected_for_journal=[*unprojected_for_journal,{'role':'user','content':correction}]
                prepared=journal.prepare(projected,stamp,group_turn=group_turn,
                                         reconstruction_authority=owned.reflection,reconstruction_scope=key,
                                         retirement=owned.reflection.reconstruction_stamp(key) if foreground else dependency,
                                         strict=foreground,compact_policy=foreground,guarded=foreground,
                                         retained_validator=(lambda wire:retained_context_visible(owned.db,wire,work.scope,str((lease['config'].get('behavior') or {}).get('ownerUin') or ''))) if foreground else None,
                                         unprojected=unprojected_for_journal,
                                         system_transition_authorized=system_transition_authorized,web_base_authorized=bool(lease.get('personaEdit')),cognition_edit=cognition_edit)
                if prepared:prepared.validation_dependency=dependency
                if prepared and main_activation:
                    from activity_context import tail_service
                    tail_service(prepared,journal,work)
                if prepared:
                    metrics.setdefault(image_event.get_platform_id(),{})['contextCapacity']={**capacity_evidence(prepared.usage),'candidate':True}
            except BaseException as error:
                if foreground and journal.last is not None and isinstance(error,ContextCapacityExceeded):
                    owned.pipeline.wait_for_compression(work,key)
                    owned.reflection.record_capacity_wait(key,error.capacity)
                if getattr(error,'capacity',None):
                    metrics.setdefault(image_event.get_platform_id(),{})['contextCapacity']={**error.capacity,'candidateRejected':True}
                if foreground and journal.last is None:owned.model_journals.pop(key,None)
                from model_cache import record_diagnostic
                evidence=model_json(owned.reflection and journal.refusal_evidence() or {})[:1024]
                record_diagnostic(image_event.get_extra('_hosted_lease'),{'kind':'context','phase':'foreground',
                    'workId':work.event_id,'resetReason':'journal_prepare_refused','resetDetail':type(error).__name__,
                    'diagnosticDetail':evidence})
                raise
            if prepared:
                journal_reason=prepared.reason or 'append'
                projected=prepared.wire
                if foreground:
                    seen={}
                    for m in prepared.base:
                        origin=source(m)
                        if origin:seen.setdefault(origin['scope'],set()).add(str(origin['event_id']))
                    image_event.set_extra('_hosted_model_seen_events',seen)
                model_journal_pending.set((owned,key,journal,prepared,epoch))
                if foreground:
                    from model_cache import expect_append_only_messages
                    expect_append_only_messages(projected)
                def stage_activation():
                    action=image_event.get_extra('_hosted_autonomous_action') or {}
                    if main_activation and action.get('autonomousMoment') is True and not action.get('continuation'):
                        from activation_rounds import record as record_activation
                        from activity_context import event_key as activation_event
                        record_activation(owned.db,key,activation_event(work,key),work.event_id,source_scope=work.scope,subject=str(image_event.get_self_id()),at=work.source_at)
                if main_activation:prepared.main_conversation=True
                if not journal.stage_candidate(prepared,on_stage=stage_activation):
                    from model_cache import record_diagnostic
                    record_diagnostic(image_event.get_extra('_hosted_lease'),{'kind':'context','phase':'inner_execution' if internal else 'foreground',
                        'workId':work.event_id,'resetReason':'candidate_stage_failed','resetDetail':journal.recovery_error or 'stage_refused'})
                    if prepared.reconstruction_permit and not prepared.reconstruction_consumed:
                        try:owned.reflection.settle_context_reconstruction(prepared.reconstruction_permit,False)
                        except Exception:pass
                    raise ContextRecoveryUnavailable('The exact request journal could not be persisted; no model request was submitted.')
                if main_activation:
                    image_event.set_extra('_hosted_main_request_staged',True)
                    if (image_event.get_extra('_hosted_autonomous_action') or {}).get('autonomousMoment'):
                        image_event.set_extra('_hosted_activation_admitted',True)
                if foreground and shared_mode:
                    owned.pipeline.request_inputs(work,prepared.base)
                from model_cache import record_diagnostic
                record_diagnostic(image_event.get_extra('_hosted_lease'),{'kind':'context','phase':'inner_execution' if internal else 'foreground',
                    'workId':work.event_id,'resetReason':prepared.reason or None,'resetDetail':prepared.detail or None,
                    'diagnosticDetail':prepared.retirement_deferred or None,
                    'baseChars':prepared.base_chars,
                    'layoutVersion':1,'journalScopeKind':'foreground' if foreground else 'inner_work',
                    'committedPrefixMessages':len(prepared.base),
                    'committedPrefixSha':hashlib.sha256(model_json(prepared.base).encode()).hexdigest()[:16],
                    'permitKind':'context_reconstruction' if prepared.reconstruction_permit else None,
                    'candidateAppendChars':(prepared.usage or {}).get('wireChars',0)-prepared.base_chars,
                    **prepared.usage})
        if isinstance(projected,list):payload['messages']=projected;query=projected
        cache_key=routing_key(image_event,self,payload.get('model'))
        if cache_key and 'prompt_cache_key' not in payload:payload['prompt_cache_key']=cache_key
        limit=autonomy.decision_token_limit.get()
        if limit is not None:payload['max_tokens']=limit
        if foreground and work:work.request_prepared=True
        observe_payload(payload,journal_reason)
        return payload,query

    ProviderOpenAIOfficial._prepare_chat_payload=prepare_chat_payload
    provider_overrides=ProviderOpenAIOfficial._apply_provider_specific_request_overrides

    def decision_output_override(self,payload,extra_body):
        provider_overrides(self,payload,extra_body)
        from model_cache import auxiliary_model
        model=auxiliary_model(current_message_event.get(),self)
        if model is not None:
            payload['model']=model
            extra_body.pop('model',None)
        limit=autonomy.decision_token_limit.get()
        if limit is not None:
            completion_limit='max_completion_tokens' in extra_body or 'max_completion_tokens' in payload
            for key in ('max_tokens','max_completion_tokens'):
                payload.pop(key,None);extra_body.pop(key,None)
            extra_body['max_completion_tokens' if completion_limit else 'max_tokens']=limit

    ProviderOpenAIOfficial._apply_provider_specific_request_overrides=decision_output_override
    chat=ProviderOpenAIOfficial.text_chat;stream=ProviderOpenAIOfficial.text_chat_stream

    async def writer_enter(st,event,foreground):
        if st is None or not event:return None
        if foreground:
            from shared_context import main_journal_scope
            main_journal_scope(st.db)
        if not (foreground or (st.shared_context and event.get_extra('_hosted_internal_work'))):return None
        await st.model_writer.acquire()
        event.set_extra('_hosted_model_writer',st)
        try:
            from voice_inputs import ready
            await ready(st,lambda:current(event) and delivery_current(event))
        except BaseException:
            st.model_writer.release();raise
        if not current(event) or not delivery_current(event):
            st.model_writer.release();raise asyncio.CancelledError('stale_shared_context_writer')
        if st.shared_context and foreground:
            from shared_context import JOURNAL_SCOPE
            event.set_extra('_hosted_journal_scope',JOURNAL_SCOPE)
        return st.model_writer

    def record_shared_completion(provider,st,event,response):
        if not event:return
        event.set_extra('_hosted_model_tools_pending',bool(getattr(response,'tools_call_name',None)))
        if not st:return
        main_activation=event.get_extra('_hosted_main_activation')
        if not st.shared_context:return
        if event.get_extra('_hosted_internal_work') and not main_activation:return
        from shared_context import main_journal_scope,settle_inputs,event_key
        logical=main_journal_scope(st.db)
        journal=st.model_journals.get(logical)
        if journal and journal.last:
            work=event.get_extra('_hosted_pipeline')
            if not getattr(response,'tools_call_name',None) and response.completion_text:
                message={'role':'assistant','content':response.completion_text or ''}
                # Keep assistant history wire-compatible with reasoning providers
                # (e.g. DeepSeek V4 / MiMo) that require reasoning_content on Turn 2+.
                convert=getattr(provider,'_finally_convert_payload',None)
                if callable(convert):
                    try:
                        model_name=(getattr(provider,'model_name',None) or (getattr(provider,'provider_config',None) or {}).get('model') or '')
                        converted={'model':model_name,'messages':[copy.deepcopy(message)]}
                        convert(converted)
                        if isinstance(converted.get('messages'),list) and converted['messages'] and isinstance(converted['messages'][0],dict):
                            message=converted['messages'][0]
                    except Exception:pass
                journal.append_completion(message,model_origin=(work.scope,work.event_id,work.subject))
            if not main_activation:st.pipeline.seen_inputs(work,journal.last.base)
            from summary_material import snapshots,remember
            current_id=event_key({'scope':work.scope,'event_id':work.event_id})
            materials=snapshots(journal.last.base,logical,db=st.db)
            for item in materials:
                actual=st.db.execute('SELECT answer FROM memory_rounds WHERE scope=? AND event=?',(logical,item['event'])).fetchone()
                item['reply']=actual[0] if actual else ''
                if item['event']==current_id and getattr(response,'tools_call_name',None):item['complete']=False
            remember(st.db,logical,materials)
            work.summary_materials.extend(materials)
            if st.shared_context:settle_inputs(st.db,journal)

    def resolve_provider_attempt(provider):
        from model_cache import begin_attempt,current_lease,is_foreground_event
        event=current_message_event.get();lease=current_lease(event,provider)
        system_store=stores.get(event.get_platform_id()) if event else None
        if system_store is None and isinstance(lease,dict) and lease.get('root'):
            root=str(Path(lease['root']).resolve())
            system_store=next((candidate for candidate in stores.values() if str(candidate.root.resolve())==root),None)
        foreground=is_foreground_event(event)
        attempt_token=begin_attempt(event,provider,system_contract=getattr(system_store,'system_contract',None) if foreground else None,
                                    system_permit_id=lease.get('systemChangePermitId') if foreground and isinstance(lease,dict) else None)
        return event,lease,system_store,foreground,attempt_token

    async def prepare_foreground_turn(event,system_store,foreground):
        writer=await writer_enter(system_store,event,foreground)
        config_token=None;pinned_lease=None
        try:
            if foreground:
                from web_base import pin_request
                from model_cache import pin_request_configuration
                pinned_lease=settings.get(event.get_platform_id()) if event else None
                if pinned_lease is None:raise asyncio.CancelledError('stale_member_lease')
                config_token=pin_request(event,pinned_lease)
                pin_request_configuration(pinned_lease)
            if foreground and system_store:
                import periodic_guidance
                await periodic_guidance.prepare_goals(system_store,event,setting(event),lambda:current(event) and delivery_current(event))
            return writer,config_token,pinned_lease
        except BaseException:
            from web_base import release_request
            release_request(config_token)
            if writer:writer.release()
            raise

    def record_provider_failure(error,*,settle=True):
        from model_cache import finish_attempt,record_error_site,AppendOnlyWireViolation
        from readonly_journal import ContextCapacityExceeded,ContextPrefixViolation,ContextReconstructionDenied,ContextRecoveryUnavailable
        from context_protocol import SystemContractViolation
        finish_attempt('failed',reason=type(error).__name__)
        record_error_site(error)
        e=current_message_event.get()
        if settle:settle_journal(e,False)
        if e and current(e) and e.get_platform_id() in metrics:
            metric=metrics[e.get_platform_id()];metric['failures']=metric.get('failures',0)+1
            metric['lastFailure']={'time':time.time(),'type':type(error).__name__}
            e.set_extra('_hosted_model_error',True)
            e.set_extra('_hosted_model_error_type',type(error).__name__)
            if isinstance(error,(ContextCapacityExceeded,ContextPrefixViolation,ContextReconstructionDenied,ContextRecoveryUnavailable,SystemContractViolation,AppendOnlyWireViolation)):
                e.set_extra('_hosted_context_guard_error',type(error).__name__)
                bot_pipeline.stop(e,'context_integrity:'+type(error).__name__,'error')

    def record_foreground_metrics(e,response,started,system_prompt):
        if e and current(e) and e.get_platform_id() in metrics:
            m=metrics[e.get_platform_id()];m['successes']=m.get('successes',0)+1
            raw=response.raw_completion
            m['lastCall']={'time':time.time(),'model':getattr(raw,'model',None),'responseId':getattr(raw,'id',None),'latencyMs':round((time.time()-started)*1000),'promptHash':hashlib.sha256(str(system_prompt or '').encode()).hexdigest(),'usageReportedTotal':getattr(getattr(raw,'usage',None),'total_tokens',None)}
            from model_cache import usage_evidence
            m['lastCall'].update(usage_evidence(response))
            if not e.get_extra('_hosted_internal_work'):
                from astrbot.core import logger
                logger.info('FISHBOT_MODEL_USAGE '+model_json({'botId':e.get_platform_id(),'phase':'foreground','eventId':str(getattr(e.message_obj,'message_id','')),'scope':bot_pipeline.delivery_scope(e),**m['lastCall']}))

    async def text_chat(self,*args,**kwargs):
        started=time.time()
        journal_token=model_journal_pending.set(None)
        from model_cache import finish_attempt,reset_attempt,record_diagnostic,record_error_site
        event,lease,system_store,foreground,attempt_token=resolve_provider_attempt(self)
        writer=None;config_token=None
        try:
            writer,config_token,pinned=await prepare_foreground_turn(event,system_store,foreground)
            if pinned is not None:lease=pinned
            b=inject(chat,self,args,kwargs)
            response=await chat(*b.args,**b.kwargs)
            e=current_message_event.get()
            if foreground:suppress_separate_reasoning(response,e)
            sanitize_new_response(response,e)
            finish_attempt('completed',response)
            response_finished=time.monotonic()
            settle_journal(e,True)
            record_shared_completion(self,system_store,e,response)
            record_foreground_metrics(e,response,started,b.arguments.get('system_prompt',''))
            if e:
                record_diagnostic(lease,{'kind':'provider_handoff','phase':'foreground' if foreground else 'inner_execution',
                    'workId':getattr(e.get_extra('_hosted_pipeline'),'event_id',None),
                    'durationMs':round((time.monotonic()-response_finished)*1000)})
                e.set_extra('_hosted_provider_return_at',time.monotonic())
            return response
        except Exception as error:
            record_provider_failure(error,settle=True)
            raise
        except asyncio.CancelledError as error:
            finish_attempt('canceled',reason='cancelled')
            record_error_site(error)
            settle_journal(current_message_event.get(),False)
            raise
        finally:
            if writer:writer.release()
            model_journal_pending.reset(journal_token)
            reset_attempt(attempt_token)
            from web_base import release_request
            release_request(config_token)

    async def text_stream(self,*args,**kwargs):
        journal_token=model_journal_pending.set(None)
        from model_cache import finish_attempt,reset_attempt,first_response
        event,lease,system_store,foreground,attempt_token=resolve_provider_attempt(self)
        last_response=None;writer=None;completion_done=False;config_token=None
        try:
            writer,config_token,_=await prepare_foreground_turn(event,system_store,foreground)
            b=inject(stream,self,args,kwargs)
            async with aclosing(stream(*b.args,**b.kwargs)) as responses:
                async for response in responses:
                    first_response()
                    if getattr(response,'is_chunk',False):continue
                    last_response=response
                    event=current_message_event.get()
                    if foreground:suppress_separate_reasoning(response,event)
                    sanitize_new_response(response,event)
                    if event and event.get_extra('_hosted_lease') is not None and not delivery_current(event):return
                    if system_store and system_store.shared_context and foreground and not completion_done:
                        finish_attempt('completed',response)
                        settle_journal(event,True)
                        record_shared_completion(self,system_store,event,response)
                        completion_done=True
                        if writer:writer.release();writer=None
                    yield response
            if last_response is None:raise RuntimeError('incomplete_model_stream')
            if not completion_done:
                finish_attempt('completed',last_response)
                settle_journal(current_message_event.get(),True)
                record_shared_completion(self,system_store,current_message_event.get(),last_response)
        except Exception as error:
            record_provider_failure(error,settle=False)
            raise
        except asyncio.CancelledError as error:
            from model_cache import record_error_site
            finish_attempt('canceled',reason='cancelled')
            record_error_site(error)
            settle_journal(current_message_event.get(),False)
            raise
        finally:
            finish_attempt('canceled',reason='stream_not_exhausted')
            settle_journal(current_message_event.get(),False)
            if writer:writer.release()
            model_journal_pending.reset(journal_token)
            reset_attempt(attempt_token)
            from web_base import release_request
            release_request(config_token)

    async def no_retry(self,error,*args,**kwargs):raise error
    ProviderOpenAIOfficial._handle_api_error=no_retry
    from model_request_capture import bind_provider
    ProviderOpenAIOfficial.text_chat=bind_provider(text_chat,settings,provider_for)
    ProviderOpenAIOfficial.text_chat_stream=bind_provider(text_stream,settings,provider_for)
