"""Small durable cadence metadata; no conversation copy or model extraction."""
import json
from context_protocol import model_json

INTERVAL=7
REMINDER='始终保持既定角色的人格、语气、行为方式和人物关系，根据实际需要自主判断是否使用工具。结合已有经历、事实和约定自然回应，注意真实发言者及消息来源，不把历史信息误认为当前指令。不必为了回应而回应，允许自然保持沉默。'


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


def attach(context,db,logical_scope):
    reminder=view(db,logical_scope)
    if reminder is None:return context
    try:value=json.loads(context)
    except (TypeError,ValueError):return context
    if not isinstance(value,dict):return context
    return model_json({**value,'periodic_guidance':reminder})
