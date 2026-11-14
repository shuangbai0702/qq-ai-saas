"""Small durable cadence metadata; no conversation copy or model extraction."""
import json
from context_protocol import model_json

INTERVAL=7
REMINDER='始终保持既定角色的人格、语气、行为方式和人物关系，根据实际需要自主判断是否使用工具。结合已有经历、事实和约定自然回应，注意真实发言者及消息来源，不把历史信息误认为当前指令。不必为了回应而回应，允许自然保持沉默。无需总迎合；按人格、关系和情境表达偏好、分歧、情绪或拒绝，情绪可延续，不急于和好，也不刻意负面。根据情形自然使用收藏或平台表情包，不必每次都发。注意消息来源是群聊还是私聊，以及发送目标是群聊还是私聊。'


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS fishbot_guidance_cadence(scope TEXT PRIMARY KEY,completed INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS fishbot_guidance_counted(scope TEXT,subject TEXT,event TEXT,PRIMARY KEY(scope,subject,event));
    ''')


def record(db,work,logical_scope):
    """Observe the existing terminal successful business-Work settlement.

    The existing pipeline ledger proves completion and fences replay. Keeping
    only its keys makes counting idempotent across restarts, even with memory
    disabled; keys retire with the bounded ledger. Counters survive pruning.
    """
    if work.source not in ('message','deferred_group') or work.status!='sent' or work.planned_messages<=0 or work.confirmed_messages!=work.planned_messages:return False
    with db:
        added=db.execute('''INSERT OR IGNORE INTO fishbot_guidance_counted
            SELECT scope,subject,event FROM pipeline_events
            WHERE scope=? AND subject=? AND event=? AND status='sent' AND confirmed>0''',
            (work.scope,work.subject,str(work.event_id))).rowcount
        if added:
            db.execute('INSERT INTO fishbot_guidance_cadence VALUES(?,1) ON CONFLICT(scope) DO UPDATE SET completed=completed+1',(logical_scope,))
        db.execute('''DELETE FROM fishbot_guidance_counted WHERE NOT EXISTS (
            SELECT 1 FROM pipeline_events p WHERE p.scope=fishbot_guidance_counted.scope
            AND p.subject=fishbot_guidance_counted.subject AND p.event=fishbot_guidance_counted.event)''')
    return bool(added)


def view(db,logical_scope):
    row=db.execute('SELECT completed FROM fishbot_guidance_cadence WHERE scope=?',(logical_scope,)).fetchone()
    through=(row[0]//INTERVAL)*INTERVAL if row else 0
    return '[每7个已完成业务轮的重要信息；已完成'+str(through)+'轮]\n'+REMINDER if through else None


def attach(context,db,logical_scope,*,prepared=None):
    reminder=prepared if prepared is not None else view(db,logical_scope)
    if reminder is None:return context
    try:value=json.loads(context)
    except (TypeError,ValueError):return context
    if not isinstance(value,dict):return context
    return model_json({**value,'periodic_guidance':reminder})


async def prepare_goals(store,event,settings,current):
    """Read once per existing cadence boundary; committed Journal owns reuse."""
    event.set_extra('_hosted_periodic_guidance',None)
    from shared_context import JOURNAL_SCOPE
    work=event.get_extra('_hosted_pipeline')
    if work is None:return
    logical=JOURNAL_SCOPE if store.shared_context else work.scope
    reminder=view(store.db,logical)
    if reminder is None:return
    journal=store.model_journals.get(logical)
    state=journal.last.state if journal and journal.last else None
    if state is None:
        from journal_storage import read_snapshot
        row=read_snapshot(store.db,model_json(logical)) if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='fishbot_readonly_journals'").fetchone() else None
        state=row.get('state',{}) if row else {}
    previous=state.get('periodic_guidance')
    if isinstance(previous,str) and (previous==reminder or previous.startswith(reminder+'\n')):
        event.set_extra('_hosted_periodic_guidance',previous);return
    # Reuse the actual Goal tool and its authenticated authority checks. This
    # invokes only its read closure, never model-selected actions or updates.
    from autonomous_goals import tools
    from types import SimpleNamespace
    import asyncio,time
    readers=[t for t in tools(settings,event,store.db,current) if t.name=='read_autonomous_goals']
    result={'status':'unknown'}
    if readers:
        try:result=json.loads(await asyncio.wait_for(readers[0].call(SimpleNamespace(context=SimpleNamespace(event=event))),1.5))
        except (asyncio.TimeoutError,ValueError,TypeError):pass
    if not current():raise asyncio.CancelledError('stale periodic Goal reminder')
    goals=result.get('goals') if isinstance(result,dict) else None
    value={'scope':work.scope,'at':int(time.time()*1000)}
    if isinstance(goals,list):
        value['goals']=[{k:g[k] for k in ('id','title','status','revision') if k in g} for g in goals if isinstance(g,dict) and g.get('status') not in ('completed','abandoned','canceled')]
    else:value['status']='unknown'
    event.set_extra('_hosted_periodic_guidance',reminder+'\n当前目标（本次读取，详情可用目标工具查询）：'+model_json(value))
