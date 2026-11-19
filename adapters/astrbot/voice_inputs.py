"""QQ transcription derives from the original durable input, never a new event."""
import asyncio
import copy
import json
import time
import uuid

SOFT_TIMEOUT = 20
HARD_TIMEOUT = 60
RESULT_RETRY_DELAY = .5


def has_voice(raw):
    segments=raw.get('message')
    return isinstance(segments,list) and any(isinstance(s,dict) and s.get('type')=='record' for s in segments)


def retain(db, scope, identifier, raw):
    if has_voice(raw):
        db.execute('UPDATE pipeline_work_inputs SET voice=? WHERE scope=? AND event=? AND voice IS NULL',
                   (json.dumps({'state': 'pending', 'attempts': 0}), scope, identifier))


def load(db, scope, identifier):
    row = db.execute('SELECT voice FROM pipeline_work_inputs WHERE scope=? AND event=?', (scope, identifier)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def finish(db, scope, identifier, owner, state, text=None, reason=None):
    # A late response cannot mutate a prepared/committed input or retired raw.
    row = db.execute('SELECT voice,payload,seen FROM pipeline_work_inputs WHERE scope=? AND event=?', (scope, identifier)).fetchone()
    if not row or not row[0] or row[1] is None or row[2]:
        return False
    value = json.loads(row[0])
    if value.get('owner') != owner or value.get('finished'):
        return False
    value.update(state=state, finished=True)
    if text is not None:
        value['text'] = text
    if reason is not None:
        value['reason'] = reason
    with db:
        db.execute('UPDATE pipeline_work_inputs SET voice=? WHERE scope=? AND event=?',
                   (json.dumps(value, ensure_ascii=False), scope, identifier))
    return True


def render(value):
    if value['state'] == 'success':
        return '[QQ语音] ' + value['text']
    return '[QQ语音：识别失败]' if value['state'] == 'failed' else '[QQ语音：转写结果未知]'


async def prepare(st, raw, call, current=lambda: True):
    if not has_voice(raw):return raw
    from bot_pipeline import protocol_input_scope
    key=(protocol_input_scope(raw),str(raw['message_id']))
    running=getattr(st,'_voice_inflight',None)
    if running is None:running=st._voice_inflight={}
    task=running.get(key)
    if task is None:
        task=asyncio.create_task(_prepare(st,raw,call,current));running[key]=task
        def release(done):
            if running.get(key) is done:running.pop(key,None)
        task.add_done_callback(release)
    return await asyncio.shield(task)


def result_unavailable(error):
    # NapCat may acknowledge translation before its subsequent message read
    # contains text. Retry only this explicit response, never an unknown send.
    result = getattr(error, 'result', None)
    return (isinstance(result, dict) and result.get('status') == 'failed'
            and any(isinstance(result.get(k), str) and result[k].removeprefix('Error: ') == '获取语音转文字结果失败'
                    for k in ('message', 'wording')))


async def transcribe(st, scope, identifier, owner, call, current):
    try:
        return await call('fetch_ptt_text', message_id=identifier)
    except Exception as error:
        if not result_unavailable(error):raise
        await asyncio.sleep(RESULT_RETRY_DELAY)
        if not current() or getattr(st, '_storage_closed', False):
            raise asyncio.CancelledError('voice_binding_changed')
        value = load(st.db, scope, identifier)
        if value.get('owner') != owner or value.get('finished'):
            raise asyncio.CancelledError('voice_owner_changed')
        value.update(attempts=2, reason='qq_result_unavailable')
        with st.db:
            st.db.execute('UPDATE pipeline_work_inputs SET voice=? WHERE scope=? AND event=?',
                          (json.dumps(value), scope, identifier))
        return await call('fetch_ptt_text', message_id=identifier)


async def _prepare(st, raw, call, current):
    """One bounded attempt; interrupted attempts recover as unknown, not replay."""
    if not has_voice(raw):
        return raw
    from bot_pipeline import protocol_input_scope
    scope = protocol_input_scope(raw)
    identifier = str(raw['message_id'])
    value = load(st.db, scope, identifier)
    if value is None:
        raise ValueError('voice_original_input_missing')
    if not value.get('finished'):
        if value.get('attempts'):
            # The prior process may have dispatched QQ recognition. Retrying
            # blindly would be a second paid/remote operation, not recovery.
            finish(st.db, scope, identifier, value.get('owner'), 'unknown')
        else:
            owner = str(uuid.uuid4())
            value.update(owner=owner, attempts=1, started=time.time())
            with st.db:
                st.db.execute('UPDATE pipeline_work_inputs SET voice=? WHERE scope=? AND event=?',
                              (json.dumps(value), scope, identifier))
            task = asyncio.create_task(transcribe(st, scope, identifier, owner, call, current))
            try:
                try:
                    result = await asyncio.wait_for(asyncio.shield(task), SOFT_TIMEOUT)
                except asyncio.TimeoutError:
                    value = load(st.db, scope, identifier)
                    value['state'] = 'unknown'
                    with st.db:
                        st.db.execute('UPDATE pipeline_work_inputs SET voice=? WHERE scope=? AND event=?',
                                      (json.dumps(value), scope, identifier))
                    result = await asyncio.wait_for(asyncio.shield(task), HARD_TIMEOUT - SOFT_TIMEOUT)
                if not current():
                    raise asyncio.CancelledError('voice_binding_changed')
                text = result.get('text') if isinstance(result, dict) else None
                if not isinstance(text, str) or not text.strip():
                    finish(st.db, scope, identifier, owner, 'failed', reason='empty_result')
                else:
                    finish(st.db, scope, identifier, owner, 'success', text.strip())
            except asyncio.CancelledError:
                if not getattr(st,'_storage_closed',False):finish(st.db, scope, identifier, owner, 'unknown')
                raise
            except Exception as error:
                # NapCat's PromiseTimer is also indeterminate, even when its
                # translated error contains the word "failed".
                uncertain = isinstance(error, (TimeoutError, ConnectionError, OSError)) or 'PromiseTimer' in str(error) or 'timeout' in str(error).lower()
                reason = 'timeout_or_transport' if uncertain else ('qq_result_unavailable' if result_unavailable(error) else type(error).__name__[:80])
                if not getattr(st,'_storage_closed',False):finish(st.db, scope, identifier, owner, 'unknown' if uncertain else 'failed', reason=reason)
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                st.slot_changed.set()
        value = load(st.db, scope, identifier)
    derived = copy.deepcopy(raw)
    derived['message'] = []
    rendered=False
    for segment in raw['message']:
        if segment.get('type')=='record':
            derived['message'].append({'type':'text','data':{'text':render(value) if not rendered else '[QQ语音：其他片段未转写]'}})
            rendered=True
        else:derived['message'].append(segment)
    return derived


async def ready(st, current=lambda: True):
    """Input completeness before the existing Writer, across this Bot's scopes.

    Conversion can finish before the SDK observes its event. Wait for that
    existing staging row too, so later text cannot win the enqueue race.
    """
    task=asyncio.current_task()
    task._hosted_voice_waiting=True
    try:
        await _ready(st,current)
    finally:
        task._hosted_voice_waiting=False


async def _ready(st,current):
    while current():
        st.slot_changed.clear()
        row = st.db.execute("""SELECT 1 FROM pipeline_work_inputs i
          WHERE i.voice IS NOT NULL AND i.seen=0 AND i.payload IS NOT NULL
          AND i.status IN ('pending','queued','owned','deferred') AND
          (coalesce(json_extract(i.voice,'$.finished'),0)=0 OR NOT EXISTS
           (SELECT 1 FROM fishbot_shared_inputs q WHERE q.scope=i.scope AND q.event=i.event)) LIMIT 1""").fetchone()
        if not row:
            return
        await st.slot_changed.wait()
    raise asyncio.CancelledError('voice_input_binding_changed')
