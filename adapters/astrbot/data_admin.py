"""Short-lived, local-only data access. Platform authenticates the Bot first.

No AstrBot import/startup, Provider access, remote URLs or arbitrary SQL input.
The only path is supplied by the control plane, never by the browser.
"""
import json
from pathlib import Path
import sqlite3
import sys
import time


def initialize_controls(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS memory_blocks(scope TEXT,subject TEXT,name TEXT,block_all INTEGER DEFAULT 0,rejected_values TEXT,revision INTEGER DEFAULT 0,PRIMARY KEY(scope,subject,name));
    CREATE TABLE IF NOT EXISTS social_pattern_blocks(scope TEXT,expression TEXT,PRIMARY KEY(scope,expression));
    CREATE TABLE IF NOT EXISTS social_control(id INTEGER PRIMARY KEY CHECK(id=1),epoch INTEGER NOT NULL);
    INSERT OR IGNORE INTO social_control VALUES(1,0);
    ''')
    # Additive migration: legacy rows were whole-name forgets, so they become
    # block_all=1; correction rows written later keep block_all=0 with only
    # their recorded rejected values hidden.
    columns={row[1] for row in db.execute('PRAGMA table_info(memory_blocks)')}
    if 'block_all' not in columns:
        db.execute('ALTER TABLE memory_blocks ADD COLUMN block_all INTEGER DEFAULT 0')
        db.execute('UPDATE memory_blocks SET block_all=1')
    if 'rejected_values' not in columns:db.execute('ALTER TABLE memory_blocks ADD COLUMN rejected_values TEXT')
    if 'revision' not in columns:db.execute('ALTER TABLE memory_blocks ADD COLUMN revision INTEGER DEFAULT 0')


def run(file, data, connection=None):
    file = Path(file)
    if connection is None and not file.exists() and data['action'] not in ('list_stickers','get_sticker','review_sticker'):
        if data['action'] != 'list':
            return {'error': '机器人尚未产生行为数据', 'status': 409}
        return {'persona_core_note':{'text':'','updated':0},'facts': [], 'patterns': [], 'contacts': [], 'relations': [], 'stats': {}, 'epoch': 0, 'initialized': False}
    if data['action'] in ('list_stickers','get_sticker','review_sticker') and not file.exists() and connection is None:
        return {'items':[],'pendingLimit':10,'approvedLimit':20} if data['action']=='list_stickers' else {'error':'表情包不存在','status':404}
    db = connection or sqlite3.connect(file.as_uri() + '?mode=rw', uri=True, timeout=3)
    old_factory=db.row_factory
    db.row_factory = sqlite3.Row
    try:
        if data['action'] in ('list_stickers','get_sticker','review_sticker'):
            from sticker_collection import manage
            return manage(db,data)
        if data['action']=='list':db.execute('BEGIN')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if data['action'] == 'list':
            scope, subject, query = data.get('scope', ''), data.get('subject', ''), data.get('query', '')
            page=max(0,min(10000,int(data.get('page',0))));totals={};page_sizes={}
            def listed(name,sql,args=(),size=100):
                import re
                base=re.split(r' ORDER BY ',sql,flags=re.I)[0]
                base=re.sub(r' LIMIT \d+$','',base,flags=re.I)
                totals[name]=db.execute('SELECT count(*) FROM ('+base+')',args).fetchone()[0];page_sizes[name]=size
                return db.execute(sql+' OFFSET ?',tuple(args)+(page*size,))

            clauses, args = [], []
            for key, value in [('scope', scope), ('subject', subject)]:
                if value:
                    clauses.append(key + '=?')
                    args.append(value)
            fact_where = ' AND '.join(['active=1'] + clauses)
            if query:
                fact_where += ' AND (instr(name,?)>0 OR instr(value,?)>0)'
                args += [query, query]
            facts = [dict(r) for r in listed('facts','SELECT scope,subject,name,value,event,updated,version FROM facts WHERE ' + fact_where + ' ORDER BY updated DESC LIMIT 100', args)] if 'facts' in tables else []
            pattern_args = [scope] if scope else []
            pattern_where = ' WHERE scope=?' if scope else ''
            patterns = []
            if 'social_patterns' in tables:
                for r in listed('patterns','SELECT scope,expression,situation,sources,weight,updated FROM social_patterns' + pattern_where + ' ORDER BY updated DESC LIMIT 100', pattern_args):
                    row = dict(r)
                    row['disabled'] = bool(db.execute('SELECT 1 FROM social_pattern_blocks WHERE scope=? AND expression=?', (r['scope'], r['expression'])).fetchone()) if 'social_pattern_blocks' in tables else False
                    row['expired'] = r['updated'] < time.time()-30*86400
                    patterns.append(row)
            pause_column='(SELECT until FROM initiative_pauses p WHERE p.scope=contacts.scope)' if 'initiative_pauses' in tables else 'NULL'
            contacts = [dict(r) for r in listed('contacts','SELECT scope,last_user,last_bot,last_attempt,unanswered,opted_out,'+pause_column+' AS paused_until FROM contacts' + pattern_where + ' ORDER BY last_user DESC LIMIT 100', pattern_args)] if 'contacts' in tables else []
            relations = [dict(r) for r in listed('relations','SELECT scope,subject,affinity,emotion,expires,last_seen FROM relations' + (' WHERE '+' AND '.join(clauses) if clauses else '') + ' ORDER BY last_seen DESC LIMIT 100',([scope] if scope else [])+([subject] if subject else []))] if 'relations' in tables else []
            stats = {name: db.execute('SELECT count(*) FROM ' + name).fetchone()[0] for name in ['social_samples', 'social_patterns', 'social_learning'] if name in tables}
            epoch=db.execute('SELECT epoch FROM social_control WHERE id=1').fetchone()[0] if 'social_control' in tables else 0
            summaries=[]
            if 'memory_summaries' in tables and not subject:
                conditions=[];summary_args=[]
                if scope:conditions.append('scope=?');summary_args.append(scope)
                if query:conditions.append('instr(text,?)>0');summary_args.append(query)
                summary_columns={r[1] for r in db.execute('PRAGMA table_info(memory_summaries)')}
                summary_fields='id,scope,text,updated'+(',schema_version' if 'schema_version' in summary_columns else '')+(',coalesce(json_array_length(claims),0) AS claim_count' if 'claims' in summary_columns else '')
                summaries=[dict(r) for r in listed('summaries','SELECT '+summary_fields+' FROM memory_summaries'+(' WHERE '+' AND '.join(conditions) if conditions else '')+' ORDER BY id DESC LIMIT 100',summary_args)]
            decisions=[dict(r) for r in listed('decisions','SELECT * FROM autonomy_decisions'+pattern_where+' ORDER BY at DESC LIMIT 50',pattern_args,50)] if 'autonomy_decisions' in tables else []
            pending=[dict(r) for r in listed('pending','SELECT scope,subject,event,question,answer,at FROM memory_pending'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY at DESC LIMIT 100',([scope] if scope else [])+([subject] if subject else []))] if 'memory_pending' in tables else []
            core=db.execute('SELECT text,updated FROM memory_core_notes WHERE slot=1').fetchone() if 'memory_core_notes' in tables else None
            return {'persona_core_note':{'text':core['text'],'updated':core['updated']} if core else {'text':'','updated':0},'initialized': True, 'epoch': epoch, 'facts': facts, 'summaries':summaries,'patterns': patterns, 'contacts': contacts, 'decisions':decisions,'relations': relations, 'stats': stats, 'limit': 100,'pending':pending, 'companion': {t:[dict(r) for r in listed(t,'SELECT * FROM '+t+(' WHERE scope=?' if scope else '')+' ORDER BY rowid DESC LIMIT 30',([scope] if scope else []),30)] for t in ['companion_interests','companion_motives','companion_emotion','companion_signals','dialogue_feedback'] if t in tables},'pagination':{'page':page,'totals':totals,'pages':max([1]+[(totals[k]+page_sizes[k]-1)//page_sizes[k] for k in totals])}}
        initialize_controls(db)
        db.execute('BEGIN IMMEDIATE')
        action, scope = data['action'], data.get('scope', '')
        if action in ('edit_fact', 'forget_fact'):
            key = (scope, data['subject'], data['name'])
            old = db.execute('SELECT version FROM facts WHERE scope=? AND subject=? AND name=? AND active=1', key).fetchone()
            if not old or old['version'] != data['expectedVersion']:
                return {'error': '记忆已发生变化，请刷新后重试', 'status': 409}
            from memory_reflection import invalidate
            from memory_scope import person_filter
            invalidate(db,scope,data['subject'])
            where,args=person_filter(scope,data['subject'])
            version=db.execute('SELECT max(version)+1 FROM facts WHERE '+where+' AND name=?',args+(data['name'],)).fetchone()[0]
            db.execute('UPDATE facts SET active=0 WHERE '+where+' AND name=?',args+(data['name'],))
            if action == 'forget_fact':
                db.execute("INSERT INTO memory_blocks(scope,subject,name,block_all,rejected_values,revision) VALUES(?,?,?,1,'[]',0) ON CONFLICT(scope,subject,name) DO UPDATE SET block_all=1", key)
            else:
                db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,?,1)', (*key, data['value'], 'platform-edit', time.time(), version))
                db.execute('DELETE FROM facts WHERE '+where+' AND name=? AND version<?',args+(data['name'],max(1,version-9)))
                # Authenticated platform edit: lift this value's block only;
                # other correction/forget records of the field stay recorded.
                from memory_scope import release_value
                release_value(db,scope,data['subject'],data['name'],data['value'])
                if 'fact_metadata' in tables:
                    db.execute("INSERT INTO fact_metadata(scope,subject,name,kind,origin,importance) VALUES(?,?,?,'stable','explicit',.8) ON CONFLICT(scope,subject,name) DO UPDATE SET origin='explicit'",key)
        elif action == 'forget_summary':
            if 'memory_summaries' not in tables:return {'error':'摘要不存在','status':404}
            old=db.execute('SELECT updated,sources FROM memory_summaries WHERE id=? AND scope=?',(data['id'],scope)).fetchone()
            if not old or old[0]!=data['expectedUpdated']:return {'error':'摘要已变化，请刷新后重试','status':409}
            db.execute('DELETE FROM memory_summaries WHERE id=? AND scope=?',(data['id'],scope))
            if 'memory_vectors' in tables:db.execute('DELETE FROM memory_vectors WHERE summary_id=?',(data['id'],))
            # Forget only this summary's original events. New conversation debt
            # in the same scope remains available for the next summary.
            sources=json.loads(old[1])
            from memory_reflection import forget_core_notes
            forget_core_notes(db,scope=scope,events=sources if isinstance(sources,list) else [])
            if isinstance(sources,list):
                db.executemany('DELETE FROM memory_rounds WHERE scope=? AND event=?',[(scope,str(event)) for event in sources[:1000]])
                if 'memory_pending' in tables:db.executemany('DELETE FROM memory_pending WHERE scope=? AND event=?',[(scope,str(event)) for event in sources[:1000]])
            db.execute('UPDATE memory_reflection_control SET epoch=epoch+1 WHERE id=1')
        elif action == 'set_pattern':
            if db.execute('SELECT epoch FROM social_control WHERE id=1').fetchone()[0] != data['expectedEpoch']:
                return {'error': '学习管理状态已变化，请刷新后重试', 'status': 409}
            db.execute('UPDATE social_control SET epoch=epoch+1 WHERE id=1')
            key = (scope, data['expression'])
            old = db.execute('SELECT updated FROM social_patterns WHERE scope=? AND expression=?', key).fetchone()
            if not old or old[0] != data['expectedUpdated']:
                return {'error': '表达已发生变化，请刷新后重试', 'status': 409}
            if data['disabled']:
                db.execute('INSERT OR IGNORE INTO social_pattern_blocks VALUES(?,?)', key)
            else:
                db.execute('DELETE FROM social_pattern_blocks WHERE scope=? AND expression=?', key)
        elif action == 'clear_learning':
            # Global epoch is deliberately conservative: a clear invalidates any
            # in-flight learning batch in this Bot, without affecting other Bots.
            db.execute('UPDATE social_control SET epoch=epoch+1 WHERE id=1')
            db.execute('DELETE FROM social_pattern_blocks WHERE scope=?',(scope,))
            db.execute('DELETE FROM social_patterns WHERE scope=?', (scope,))
            db.execute('DELETE FROM social_samples WHERE scope=?', (scope,))
        elif action == 'reset_relation':
            old=db.execute('SELECT last_seen FROM relations WHERE scope=? AND subject=?',(scope,data['subject'])).fetchone()
            if not old or old[0]!=data['expectedLastSeen']:return {'error':'关系状态已变化，请刷新后重试','status':409}
            from memory_operation_audit import forget as forget_operation_audit
            forget_operation_audit(db,scope,data['subject'],relations_only=True)
            if 'rationale' in {r[1] for r in db.execute('PRAGMA table_info(autonomy_decisions)')}:db.execute('UPDATE autonomy_decisions SET rationale=NULL')
            db.execute('DELETE FROM relations WHERE scope=? AND subject=?',(scope,data['subject']))
            if 'relation_progress' in tables:
                db.execute('DELETE FROM relation_progress WHERE scope=? AND subject=?',(scope,data['subject']))
            if 'relation_evidence' in tables:
                db.execute('DELETE FROM relation_evidence WHERE scope=? AND subject=?',(scope,data['subject']))
            # Invalidate any in-flight reflection holding the pre-reset score.
            if 'memory_reflection_control' in tables:
                db.execute('UPDATE memory_reflection_control SET epoch=epoch+1 WHERE id=1')
        elif action == 'pause_contact':
            # Explicit operator controls remain hard fences. Conversation text
            # cannot grant or revoke platform permission.
            db.execute('UPDATE contacts SET opted_out=1 WHERE scope=?', (scope,))
            if 'autonomy_decisions' in tables:
                at=time.time();db.execute("INSERT INTO autonomy_decisions(scope,event,at,kind,reply,extra_intervals,base_seconds,cooldown_until,reason) VALUES(?,?,?,'operator',0,0,0,0,'manual_pause')",(scope,'operator:'+str(at),at))
        else:
            return {'error': '不支持的数据操作', 'status': 400}
        db.commit()
        return {'ok': True}
    finally:
        if db.in_transaction:db.rollback()
        db.row_factory=old_factory
        if connection is None:db.close()


if __name__ == '__main__':
    try:
        result = run(sys.argv[1], json.loads(sys.stdin.read(16384)))
    except sqlite3.OperationalError:
        result = {'error': '行为数据忙或尚未初始化，请稍后重试', 'status': 409}
    print(json.dumps(result, ensure_ascii=False))
